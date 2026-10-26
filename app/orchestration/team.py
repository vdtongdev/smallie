"""Team mode, a task graph (design: docs/superpowers/specs/2026-10-26-team-task-graph-design.md):

    plan -+- nobody needed -> the orchestrator answers as in normal mode -> END
          +- dispatch -> speak x N (one `Send` per ready task, own sources only) -> review -+- clean -> dispatch (next wave) / synthesize when nothing is pending
                                                                                            +- question -> pause -> review / dispatch
                                                                                            +- fixable conflicts, rounds left -> speak x involved -> review

A `team` node of the assistant graph. Only the final answer joins the thread; the transcript (the workspace) goes
on its `additional_kwargs["team"]`. Progress goes out as `custom` stream events: `plan`, `wave`, `spoke`, `review`,
`synthesize`, `needs_human`, and `speaker` (internal: which `speak` task runs which agent).
"""

import asyncio
import dataclasses
import inspect
import logging
import uuid
from collections.abc import Mapping, Sequence
from typing import Annotated, Literal

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
from app.agents.registry import AGENTS
from app.core.config import get_settings
from app.core.llm import get_chat_model
from app.core.memory import TEAM_MODE, AgentContext
from app.core.prompts import reply_language
from app.orchestration.prompts import BOARD_PROMPT, CHALLENGE_MESSAGE, CONFLICT_MESSAGE, DRAW_MESSAGE, DRAWING_NOTE, NOTHING_FOUND_MESSAGE, PLAN_PROMPT, ROUND_MESSAGE, TASK_MESSAGE, TEAM_MESSAGE
from app.orchestration.team_plan import Plan, Task, add_follow_ups, dead_inputs, fallback_tasks, ready, validate_plan
from app.orchestration.router import ORCHESTRATOR, AssistantState, available_agents

log = logging.getLogger(__name__)

TEAM = TEAM_MODE
SMALL_TALK_WORDS = 5
NO_SOURCES = ("none",)  # empty sources means unscoped, so the chart task gets a source no tool carries
ANSWER = "answer"  # the id a chart task depends on: the final answer, which exists only after `synthesize`
DRAW_GOAL = "Draw the chart the user asked for from the final answer."
LIVE = ("done", "failed")  # statuses whose text reaches the board and the write-up


class Side(BaseModel):
    route: str
    value: str
    source: str
    as_of: str


Cause = Literal["definition", "date", "source", "error", "unknown"]


class Difference(BaseModel):
    routes: list[str]
    explanation: str
    sides: list[Side] = []
    cause: Cause = "unknown"


class Conflict(BaseModel):
    routes: list[str]
    claim: str
    fixable_by: list[str] = []
    issue: str = ""
    cause: Cause = "unknown"


class Board(BaseModel):
    agreements: list[str] = []
    differences: list[Difference] = []
    conflicts: list[Conflict] = []
    question: str = ""


class Challenge(BaseModel):
    to_task: str  # whose output is questioned
    by_task: str  # who found the problem
    question: str
    evidence: str  # the receiver's own value and source


class Outcome(BaseModel):
    task: str  # a task that answered a challenge in the previous round
    revised: bool


class Review(Board):
    challenges: list[Challenge] = []
    outcomes: list[Outcome] = []
    follow_ups: list[Task] = []  # parsed here; the review node does not act on them yet


def hand_off_key(to_task: str, by_task: str) -> str:
    return f"{to_task}->{by_task}"


def meeting_key(ids: Sequence[str]) -> str:
    return "meet:" + ",".join(sorted(ids))


def is_small_talk(message: str, available: Sequence[tuple[AgentSpec, RouteInfo]] | None = None) -> bool:
    """A short message with no question and no data words: the orchestrator answers it alone."""
    if len(message.split()) > SMALL_TALK_WORDS or "?" in message:
        return False
    return not any(spec.is_mentioned(message, info) or bool(spec.names and spec.names.search(message))
                   for spec, info in available or [(s, RouteInfo()) for s in AGENTS])


def participants(available: list[tuple[AgentSpec, RouteInfo]], message: str) -> list[AgentSpec]:
    """Who takes part. Small talk: nobody. Speakers named in the message (agent or source): those;
    none named: every available speaker. Plus the drawers the message names with their available
    `fed_by` routes, plus the `joins == "with_others"` agents when any other takes part. Registry order."""
    if is_small_talk(message, available):
        return []
    named = [spec for spec, info in available if spec.is_named(message, info)]
    speakers = [spec for spec in named if not spec.draws] or [spec for spec, _ in available if not spec.draws]
    drawers = [spec for spec in named if spec.draws]
    fed = {route for spec in drawers for route in spec.fed_by}
    chosen = [spec for spec, _ in available if spec in speakers or spec in drawers or spec.route in fed]
    return [spec for spec, _ in available if spec in chosen
            or (chosen and spec.joins == "with_others" and any(c.route != spec.route for c in chosen))]


def clean(board: Board, speakers: Sequence[str], tasks: Sequence[str] | None = None) -> Board:
    """Make a board safe to route on: routes only of agents that answered, `fixable_by` only of the
    conflict's own routes. `tasks`: the live task ids; a review's challenges and outcomes name only those."""
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
    update = {"conflicts": conflicts, "differences": differences}
    if isinstance(board, Review) and tasks is not None:
        update["challenges"] = [c for c in board.challenges if c.to_task in tasks and c.by_task in tasks and c.to_task != c.by_task]
        update["outcomes"] = [o for o in board.outcomes if o.task in tasks]
    return board.model_copy(update=update)


async def ask_board(messages: list[AnyMessage], answers: list[dict], config: RunnableConfig, *,
                    inputs: Mapping[str, list[str]] | None = None, agents: Sequence[AgentSpec] | None = None) -> Review:
    """One model call; `config` carries the usage counter's callbacks. `inputs`: task id -> the task ids it received; `agents`: the available agents, which follow-ups may use."""
    settings = get_settings()
    question = next((m.text for m in reversed(messages) if isinstance(m, HumanMessage)), "")
    # Texts go into the message, not a template: answers may contain braces.
    def head(a: dict) -> str:
        line = f"{a.get('task', a['agent'])} ({a['agent']})"
        if received := (inputs or {}).get(a.get("task", "")):
            line += f" received: {', '.join(received)}"
        if a.get("challenge"):
            line += f"\nPREVIOUSLY CHALLENGED, report its outcome: {a['challenge']}"
        return line

    human = f"USER MESSAGE:\n{question}\n\nANSWERS:\n" + "\n\n".join(f"{head(a)}:\n{a['text']}" for a in answers)
    judge = settings.openrouter_judge_model
    # A chosen judge may be a closed model, which the quantization filter would leave without a provider.
    board = get_chat_model(judge or settings.openrouter_orchestrator_model or None,
                           **({"exact_model": True} if judge else {})).with_structured_output(
        Review, method="function_calling")
    if agents:  # texts go into the message, not a template
        human += "\n\nAVAILABLE AGENTS:\n" + "\n".join(
            f"- {a.route}: {a.description} Reads: {', '.join(sorted(a.sources)) or 'nothing'}." for a in agents)
    system = BOARD_PROMPT.format(language=reply_language(messages))
    return await board.ainvoke([SystemMessage(system), HumanMessage(human)], config=config)


async def ask_plan(messages: list[AnyMessage], available: Sequence[tuple[AgentSpec, RouteInfo]], config: RunnableConfig) -> Plan:
    """The one plan call per message, on the orchestrator model; `config` carries the usage counter's callbacks."""
    settings = get_settings()
    question = next((m.text for m in reversed(messages) if isinstance(m, HumanMessage)), "")
    lines = []
    for spec, info in available:
        line = f"- {spec.route}: {spec.description}"
        if spec.review:
            line += f" Can review: {spec.review}."
        if spec.sources:
            line += f" Reads: {', '.join(sorted(spec.sources))}."
        if spec.draws:
            line += " Draws charts."
        if info.prompt_note:
            line += f"\n  {info.prompt_note}"
        lines.append(line)
    # Texts go into the message, not a template: they may contain braces.
    human = f"USER MESSAGE:\n{question}\n\nAGENTS:\n" + "\n".join(lines)
    planner = get_chat_model(settings.openrouter_orchestrator_model or None).with_structured_output(
        Plan, method="function_calling")
    system = PLAN_PROMPT.format(language=reply_language(messages), max_tasks=settings.team_max_tasks)
    return await planner.ainvoke([SystemMessage(system), HumanMessage(human)], config=config)


def merge_workspace(old: dict | None, new: dict) -> dict:
    """Reducer: parallel `speak` tasks each add their result and status; `RESET` (a `reset` key) starts a new workspace."""
    if new.get("reset"):
        return {k: v for k, v in new.items() if k != "reset"}
    workspace = dict(old or {})
    for key, value in new.items():
        if key == "results":
            workspace["results"] = {**workspace.get("results", {}), **value}
        elif key == "history":
            workspace["history"] = [*workspace.get("history", []), *value]
        elif key == "status":  # task id -> status
            workspace["tasks"] = [{**t, "status": value.get(t["id"], t["status"])} for t in workspace["tasks"]]
        else:
            workspace[key] = value
    return workspace


class TeamState(AssistantState):
    # tasks, results (task id -> {agent, text, tools, artifacts, round}), history, rounds_at, synthesis, question, wave
    workspace: Annotated[dict, merge_workspace]


def _merge(artifacts: Sequence[dict]) -> dict:
    """Tables and charts of several runs, once per id."""
    merged = {kind: list({item["id"]: item for a in artifacts for item in a.get(kind, [])}.values()) for kind in KINDS}
    return {kind: items for kind, items in merged.items() if items}


def _tools(messages: Sequence[AnyMessage]) -> list[dict]:
    results = {m.tool_call_id: m for m in messages if isinstance(m, ToolMessage)}
    return [{"name": c["name"], "args": c["args"],
             "result": (r.text or str(r.content))[:TOOL_RESULT_PREVIEW] if (r := results.get(c["id"])) else ""}
            for m in messages if isinstance(m, AIMessage) for c in m.tool_calls if c["name"] not in MEMORY_TOOL_NAMES]


def _answers(workspace: dict, tasks: Sequence[dict], skip: Sequence[str] = ()) -> list[dict]:
    """The board's and the write-up's view: the newest result of each task that gave one (a failed task's note counts,
    it forces the write-up). `skip`: agents whose results are not answers (the chart agent's)."""
    return [{"agent": t["agent"], "task": t["id"], "text": workspace["results"][t["id"]]["text"]}
            for t in tasks if t["status"] in LIVE and t["agent"] not in skip]


def _inputs(task: dict, workspace: dict) -> tuple[str, str]:
    """The finished tasks this one depends on, and the ones that produced nothing, as prompt text."""
    lines = []
    dead, _ = dead_inputs(task, workspace["tasks"])
    for d in task["depends_on"]:
        if d in dead or d not in workspace["results"]:
            continue
        result = workspace["results"][d]
        line = f"{d} ({result['agent']}): {result['text']}"
        if tables := [t["id"] for t in result["artifacts"].get("tables", [])]:
            line += f"\ntables: {', '.join(tables)}"
        lines.append(line)
    return "\n\n".join(lines) or "none", ", ".join(dead) or "none"


def _round_notes(routes: Sequence[str], available: Sequence[tuple[AgentSpec, RouteInfo]], base: str) -> dict[str, str]:
    """Each speaker's round note: `base` plus its own `RouteInfo.prompt_note` (docs: the user's file names)."""
    notes = {spec.route: info.prompt_note for spec, info in available}
    return {r: f"{base}\n{notes[r]}" if notes.get(r) else base for r in routes}


def _bullets(lines: Sequence[str]) -> str:
    return "\n".join(f"- {line}" for line in lines) or "none"


def _drawing(drawers: Sequence[str]) -> str:
    return DRAWING_NOTE.format(agent=" and ".join(drawers)) if drawers else ""


def with_drawers(tasks: Sequence[dict], drawers: Sequence[str], origin: str) -> list[dict]:
    """The tasks plus one chart task per drawer route; it waits for the final answer (`dispatch` never sends it)."""
    tasks = list(tasks)
    for route in drawers:
        tasks.append({"id": f"t{len(tasks) + 1}", "agent": route, "goal": DRAW_GOAL, "depends_on": [ANSWER],
                      "status": "pending", "origin": origin})
    return tasks


def _transcript(workspace: dict) -> dict:
    """The workspace as the answer carries it: the tables and charts travel on the answer itself, so results keep only text and tools."""
    transcript = {k: v for k, v in workspace.items() if k not in ("rounds_at", "artifacts", "challenged")}
    transcript["results"] = {k: {f: v for f, v in r.items() if f != "artifacts"} for k, r in workspace["results"].items()}
    return transcript


def _brief(task: dict) -> dict:
    return {k: task[k] for k in ("id", "agent", "goal", "depends_on")}


def build_team(specs: Sequence[AgentSpec], agents: Mapping, orchestrator):
    """The `team` subgraph. `agents`: route -> compiled graph or RemoteAgent; `orchestrator`: the
    orchestrator node function (it reads `team_note` as a hidden message)."""
    by_route = {spec.route: spec for spec in specs}

    def emit(event: dict) -> None:
        get_stream_writer()(event)

    def drawers_of(tasks: Sequence[dict]) -> list[str]:
        return list(dict.fromkeys(t["agent"] for t in tasks if by_route[t["agent"]].draws))

    def job(task_id: str, route: str, rnd: int, kind: str, note: str, messages) -> Send:
        return Send("speak", {"task": task_id, "route": route, "round": rnd, "kind": kind, "note": note, "messages": messages})

    async def ask_orchestrator(state: AssistantState, runtime: Runtime[AgentContext], note: str = "", drawn: bool = False):
        """The orchestrator node's Command for this thread (`note` as its hidden team message;
        `drawn`: a chart task charts the answer, so the orchestrator may not)."""
        return await orchestrator({**state, "team_note": note, "team_drawn": drawn} if note else state, runtime)

    async def handed_off(command: Command, messages, runtime: Runtime[AgentContext], config: RunnableConfig) -> AIMessage:
        """The orchestrator handed off: that agent answers on the thread, as in normal mode."""
        spec = by_route[command.goto]
        result = await agents[spec.route].ainvoke({"messages": messages}, config, context=runtime.context)
        turn = turn_messages(result.get("messages", []))
        return AIMessage(spec.result(result), name=spec.message_names[0], additional_kwargs=collect(turn))

    async def alone(state: TeamState, runtime: Runtime[AgentContext], config: RunnableConfig) -> Command:
        """No specialist is needed: the orchestrator answers as in normal mode."""
        command = await ask_orchestrator(state, runtime)
        if command.goto not in by_route:
            return Command(goto=END, update=command.update)
        return Command(goto=END, update={"messages": [await handed_off(command, state["messages"], runtime, config)]})

    async def plan(state: TeamState, runtime: Runtime[AgentContext], config: RunnableConfig) -> Command:
        messages = state["messages"]
        available = await available_agents(runtime.context.user_id, specs)
        everyone = participants(available, messages[-1].text)
        chosen = [spec.route for spec in everyone if not spec.draws]
        if not chosen:
            return await alone(state, runtime, config)
        fallback = False
        if len(everyone) == 1:  # exactly one speaker named: one task, no plan call
            tasks = [{**t, "origin": "plan"} for t in fallback_tasks(chosen)]
        else:
            try:
                tasks = validate_plan(await ask_plan(messages, available, config), [s.route for s, _ in available],
                                      get_settings().team_max_tasks)
            except (openai.RateLimitError, openai.AuthenticationError):
                raise
            except Exception:  # noqa: BLE001 - the participants take the work, as the round table did
                log.exception("Team plan failed")
                tasks = None
            if tasks == []:
                return await alone(state, runtime, config)
            if tasks is None:  # the participants take the work, a named chart agent draws from the answer
                tasks = with_drawers(fallback_tasks(chosen), [s.route for s in everyone if s.draws], "fallback")
                fallback = True
            else:  # a planned chart task waits for the answer, whatever the plan says; nothing waits for a chart task
                charts = {t["id"] for t in tasks if by_route[t["agent"]].draws}
                tasks = [{**t, "depends_on": [ANSWER]} if t["id"] in charts
                         else {**t, "depends_on": [d for d in t["depends_on"] if d not in charts]} for t in tasks]
        emit({"type": "plan", "tasks": [_brief(t) for t in tasks], "fallback": fallback})
        workspace = {"reset": True, "goal": messages[-1].text, "tasks": tasks, "results": {}, "evidence": [], "artifacts": {},
                     "history": [], "rounds_at": {}, "wave": 0, "question": "",
                     "synthesis": {"forced": False, "open_points": [], "not_done": []}, "challenged": {}}
        return Command(update={"workspace": workspace}, goto="dispatch")

    async def dispatch(state: TeamState, runtime: Runtime[AgentContext]) -> Command:
        workspace, messages = state["workspace"], state["messages"]
        tasks = [dict(t) for t in workspace["tasks"]]
        while True:  # a skipped task settles, which can leave its dependents without input too
            todo = ready(tasks)
            dead = [t for t in todo if dead_inputs(t, tasks)[1]]
            for t in dead:
                t["status"] = "skipped"
            if not dead:
                break
        if not todo:
            return Command(update={"workspace": {"tasks": tasks}}, goto="synthesize")
        for t in todo:
            t["status"] = "running"
        wave, language = workspace["wave"] + 1, reply_language(messages)
        available = await available_agents(runtime.context.user_id, specs)
        notes = {spec.route: info.prompt_note for spec, info in available}
        sends = []
        drawing = _drawing(drawers_of(tasks))
        for t in todo:
            if t["goal"]:
                inputs, missing = _inputs(t, {**workspace, "tasks": tasks})
                note = TASK_MESSAGE.format(goal=t["goal"], inputs=inputs, missing=missing, language=language)
            else:  # fallback and reply tasks: the plain round note
                note = ROUND_MESSAGE.format(language=language, drawing=drawing)
            if notes.get(t["agent"]):
                note = f"{note}\n{notes[t['agent']]}"
            sends.append(job(t["id"], t["agent"], 1, "plan", note, messages))
        ids, routes = [t["id"] for t in todo], [t["agent"] for t in todo]
        entry = {"type": "wave", "wave": wave, "kind": "plan", "tasks": ids, "agents": routes}
        emit(entry)
        return Command(update={"workspace": {"tasks": tasks, "wave": wave, "history": [entry]}}, goto=sends)

    async def speak(task: dict, runtime: Runtime[AgentContext], config: RunnableConfig) -> dict:
        route, spec, tid = task["route"], by_route[task["route"]], task["task"]
        emit({"type": "speaker", "agent": route, "task": tid, "ns": config["configurable"]["checkpoint_ns"]})
        context = dataclasses.replace(runtime.context, sources=tuple(sorted(spec.sources)))
        messages = [*task["messages"], HumanMessage(task["note"], name=TEAM)]
        entry = {"agent": route, "round": task["round"], "tools": [], "artifacts": {}}

        def done(text: str, status: str) -> dict:
            result = {**entry, "text": text}
            record = {"type": "result", "task": tid, "kind": task["kind"], "status": status,
                      **{k: result[k] for k in ("agent", "round", "text", "tools")}}
            return {"workspace": {"results": {tid: result}, "status": {tid: status}, "history": [record]}}

        recorder = ToolRecorder()
        try:
            result = await agents[route].ainvoke({"messages": messages}, merge_configs(config, {"callbacks": [recorder]}),
                                                 context=context)
        except (openai.RateLimitError, openai.AuthenticationError):
            raise
        except Exception as exc:  # noqa: BLE001 - the user still gets an answer from the others
            log.exception("Team step by %s failed", route)
            return done(f"failed: {type(exc).__name__}", "failed")
        turn = turn_messages(result.get("messages", []))
        # A server reports every tool call, a workflow's inner ones too; in-process the recorder sees them;
        # the message scan is what agents without callbacks leave.
        tools = result["tools"] if "tools" in result else recorder.tools or _tools(turn)
        # Research keeps its notes outside `messages`: it withdraws on empty findings, the others when `gave_up`.
        if spec.answers_directly:
            withdrew = spec.gave_up(turn) or bool(spec.source_tools and not any(t["name"] in spec.source_tools for t in tools))
        else:
            withdrew = not result.get("findings")
        entry = {**entry, "tools": tools, "artifacts": collect(turn)}
        text = spec.result(result)
        emit({"type": "spoke", "task": tid, "agent": route, "round": task["round"], "text": text, "tools": tools, "withdrew": withdrew})
        return done(text, "withdrew" if withdrew else "done")

    async def review(state: TeamState, runtime: Runtime[AgentContext], config: RunnableConfig) -> Command:
        workspace, messages, settings = state["workspace"], state["messages"], get_settings()
        tasks, results, rounds_at = workspace["tasks"], workspace["results"], workspace["rounds_at"]
        challenged, synthesis = workspace.get("challenged", {}), workspace["synthesis"]
        pending = [t for t in tasks if t["status"] == "pending" and ANSWER not in t["depends_on"]]
        if pending:  # an intermediate wave: its tasks, and only when they took input or meet in a pending task
            wave = next(e for e in reversed(workspace["history"]) if e["type"] == "wave" and e["kind"] == "plan")
            group = [t for t in tasks if t["id"] in wave["tasks"]]
            live_ids = {t["id"] for t in group if t["status"] in LIVE}
            worth = any(t["depends_on"] for t in group) or any(sum(d in live_ids for d in p["depends_on"]) > 1 for p in pending)
        else:  # the last wave: the board reads every task that answered
            group, worth = tasks, True
        meet = meeting_key([t["id"] for t in group])
        # A producer under challenge joins, so the review can say whether it revised its output.
        group = [*group, *(t for t in tasks if t["id"] in challenged and t not in group)]
        worth = worth or bool(challenged)
        drawn = drawers_of(tasks)  # a chart task is not an answer: it never joins the board or forces the write-up
        answers = _answers(workspace, group, drawn)
        for a in answers:
            if c := challenged.get(a["task"]):
                a["challenge"] = f"{c['by']} asked: {c['question']}"
        live = list(dict.fromkeys(a["agent"] for a in answers))
        speakers = [t for t in group if t["agent"] not in drawn]
        withdrawn = [t["agent"] for t in speakers if t["status"] == "withdrew"]
        # A lone task that did not withdraw still goes to the board; withdrawals leave nothing to compare.
        compare = worth and bool(answers) and (len(answers) > 1 or not withdrawn)
        broken = False
        board = Review()
        if compare:
            task_of = {t["id"]: t for t in tasks}
            inputs = {a["task"]: got for a in answers if (got := [d for d in task_of[a["task"]]["depends_on"] if d in results])}
            available = await available_agents(runtime.context.user_id, specs)
            wanted = {"inputs": inputs or None, "agents": [spec for spec, _ in available]}
            accepted = inspect.signature(ask_board).parameters  # a stand-in board may take fewer arguments
            try:
                board = await ask_board(messages, answers, config, **{k: v for k, v in wanted.items() if v and k in accepted})
                if not isinstance(board, Review):
                    board = Review(**board.model_dump())
                board = clean(board, live, [a["task"] for a in answers])
            except (openai.RateLimitError, openai.AuthenticationError):
                raise
            except Exception:  # noqa: BLE001 - the user still gets a write-up of the answers so far
                log.exception("Team board failed")
                broken = True
                board = Review()
        done_rounds = rounds_at.get(meet, 0)
        fixable = [c for c in board.conflicts if c.fixable_by]
        failed = broken or synthesis["forced"] or any(
            t["status"] == "failed" and results[t["id"]]["round"] == done_rounds + 1 for t in speakers)
        asks = bool(board.question.strip())
        again = bool(fixable) and not asks and not failed and done_rounds < settings.team_max_rounds
        forced = failed or (bool(fixable) and not asks and not again)
        # Outcomes of the last challenge round: revised -> its users run again; stands -> an open point.
        open_points, status, still = list(synthesis["open_points"]), {}, dict(challenged)
        for o in board.outcomes:
            if not (c := still.pop(o.task, None)):
                continue
            if o.revised:
                status.update({t["id"]: "pending" for t in tasks if t["status"] == "done" and o.task in t["depends_on"]})
            else:
                open_points.append(f"{o.task} stands: {c['question']}")
        # Challenges: one round for the producer per hand-off, at most `team_max_rounds` of them.
        sends, ids, new_rounds = [], [], {}
        language, wave, by_id = reply_language(messages), workspace["wave"] + 1, {a["task"]: a for a in answers}
        if not asks:
            for c in board.challenges:
                key = hand_off_key(c.to_task, c.by_task)
                if failed or rounds_at.get(key, 0) >= settings.team_max_rounds:
                    open_points.append(f"{c.by_task} questions {c.to_task}: {c.question}")
                    forced = True
                    continue
                new_rounds[key] = rounds_at.get(key, 0) + 1
                still[c.to_task] = {"by": c.by_task, "question": c.question}
                if c.to_task not in ids:
                    own = by_id[c.to_task]
                    note = CHALLENGE_MESSAGE.format(question=c.question, evidence=c.evidence, own=own["text"], language=language)
                    sends.append(job(c.to_task, own["agent"], results[c.to_task]["round"] + 1, "challenge", note, messages))
                    ids.append(c.to_task)
        kind = "challenge" if ids else "conflict"
        if again:
            routes = [r for r in live if any(r in c.routes for c in fixable)]
            for a in answers:
                if a["agent"] not in routes or a["task"] in ids:
                    continue
                mine = [c for c in fixable if a["agent"] in c.routes]
                others = [o for o in answers if o["agent"] != a["agent"] and any(o["agent"] in c.routes for c in mine)]
                note = CONFLICT_MESSAGE.format(
                    conflicts=_bullets([f"{c.claim} ({c.issue})" for c in mine]),
                    others="\n\n".join(f"{o['agent']}:\n{o['text']}" for o in others) or "none",
                    own=a["text"], language=language)
                sends.append(job(a["task"], a["agent"], done_rounds + 2, "conflict", note, messages))
                ids.append(a["task"])
            new_rounds[meet] = done_rounds + 1
        # Follow-ups: new pending tasks, once per agent and goal; a chart task waits for the answer, nothing waits for it.
        added, not_done = [], list(synthesis["not_done"])
        if compare and not broken and not asks and board.follow_ups:
            charts = {t["id"] for t in tasks if by_route[t["agent"]].draws}
            seen = {(t["agent"], " ".join(t["goal"].casefold().split())) for t in tasks}
            fresh = []
            for f in board.follow_ups:
                mark = (f.agent, " ".join(f.goal.casefold().split()))
                if mark not in seen:
                    seen.add(mark)
                    fresh.append(f.model_copy(update={"depends_on": [d for d in f.depends_on if d not in charts]}))
            _, added, dropped = add_follow_ups(tasks, fresh, [s.route for s in specs], settings.team_max_tasks)
            added = [{**t, "depends_on": [ANSWER]} if by_route[t["agent"]].draws else t for t in added]
            not_done += dropped
        update = {"synthesis": {**synthesis, "forced": forced, "open_points": open_points, "not_done": not_done}, "challenged": still}
        if status:
            update["status"] = status
        if compare:
            record = {"type": "review", "key": meet, **board.model_dump(), "forced": forced}
            emit({"type": "review", **{k: v for k, v in record.items() if k != "type"}})
            update["history"] = [record]
        if asks:
            emit({"type": "needs_human", "question": board.question})
            return Command(update={"workspace": {**update, "question": board.question},
                                   "messages": [AIMessage(board.question, name=ORCHESTRATOR)]}, goto="pause")
        if added:  # the new tasks join with the statuses of this review folded in
            emit({"type": "plan_updated", "tasks": added})
            final = {**update.get("status", {}), **{i: "running" for i in ids}}
            update["tasks"] = [{**t, "status": final.get(t["id"], t["status"])} for t in tasks] + added
            update.pop("status", None)
        if not sends:
            return Command(update={"workspace": update}, goto="dispatch")
        entry = {"type": "wave", "wave": wave, "kind": kind, "tasks": ids, "agents": [by_id[i]["agent"] for i in ids]}
        emit(entry)
        update["history"] = [*update.get("history", []), entry]
        update.update({"wave": wave, "rounds_at": {**rounds_at, **new_rounds}})
        if not added:
            update["status"] = {**status, **{i: "running" for i in ids}}
        return Command(update={"workspace": update}, goto=sends)

    async def pause(state: TeamState, runtime: Runtime[AgentContext]) -> Command:
        # Nothing before `interrupt`: on resume the node runs again from its start.
        reply = interrupt({"question": state["workspace"]["question"]})
        workspace, messages = state["workspace"], [*state["messages"], HumanMessage(reply)]
        available = await available_agents(runtime.context.user_id, specs)
        # Only an agent the reply names joins (a chart agent brings its `fed_by`); a free-text answer brings nobody in.
        hits = [s for s, i in available if s.is_named(reply, i)]
        named = participants(available, reply) if any(not s.draws for s in hits) else [
            s for s, _ in available if s in hits or any(s.route in h.fed_by for h in hits)]
        taking_part = {t["agent"] for t in workspace["tasks"]}
        new = [s.route for s in named if not s.draws and s.route not in taking_part]
        # The reply names an agent that is not taking part yet: it answers one wave before the review runs again.
        tasks = list(workspace["tasks"])
        for t in fallback_tasks(new):
            tasks.append({**t, "id": f"t{len(tasks) + 1}", "origin": "reply"})
        n = len(tasks)
        tasks = with_drawers(tasks, [s.route for s in named if s.draws and s.route not in taking_part], "reply")
        added = tasks[len(workspace["tasks"]):n]  # a chart task waits for the answer: it alone sends nothing now
        update = {"tasks": tasks}
        return Command(update={"messages": messages[-1:], "workspace": update}, goto="dispatch" if added else "review")

    async def synthesize(state: TeamState, runtime: Runtime[AgentContext], config: RunnableConfig) -> dict:
        workspace, messages = state["workspace"], state["messages"]
        drawers = drawers_of(workspace["tasks"])
        answers = _answers(workspace, workspace["tasks"], drawers)
        last = next((e for e in reversed(workspace["history"]) if e["type"] == "review"), None)
        board = Review(**last) if last else Review()
        forced = workspace["synthesis"]["forced"]
        open_points = workspace["synthesis"]["open_points"] + [f"not done: {g}" for g in workspace["synthesis"]["not_done"]]
        only = by_route[answers[0]["agent"]] if answers else None
        if not answers:  # every agent withdrew: the orchestrator answers as in normal mode and says so
            emit({"type": "synthesize"})
            withdrawn = [t["agent"] for t in workspace["tasks"] if t["status"] == "withdrew" and t["agent"] not in drawers]
            note = NOTHING_FOUND_MESSAGE.format(withdrawn=", ".join(dict.fromkeys(withdrawn)),
                                                language=reply_language(messages), drawing=_drawing(drawers))
            command = await ask_orchestrator(state, runtime, note, bool(drawers))
            answer = await handed_off(command, messages, runtime, config) if command.goto in by_route \
                else command.update["messages"][0]
        elif len(answers) == 1 and not board.conflicts and not forced and not open_points and only.answers_directly:
            answer = AIMessage(answers[0]["text"], name=only.message_names[0])
        else:
            emit({"type": "synthesize"})  # only a write-up announces itself
            note = TEAM_MESSAGE.format(
                answers="\n\n".join(f"{a['agent']}:\n{a['text']}" for a in answers),
                differences=_bullets([f"{', '.join(d.routes)}: {d.explanation} ({'; '.join(f'{x.route}: {x.value}, {x.source}, {x.as_of}' for x in d.sides)})"
                                   for d in board.differences]),
                conflicts=_bullets([f"{c.claim} ({', '.join(c.routes)}): {c.issue}" for c in board.conflicts] + open_points),
                language=reply_language(messages), drawing=_drawing(drawers))
            command = await ask_orchestrator(state, runtime, note, bool(drawers))
            did_handoff = command.goto in by_route  # it handed off: the answers stand
            answer = AIMessage("\n\n".join(a["text"] for a in answers), name=ORCHESTRATOR) if did_handoff \
                else command.update["messages"][0]
        artifacts = _merge([r["artifacts"] for r in workspace["results"].values()])
        kwargs = {**answer.additional_kwargs, **_merge([artifacts, collect([answer])]), "team": _transcript(workspace)}
        if kwargs.get("charts") and not drawers:
            emit({"type": "charts", "charts": kwargs["charts"]})
        # An id, so the `draw` step's version replaces this message instead of joining it.
        return {"messages": [answer.model_copy(update={"additional_kwargs": kwargs, "id": answer.id or str(uuid.uuid4())})]}

    async def draw(state: TeamState, runtime: Runtime[AgentContext], config: RunnableConfig) -> dict:
        """Each chart task runs once on the thread plus the final answer, with no data source, and its charts join the answer."""
        workspace, messages, answer = state["workspace"], state["messages"], state["messages"][-1]
        artifacts, results, status = [collect([answer])], {}, {}
        for task in (t for t in workspace["tasks"] if t["status"] == "pending" and ANSWER in t["depends_on"]):
            route, tid = task["agent"], task["id"]
            emit({"type": "speaker", "agent": route, "task": tid, "ns": config["configurable"]["checkpoint_ns"]})
            # With a table on this turn's answer the chart agent charts that table: it may not copy numbers.
            deny = ("chart_data",) if answer.additional_kwargs.get("tables") else ()
            context = dataclasses.replace(runtime.context, sources=NO_SOURCES, deny_tools=deny)
            note = HumanMessage(DRAW_MESSAGE.format(language=reply_language(messages)), name=TEAM)
            recorder = ToolRecorder()
            tools, text, charts = [], "", []
            try:
                result = await agents[route].ainvoke({"messages": [*messages, note]},
                                                     merge_configs(config, {"callbacks": [recorder]}), context=context)
            except (openai.RateLimitError, openai.AuthenticationError):
                raise
            except Exception as exc:  # noqa: BLE001 - the final answer stands without the chart
                log.exception("Team chart task %s failed", route)
                status[tid], text = "failed", f"failed: {type(exc).__name__}"
            else:
                turn = turn_messages(result.get("messages", []))
                tools = result["tools"] if "tools" in result else recorder.tools or _tools(turn)
                status[tid], text, charts = "done", by_route[route].result(result), collect(turn).get("charts", [])
            results[tid] = {"agent": route, "round": 1, "tools": tools, "artifacts": {}, "text": text}
            artifacts.append({"charts": charts})
        update = {"results": results, "status": status}
        merged = _merge(artifacts)
        kwargs = {**answer.additional_kwargs, **merged, "team": _transcript(merge_workspace(workspace, update))}
        if merged.get("charts"):
            emit({"type": "charts", "charts": merged["charts"]})
        return {"messages": [answer.model_copy(update={"additional_kwargs": kwargs})], "workspace": update}

    graph = StateGraph(TeamState, context_schema=AgentContext)
    graph.add_node("plan", plan, destinations=("dispatch", END))
    graph.add_node("dispatch", dispatch, destinations=("speak", "synthesize"))
    graph.add_node("speak", speak)
    graph.add_node("review", review, destinations=("speak", "dispatch", "pause"))
    graph.add_node("pause", pause, destinations=("review", "dispatch"))
    graph.add_node("synthesize", synthesize)
    graph.add_edge(START, "plan")
    graph.add_edge("speak", "review")
    graph.add_node("draw", draw)
    graph.add_conditional_edges("synthesize", lambda state: "draw" if any(
        t["status"] == "pending" and ANSWER in t["depends_on"] for t in state["workspace"]["tasks"]) else END, ["draw", END])
    graph.add_edge("draw", END)
    return graph.compile(name=TEAM)
