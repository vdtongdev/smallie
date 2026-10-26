"""Team mode, a round table (design: docs/superpowers/specs/2026-10-23-agent-team-design.md):

    pick -+- nobody mentioned -> the orchestrator answers as in normal mode -> END
          +- speak x N (parallel, one `Send` each, own sources only) -> judge -+- agreed / explained -> synthesize -> END
                                                                              +- question -> pause -> judge
                                                                              +- fixable conflicts, round < cap -> speak x involved -> judge

A `team` node of the assistant graph. Only the final answer joins the thread; the transcript goes
on its `additional_kwargs["team"]`. Progress goes out as `custom` stream events: `round`, `board`,
`synthesize`, `needs_human`, and `speaker` (internal: which `speak` task runs which agent).
"""

import asyncio
import dataclasses
import logging
from collections.abc import Mapping, Sequence
from typing import Annotated

import openai
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import merge_configs
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, Send, interrupt
from pydantic import BaseModel

from app.agents.artifacts import KINDS, collect
from app.agents.base import MEMORY_TOOL_NAMES, TOOL_RESULT_PREVIEW, AgentSpec, RouteInfo, ToolRecorder, turn_messages
from app.core.config import get_settings
from app.core.llm import get_chat_model
from app.core.memory import TEAM_MODE, AgentContext
from app.core.prompts import reply_language
from app.orchestration.prompts import BOARD_PROMPT, CONFLICT_MESSAGE, ROUND_MESSAGE, TEAM_MESSAGE
from app.orchestration.router import ORCHESTRATOR, AssistantState, available_agents

log = logging.getLogger(__name__)

TEAM = TEAM_MODE
RESET = {"reset": True}  # first entry of `speeches` at the start of a message: forget the earlier message's


class Side(BaseModel):
    route: str
    value: str
    source: str
    as_of: str


class Difference(BaseModel):
    routes: list[str]
    explanation: str
    sides: list[Side] = []


class Conflict(BaseModel):
    routes: list[str]
    claim: str
    fixable_by: list[str] = []
    issue: str = ""


class Board(BaseModel):
    agreements: list[str] = []
    differences: list[Difference] = []
    conflicts: list[Conflict] = []
    question: str = ""


def participants(available: list[tuple[AgentSpec, RouteInfo]], message: str) -> list[AgentSpec]:
    """Who takes part: agents the message points to (one that another mentioned agent `overrides`
    is dropped unless its own `hints` match), plus the `joins == "with_others"` agents when any
    other takes part. Registry order."""
    mentioned = [spec for spec, info in available if spec.is_mentioned(message, info)]
    chosen = [spec for spec in mentioned
              if (spec.hints and spec.hints.search(message))
              or not any(spec.route in other.overrides for other in mentioned)]
    return [spec for spec, _ in available if spec in chosen
            or (chosen and spec.joins == "with_others" and any(c.route != spec.route for c in chosen))]


def clean(board: Board, speakers: Sequence[str]) -> Board:
    """Make a board safe to route on: routes only of agents that answered, `fixable_by` only of the
    conflict's own routes."""
    def keep(routes: Sequence[str], allowed: Sequence[str]) -> list[str]:
        return [r for r in dict.fromkeys(routes) if r in allowed]

    conflicts = []
    for conflict in board.conflicts:
        routes = keep(conflict.routes, speakers)
        conflicts.append(conflict.model_copy(update={"routes": routes, "fixable_by": keep(conflict.fixable_by, routes)}))
    differences = []
    for d in board.differences:
        routes = keep(d.routes, speakers)
        if d.sides and all(side.source.strip() and side.as_of.strip() for side in d.sides):
            differences.append(d.model_copy(update={"routes": routes}))
        else:  # explained only when every side gives a source and a date
            claim = "; ".join(f"{side.route}: {side.value}" for side in d.sides) or d.explanation
            conflicts.append(Conflict(routes=routes, claim=claim, issue="a side has no source or date"))
    return board.model_copy(update={"conflicts": conflicts, "differences": differences})


async def ask_board(messages: list[AnyMessage], answers: list[dict], config: RunnableConfig) -> Board:
    """One model call; `config` carries the usage counter's callbacks."""
    settings = get_settings()
    question = next((m.text for m in reversed(messages) if isinstance(m, HumanMessage)), "")
    # Texts go into the message, not a template: answers may contain braces.
    human = f"USER MESSAGE:\n{question}\n\nANSWERS:\n" + "\n\n".join(f"{a['agent']}:\n{a['text']}" for a in answers)
    judge = settings.openrouter_judge_model
    # A chosen judge may be a closed model, which the quantization filter would leave without a provider.
    board = get_chat_model(judge or settings.openrouter_orchestrator_model or None,
                           **({"exact_model": True} if judge else {})).with_structured_output(
        Board, method="function_calling")
    system = BOARD_PROMPT.format(language=reply_language(messages))
    return await board.ainvoke([SystemMessage(system), HumanMessage(human)], config=config)


def merge_speeches(old: list[dict] | None, new: list[dict]) -> list[dict]:
    """Reducer: parallel `speak` tasks each add one entry; `RESET` first starts a new list."""
    if new and new[0] == RESET:
        return new[1:]
    return [*(old or []), *new]


class TeamState(AssistantState):
    speeches: Annotated[list[dict], merge_speeches]  # {round, kind, agent, text, tools, artifacts, failed}


def _merge(artifacts: Sequence[dict]) -> dict:
    """Tables and charts of several runs, once per id."""
    merged = {kind: list({item["id"]: item for a in artifacts for item in a.get(kind, [])}.values()) for kind in KINDS}
    return {kind: items for kind, items in merged.items() if items}


def _tools(messages: Sequence[AnyMessage]) -> list[dict]:
    results = {m.tool_call_id: m for m in messages if isinstance(m, ToolMessage)}
    return [{"name": c["name"], "args": c["args"],
             "result": (r.text or str(r.content))[:TOOL_RESULT_PREVIEW] if (r := results.get(c["id"])) else ""}
            for m in messages if isinstance(m, AIMessage) for c in m.tool_calls if c["name"] not in MEMORY_TOOL_NAMES]


def _rounds(speeches: Sequence[dict]) -> list[dict]:
    rounds: dict[int, dict] = {}
    for s in speeches:
        entry = rounds.setdefault(s["round"], {"round": s["round"], "kind": s["kind"], "answers": []})
        entry["answers"].append({"agent": s["agent"], "text": s["text"], "tools": s["tools"]})
    return list(rounds.values())


def _latest(speeches: Sequence[dict], routes: Sequence[str]) -> list[dict]:
    """Each listed agent's newest entry, in the order of `routes`."""
    newest = {s["agent"]: s for s in speeches}
    return [newest[r] for r in routes if r in newest]


def _bullets(lines: Sequence[str]) -> str:
    return "\n".join(f"- {line}" for line in lines) or "none"


def build_team(specs: Sequence[AgentSpec], agents: Mapping, orchestrator):
    """The `team` subgraph. `agents`: route -> compiled graph or RemoteAgent; `orchestrator`: the
    orchestrator node function (it reads `team_note` as a hidden message)."""
    by_route = {spec.route: spec for spec in specs}

    def emit(event: dict) -> None:
        get_stream_writer()(event)

    def sends(routes: Sequence[str], rnd: int, kind: str, notes: Mapping[str, str], messages) -> list[Send]:
        emit({"type": "round", "round": rnd, "max_rounds": get_settings().team_max_rounds + 1,  # total: round 1 plus the conflict rounds allowed
              "kind": kind, "agents": list(routes)})
        return [Send("speak", {"route": r, "round": rnd, "kind": kind, "note": notes[r], "messages": messages})
                for r in routes]

    async def ask_orchestrator(state: AssistantState, runtime: Runtime[AgentContext], note: str = ""):
        """The orchestrator node's Command for this thread (`note` as its hidden team message)."""
        return await orchestrator({**state, "team_note": note} if note else state, runtime)

    async def pick(state: TeamState, runtime: Runtime[AgentContext], config: RunnableConfig) -> Command:
        messages = state["messages"]
        available = await available_agents(runtime.context.user_id, specs)
        chosen = [spec.route for spec in participants(available, messages[-1].text)]
        if not chosen:
            command = await ask_orchestrator(state, runtime)
            if command.goto not in by_route:
                return Command(goto=END, update=command.update)
            # It handed off: that agent answers on the thread, as in normal mode.
            spec = by_route[command.goto]
            result = await agents[spec.route].ainvoke({"messages": messages}, config, context=runtime.context)
            turn = turn_messages(result.get("messages", []))
            answer = AIMessage(spec.result(result), name=spec.message_names[0], additional_kwargs=collect(turn))
            return Command(goto=END, update={"messages": [answer]})
        note = ROUND_MESSAGE.format(language=reply_language(messages))
        team = {"participants": chosen, "round": 1, "rounds": [], "boards": [], "forced": False, "artifacts": {},
                "question": ""}  # a fresh debate: nothing of an earlier message's carries over
        return Command(update={"team": team, "speeches": [RESET]},
                       goto=sends(chosen, 1, "answer", {r: note for r in chosen}, messages))

    async def speak(job, runtime: Runtime[AgentContext], config: RunnableConfig) -> dict:
        route, spec = job["route"], by_route[job["route"]]
        emit({"type": "speaker", "agent": route, "ns": config["configurable"]["checkpoint_ns"]})
        context = dataclasses.replace(runtime.context, sources=tuple(sorted(spec.sources)))
        messages = [*job["messages"], HumanMessage(job["note"], name=TEAM)]
        entry = {"round": job["round"], "kind": job["kind"], "agent": route, "tools": [], "artifacts": {}, "failed": False}
        recorder = ToolRecorder()
        try:
            result = await agents[route].ainvoke({"messages": messages}, merge_configs(config, {"callbacks": [recorder]}),
                                                 context=context)
        except (openai.RateLimitError, openai.AuthenticationError):
            raise
        except Exception as exc:  # noqa: BLE001 - the user still gets an answer from the others
            log.exception("Team step by %s failed", route)
            return {"speeches": [{**entry, "text": f"failed: {type(exc).__name__}", "failed": True}]}
        turn = turn_messages(result.get("messages", []))
        # A server reports every tool call, a workflow's inner ones too; in-process the recorder sees them;
        # the message scan is what agents without callbacks leave.
        tools = result["tools"] if "tools" in result else recorder.tools or _tools(turn)
        entry = {**entry, "text": spec.result(result), "tools": tools, "artifacts": collect(turn)}
        emit({"type": "spoke", **{k: entry[k] for k in ("agent", "round", "text", "tools")}})
        return {"speeches": [entry]}

    async def judge(state: TeamState, runtime: Runtime[AgentContext], config: RunnableConfig) -> Command:
        team, speeches, settings = state["team"], state["speeches"], get_settings()
        rnd, messages = team["round"], state["messages"]
        answers = _latest(speeches, team["participants"])
        broken = False
        try:
            board = clean(await ask_board(messages, answers, config), [a["agent"] for a in answers])
        except (openai.RateLimitError, openai.AuthenticationError):
            raise
        except Exception:  # noqa: BLE001 - the user still gets a write-up of the answers so far
            log.exception("Team board failed")
            board, broken = Board(), True
        fixable = [c for c in board.conflicts if c.fixable_by]
        failed = broken or team["forced"] or any(s["failed"] for s in speeches if s["round"] == rnd)
        asks = bool(board.question.strip())
        again = bool(fixable) and not asks and not failed and rnd <= settings.team_max_rounds  # round 1 is the answer round; conflict rounds are 2..cap+1
        forced = failed or (bool(fixable) and not asks and not again)
        record = {"round": rnd, **board.model_dump(), "forced": forced}
        emit({"type": "board", **record})
        team = {**team, "rounds": _rounds(speeches), "boards": [*team["boards"], record], "forced": forced,
                "artifacts": _merge([s["artifacts"] for s in speeches])}
        if asks:
            emit({"type": "needs_human", "question": board.question})
            return Command(update={"team": {**team, "question": board.question},
                                   "messages": [AIMessage(board.question, name=ORCHESTRATOR)]}, goto="pause")
        if not again:
            return Command(update={"team": team}, goto="synthesize")
        routes = [r for r in team["participants"] if any(r in c.routes for c in fixable)]
        language, notes = reply_language(messages), {}
        for r in routes:
            mine = [c for c in fixable if r in c.routes]
            others = [o for o in _latest(speeches, team["participants"]) if o["agent"] != r
                      and any(o["agent"] in c.routes for c in mine)]
            notes[r] = CONFLICT_MESSAGE.format(
                conflicts=_bullets([f"{c.claim} ({c.issue})" for c in mine]),
                others="\n\n".join(f"{o['agent']}:\n{o['text']}" for o in others) or "none",
                own=next(s["text"] for s in reversed(speeches) if s["agent"] == r), language=language)
        return Command(update={"team": {**team, "round": rnd + 1}}, goto=sends(routes, rnd + 1, "conflict", notes, messages))

    async def pause(state: TeamState, runtime: Runtime[AgentContext]) -> Command:
        # Nothing before `interrupt`: on resume the node runs again from its start.
        reply = interrupt({"question": state["team"]["question"]})
        team, messages = state["team"], [*state["messages"], HumanMessage(reply)]
        available = await available_agents(runtime.context.user_id, specs)
        new = [s.route for s in participants(available, reply) if s.route not in team["participants"]]
        if not new:
            return Command(update={"messages": messages[-1:]}, goto="judge")
        # The reply names an agent that is not taking part yet: it answers one round before the board runs again.
        rnd = team["round"] + 1
        note = ROUND_MESSAGE.format(language=reply_language(messages))
        team = {**team, "participants": [*team["participants"], *new], "round": rnd}
        return Command(update={"messages": messages[-1:], "team": team},
                       goto=sends(new, rnd, "answer", {r: note for r in new}, messages))

    async def synthesize(state: TeamState, runtime: Runtime[AgentContext]) -> dict:
        team, messages = state["team"], state["messages"]
        answers = _latest(state["speeches"], team["participants"])
        board = Board(**team["boards"][-1])
        transcript = {key: team[key] for key in ("participants", "rounds", "boards", "forced")}
        only = by_route[team["participants"][0]]
        if len(answers) == 1 and len(team["participants"]) == 1 and not board.conflicts and not team["forced"] \
                and only.answers_directly:
            answer = AIMessage(answers[0]["text"], name=only.message_names[0])
        else:
            emit({"type": "synthesize"})  # only a write-up announces itself
            note = TEAM_MESSAGE.format(
                answers="\n\n".join(f"{a['agent']}:\n{a['text']}" for a in answers),
                differences=_bullets([f"{', '.join(d.routes)}: {d.explanation} ({'; '.join(f'{x.route}: {x.value}, {x.source}, {x.as_of}' for x in d.sides)})"
                                   for d in board.differences]),
                conflicts=_bullets([f"{c.claim} ({', '.join(c.routes)}): {c.issue}" for c in board.conflicts]),
                language=reply_language(messages))
            command = await ask_orchestrator(state, runtime, note)
            handed_off = command.goto in by_route  # it handed off: the answers stand
            answer = AIMessage("\n\n".join(a["text"] for a in answers), name=ORCHESTRATOR) if handed_off \
                else command.update["messages"][0]
        kwargs = {**answer.additional_kwargs, **_merge([team["artifacts"], collect([answer])]), "team": transcript}
        return {"messages": [answer.model_copy(update={"additional_kwargs": kwargs})]}

    graph = StateGraph(TeamState, context_schema=AgentContext)
    graph.add_node("pick", pick, destinations=("speak", END))
    graph.add_node("speak", speak)
    graph.add_node("judge", judge, destinations=("speak", "synthesize", "pause"))
    graph.add_node("pause", pause, destinations=("judge", "speak"))
    graph.add_node("synthesize", synthesize)
    graph.add_edge(START, "pick")
    graph.add_edge("speak", "judge")
    graph.add_edge("synthesize", END)
    return graph.compile(name=TEAM)
