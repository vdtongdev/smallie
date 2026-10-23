"""Team mode: a judge decides after each draft whether a critic checks it (design:
docs/superpowers/specs/2026-10-23-agent-team-design.md):

    delegate -> judge -+- approve ------------------------------> synthesize -> END
                       +- needs_human -> pause -> judge
                       +- critique -> critique -> judge
                       +- revise -> refine -> critique (turns < cap) | synthesize (forced)

A `team` node of the assistant graph. Only the synthesized answer joins the thread; the transcript
goes on its `additional_kwargs["team"]`. Progress goes out as `custom` stream events.
"""

import asyncio
import logging
from collections.abc import Mapping, Sequence
from typing import Literal

import openai
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt
from pydantic import BaseModel, create_model, field_validator

from app.agents.artifacts import KINDS, collect
from app.agents.base import AgentSpec, RouteInfo
from app.core.config import get_settings
from app.core.llm import get_chat_model
from app.core.memory import TEAM_MODE, AgentContext
from app.core.prompts import reply_language
from app.orchestration.prompts import (
    CRITIQUE_MESSAGE,
    CUT_SHORT,
    FAILED_STEP,
    JUDGE_PROMPT,
    REFINE_MESSAGE,
    TEAM_MESSAGE,
    UNREVIEWED,
)
from app.orchestration.router import ORCHESTRATOR, AssistantState, available_agents, obvious_route

log = logging.getLogger(__name__)

TEAM = TEAM_MODE


class Verdict(BaseModel):
    action: Literal["approve", "critique", "revise", "needs_human"]
    critic: str | None = None
    issues: list[str] = []
    question: str = ""

    @field_validator("issues")
    @classmethod
    def _cap(cls, issues: list[str]) -> list[str]:
        return issues[:5]


def verdict_model(allowed: list[AgentSpec]) -> type[Verdict]:
    """Verdict whose `critic` only accepts an allowed route (plain Verdict when none)."""
    if not allowed:
        return Verdict
    routes = Literal[tuple(spec.route for spec in allowed)]  # type: ignore[valid-type]
    return create_model("TeamVerdict", __base__=Verdict, critic=(routes | None, None))


def allowed_critics(available: list[tuple[AgentSpec, RouteInfo]], author: str,
                    author_sources: frozenset[str]) -> list[AgentSpec]:
    """Reviewers other than the author that read a source the author did not (a same-source critic
    could only confirm its figures)."""
    return [spec for spec, _ in available
            if spec.review and spec.route != author and not (spec.sources and spec.sources <= author_sources)]


def coerce(verdict: Verdict, allowed: list[AgentSpec]) -> Verdict:
    """Make a verdict safe to route on: a critic outside `allowed` becomes the first allowed one."""
    update: dict = {}
    if verdict.action == "needs_human" and not verdict.question.strip():
        update = {"action": "approve", "critic": None}
    elif verdict.action in ("critique", "revise"):
        if not allowed:
            update = {"action": "approve" if verdict.action == "critique" else "revise", "critic": None}
        elif verdict.critic not in [spec.route for spec in allowed]:
            update = {"critic": allowed[0].route}
    else:
        update = {"critic": None}
    return verdict.model_copy(update=update)


async def ask_judge(messages: list[AnyMessage], team: dict, allowed: list[AgentSpec],
                    config: RunnableConfig) -> Verdict:
    """One model call; `config` carries the usage counter's callbacks."""
    settings = get_settings()
    question = next((m.text for m in reversed(messages) if isinstance(m, HumanMessage)), "")
    critiques = "\n\n".join(f"{c['critic']}: {c['text']}" for c in team.get("critiques", [])) or "none yet"
    system = JUDGE_PROMPT.format(
        critics="\n".join(f"- {spec.route}: {spec.review}" for spec in allowed) or "none: choose approve, revise or needs_human.",
        turns=team.get("turns", 0), max_turns=settings.team_max_turns, language=reply_language(messages))
    # Texts go into the message, not a template: drafts may contain braces.
    human = f"USER MESSAGE:\n{question}\n\nDRAFT:\n{team.get('draft', '')}\n\nCRITIQUES:\n{critiques}"
    judge = get_chat_model(settings.openrouter_orchestrator_model or None).with_structured_output(
        verdict_model(allowed), method="function_calling")
    return coerce(await judge.ainvoke([SystemMessage(system), HumanMessage(human)], config=config), allowed)


def _merge(*artifacts: dict) -> dict:
    """Tables and charts of several runs, once per id."""
    merged = {kind: list({item["id"]: item for a in artifacts for item in a.get(kind, [])}.values()) for kind in KINDS}
    return {kind: items for kind, items in merged.items() if items}


def _bullets(issues: Sequence[str]) -> str:
    return "\n".join(f"- {issue}" for issue in issues)


def build_team(specs: Sequence[AgentSpec], agents: Mapping, orchestrator):
    """The `team` subgraph. `agents`: route -> compiled graph or RemoteAgent; `orchestrator`: the
    orchestrator node function (it reads `team_note` as a hidden message)."""
    by_route = {spec.route: spec for spec in specs}

    def emit(event: dict) -> None:
        get_stream_writer()(event)

    def announce(phase: str, agent: str, turn: int) -> None:
        emit({"type": "team", "phase": phase, "agent": agent, "turn": turn, "max_turns": get_settings().team_max_turns})

    async def run(route: str, messages: list[AnyMessage], runtime: Runtime[AgentContext], config: RunnableConfig):
        state = await agents[route].ainvoke({"messages": messages}, config, context=runtime.context)
        return by_route[route].result(state), collect(state.get("messages", []))

    async def ask_orchestrator(state: AssistantState, runtime: Runtime[AgentContext], note: str = ""):
        """(handoff route, None) or (None, the orchestrator's answer)."""
        command = await orchestrator({**state, "team_note": note} if note else state, runtime)
        if command.goto in by_route:
            return command.goto, None
        return None, command.update["messages"][0]

    def force(team: dict, reason: str) -> dict:
        """Mark the last verdict as a forced stop and say what stayed open."""
        verdicts = team["verdicts"]
        if verdicts:
            verdicts = [*verdicts[:-1], {**verdicts[-1], "forced": True}]
            emit({"type": "verdict", **verdicts[-1]})
        return {**team, "verdicts": verdicts, "open": reason}

    def failed(team: dict, route: str, exc: Exception) -> Command:
        log.exception("Team step by %s failed", route)
        critiques = [*team["critiques"], {"critic": route, "text": f"failed: {type(exc).__name__}", "turn": team["turns"]}]
        return Command(update={"team": force({**team, "critiques": critiques}, FAILED_STEP)}, goto="synthesize")

    async def delegate(state: AssistantState, runtime: Runtime[AgentContext], config: RunnableConfig) -> dict:
        messages = state["messages"]
        available = await available_agents(runtime.context.user_id, specs)
        route = await asyncio.to_thread(obvious_route, messages[-1].text, available)
        if route and not by_route[route].answers_directly:
            route = None
        announce("delegate", route or ORCHESTRATOR, 0)
        draft, artifacts, asked = "", {}, []
        if route is None:
            route, answer = await ask_orchestrator(state, runtime)
            if answer is not None:
                draft, artifacts, asked = answer.text, collect([answer]), answer.additional_kwargs.get("asked", [])
        if route:
            draft, artifacts = await run(route, messages, runtime, config)
        sources = by_route[route].sources if route else frozenset().union(*(by_route[r].sources for r in asked))
        team = {"phase": "delegate", "author": route or ORCHESTRATOR, "author_sources": sorted(sources), "critic": None, "draft": draft, "critiques": [],
                "verdicts": [], "turns": 0, "question": "", "issues": [], "open": "", "artifacts": artifacts}
        return {"team": team}  # a fresh debate: nothing of an earlier message's carries over

    async def judge(state: AssistantState, runtime: Runtime[AgentContext], config: RunnableConfig) -> Command:
        team = state["team"]
        available = await available_agents(runtime.context.user_id, specs)
        verdict = await ask_judge(state["messages"], team, allowed_critics(available, team["author"], frozenset(team["author_sources"])), config)
        cut = verdict.action in ("critique", "revise") and team["turns"] >= get_settings().team_max_turns
        record = {"action": verdict.action, "critic": verdict.critic, "issues": verdict.issues,
                  "turn": team["turns"], "forced": cut}
        emit({"type": "verdict", **record})
        team = {**team, "verdicts": [*team["verdicts"], record], "critic": verdict.critic, "issues": verdict.issues}
        if cut:
            return Command(update={"team": {**team, "open": _bullets(verdict.issues) or CUT_SHORT}}, goto="synthesize")
        if verdict.action == "needs_human":
            emit({"type": "needs_human", "question": verdict.question})
            return Command(update={"team": {**team, "question": verdict.question},
                                   "messages": [AIMessage(verdict.question, name=ORCHESTRATOR)]}, goto="pause")
        goto = {"approve": "synthesize", "critique": "critique", "revise": "refine"}[verdict.action]
        return Command(update={"team": team}, goto=goto)

    async def pause(state: AssistantState) -> Command:
        # Only this: on resume the node runs again from its start.
        reply = interrupt({"question": state["team"]["question"]})
        return Command(update={"messages": [HumanMessage(reply)]}, goto="judge")

    async def critique(state: AssistantState, runtime: Runtime[AgentContext], config: RunnableConfig) -> Command:
        team, messages = state["team"], state["messages"]
        route, turn = team["critic"], team["turns"] + 1
        announce("critique", route, turn)
        note = HumanMessage(CRITIQUE_MESSAGE.format(draft=team["draft"], language=reply_language(messages)), name=TEAM)
        try:
            text, _ = await run(route, [*messages, note], runtime, config)
        except (openai.RateLimitError, openai.AuthenticationError):
            raise
        except Exception as exc:  # noqa: BLE001 - the user still gets an answer from the latest draft
            return failed(team, route, exc)
        critiques = [*team["critiques"], {"critic": route, "text": text, "turn": turn}]
        return Command(update={"team": {**team, "phase": "critique", "critiques": critiques, "turns": turn}}, goto="judge")

    async def refine(state: AssistantState, runtime: Runtime[AgentContext], config: RunnableConfig) -> Command:
        team, messages = state["team"], state["messages"]
        author, turn = team["author"], team["turns"] + 1
        announce("refine", author, turn)
        note = REFINE_MESSAGE.format(draft=team["draft"], issues=_bullets(team["issues"]), language=reply_language(messages))
        try:
            if author == ORCHESTRATOR:  # a handoff is not followed: the draft stays
                _, answer = await ask_orchestrator(state, runtime, note)
                text, artifacts = (answer.text, collect([answer])) if answer is not None else ("", {})
            else:
                text, artifacts = await run(author, [*messages, HumanMessage(note, name=TEAM)], runtime, config)
        except (openai.RateLimitError, openai.AuthenticationError):
            raise
        except Exception as exc:  # noqa: BLE001 - the user still gets an answer from the latest draft
            return failed(team, author, exc)
        team = {**team, "phase": "refine", "draft": text or team["draft"], "turns": turn,
                "artifacts": _merge(team["artifacts"], artifacts)}
        if team["critic"] and turn < get_settings().team_max_turns:
            return Command(update={"team": team}, goto="critique")
        if turn >= get_settings().team_max_turns:
            team = force(team, UNREVIEWED)
        return Command(update={"team": team}, goto="synthesize")

    async def synthesize(state: AssistantState, runtime: Runtime[AgentContext]) -> dict:
        team, messages = state["team"], state["messages"]
        announce("synthesize", ORCHESTRATOR, team["turns"])
        note = TEAM_MESSAGE.format(
            draft=team["draft"], language=reply_language(messages), issues=team["open"] or "none",
            critiques="\n\n".join(f"{c['critic']}: {c['text']}" for c in team["critiques"]) or "none")
        _, answer = await ask_orchestrator(state, runtime, note)
        answer = answer or AIMessage(team["draft"], name=ORCHESTRATOR)  # it handed off: the draft stands
        transcript = {key: team[key] for key in ("author", "draft", "critiques", "verdicts", "turns")}
        kwargs = {**answer.additional_kwargs, **_merge(team["artifacts"], collect([answer])), "team": transcript}
        return {"messages": [answer.model_copy(update={"additional_kwargs": kwargs})]}

    graph = StateGraph(AssistantState, context_schema=AgentContext)
    graph.add_node("delegate", delegate)
    graph.add_node("judge", judge, destinations=("synthesize", "pause", "critique", "refine"))
    graph.add_node("critique", critique, destinations=("judge", "synthesize"))
    graph.add_node("refine", refine, destinations=("critique", "synthesize"))
    graph.add_node("pause", pause, destinations=("judge",))
    graph.add_node("synthesize", synthesize)
    graph.add_edge(START, "delegate")
    graph.add_edge("delegate", "judge")
    graph.add_edge("synthesize", END)
    return graph.compile(name=TEAM)
