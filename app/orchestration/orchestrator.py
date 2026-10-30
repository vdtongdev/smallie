"""The front door: answers itself (generic chat, a quick web search), hands a message off to
one specialist, or asks specialists as tools and writes the answer from their results.

The `orchestrator` node wraps the agent like a workflow step: only its final answer goes into
the shared thread, so delegated results and search output don't bloat later prompts (they show
in the UI as tool calls while it runs). `handoff` ends the agent at once (`return_direct`) and the
node jumps to that specialist (`Command(goto=...)`), which replies on the same thread without the
orchestrator's tool call in it. Small models rarely pick `handoff` and ask the one specialist
instead (an extra write-up call, and nothing streams until it's done), so a first step that asks
exactly one directly-answering specialist is turned into a handoff (`prefer_handoff`), unless the
message also points to another specialist (`AgentContext.mentioned`): then it can still ask that
one in its next step and combine the answers.
Research findings from the router's direct route arrive in `findings` and are written up the
same way as the orchestrator's own `ask_research` results. When a specialist the router picked
found no answer, what it tried arrives in `attempt` (app/orchestration/fallback.py): the
orchestrator then gets no `handoff`, and `no_repeats` stops it from asking a specialist more than
`MAX_ASKS` times or running the same tool call twice in one message.
"""

import dataclasses
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Literal

from langchain.agents.middleware import ModelRequest, ModelResponse, wrap_model_call, wrap_tool_call
from langchain.tools import tool
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langgraph.graph import END
from langgraph.runtime import Runtime
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.agents import registry
from app.agents.artifacts import artifacts_from, collect, table_note
from app.agents.base import LIMIT_MESSAGE, MEMORY_TOOL_NAMES, AgentSpec
from app.agents.chart.prompts import TABLES_SECTION
from app.agents.chart.tools import chart_data, chart_table
from app.agents.delegation import ask_tool_name, make_ask_tool
from app.agents.factory import build_agent, call_key
from app.agents.research.agent import mcp_tools
from app.agents.skills import load_skills, skills_inline
from app.core.config import get_settings
from app.core.memory import TEAM_MODE, AgentContext
from app.core.prompts import CALL_NOTE, LANGUAGE_RULE, MEMORY_RULE, reply_language
from app.orchestration.prompts import (
    ALREADY_ASKED,
    ATTEMPT_MESSAGE,
    BOOTSTRAP_SECTION,
    COMPOSE_SECTION,
    FINDINGS_MESSAGE,
    MENTIONS_NOTE,
    ORCHESTRATOR_SYSTEM,
    PREVIOUS_NOTE,
    REPLY_LANGUAGE,
    REPEATED_CALL,
    UNAVAILABLE_NOTE,
)
from app.orchestration.router import ORCHESTRATOR, AssistantState, available_agents, mentioned_routes, previous_route

LABELS = {"en": "Assistant", "vi": "Trợ lý"}
FORMATS = skills_inline(load_skills(Path(__file__).resolve().parent / "formats"))  # answer formats
HANDOFF = "handoff"
FINDINGS = "research_findings"  # HumanMessage.name of the hidden findings message
ATTEMPT = "failed_attempt"  # HumanMessage.name of the hidden note on a specialist that gave up
HIDDEN = frozenset({FINDINGS, ATTEMPT, TEAM_MODE, CALL_NOTE})
MAX_ASKS = 2  # `ask_<route>` calls per specialist and message, a fallen-back direct attempt included
WEB_SEARCH_TOOLS = ("tavily_tavily_search", "knowledge_wikipedia_search")
SORRY = "Sorry, I couldn't finish this answer. Please try again or rephrase the question."

# AIMessage.name -> route, including the orchestrator (and the removed research writer of old chats).
MESSAGE_ROUTES = {**registry.MESSAGE_ROUTES, ORCHESTRATOR: ORCHESTRATOR, "report_agent": ORCHESTRATOR}


def make_handoff_tool(routes: Sequence[str]) -> BaseTool:
    class HandoffArgs(BaseModel):
        agent: Literal[tuple(routes)] = Field(description="The specialist that answers the whole message.")

    @tool(HANDOFF, args_schema=HandoffArgs, return_direct=True)
    def handoff(agent: str) -> str:
        """Hand the user's message to one specialist, which then replies to the user directly (it sees the whole chat). Call it alone, without other tools or text."""
        return f"Handed off to {agent}."

    return handoff


def handoff_target(messages: Sequence[AnyMessage], routes: Sequence[str]) -> str | None:
    for message in messages:
        if isinstance(message, AIMessage):
            for call in message.tool_calls:
                if call["name"] == HANDOFF and call["args"].get("agent") in routes:
                    return call["args"]["agent"]
    return None


def single_delegation(message: AIMessage, routes: Sequence[str]) -> str | None:
    """The route when `message` asks exactly one of `routes` (memory tool calls aside)."""
    calls = [c for c in message.tool_calls if c["name"] not in MEMORY_TOOL_NAMES]
    if len(calls) != 1:
        return None
    return next((route for route in routes if calls[0]["name"] == f"ask_{route}"), None)


def as_handoff(message: AIMessage, route: str) -> AIMessage:
    """Only the handoff call: `return_direct` ends the agent only when every call in the step is
    return-direct, and the specialist can still save a memory (it has the same tools and message)."""
    call = next(c for c in message.tool_calls if c["name"] not in MEMORY_TOOL_NAMES)
    handoff = {**call, "name": HANDOFF, "args": {"agent": route}}
    return message.model_copy(update={"tool_calls": [handoff], "tool_call_chunks": [], "content": ""})


def has_findings(messages: Sequence[AnyMessage]) -> bool:
    return any(isinstance(m, HumanMessage) and m.name == FINDINGS for m in messages)


def make_prefer_handoff(routes: Sequence[str], research_tools: frozenset[str]):
    @wrap_model_call
    async def prefer_handoff(request: ModelRequest, handler):
        if has_findings(request.messages):
            # The router already ran the research: write it up instead of researching again.
            request = request.override(tools=[t for t in request.tools if getattr(t, "name", "") not in research_tools])
        if request.runtime.context.failed:
            # A specialist already gave up on this message: answer it here, never hand it back.
            return await handler(request.override(
                tools=[t for t in request.tools if getattr(t, "name", "") != HANDOFF]))
        response = await handler(request)
        if is_composing(request.messages):
            return response  # a later step: asking one more specialist is fine
        messages = response.result if isinstance(response, ModelResponse) else [response]
        last = messages[-1]
        if not isinstance(last, AIMessage):
            return response
        route = handoff_target([last], routes)
        if not route and (asked := single_delegation(last, routes)):
            # The message points to another specialist too: keep asking, so it can be combined.
            others = set(request.runtime.context.mentioned) - {asked}
            route = None if others else asked
        if route:
            messages = [*messages[:-1], as_handoff(last, route)]
            return ModelResponse(result=messages, structured_response=getattr(response, "structured_response", None))
        return response

    return prefer_handoff


def calls_before(messages: Sequence[AnyMessage], call_id: str) -> list[dict]:
    """This run's tool calls (after the last user or hidden message) that came before `call_id`."""
    calls = []
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            break
        if isinstance(message, AIMessage):
            calls[:0] = message.tool_calls
    ids = [c["id"] for c in calls]
    return calls[:ids.index(call_id)] if call_id in ids else calls


CHART_TOOLS = ("chart_table", "chart_data")


@wrap_tool_call
async def no_repeats(request, handler):
    """Stops loops of failing calls: the same tool call twice in one message, or a specialist
    asked more than MAX_ASKS times (the direct attempt of one that gave up counts as one)."""
    call = request.tool_call
    if call["name"] in MEMORY_TOOL_NAMES:
        return await handler(request)
    earlier = calls_before(request.state["messages"], call["id"])
    if call_key(call) in {call_key(c) for c in earlier}:
        return ToolMessage(REPEATED_CALL, tool_call_id=call["id"], name=call["name"])
    if call["name"].startswith("ask_"):
        asks = sum(c["name"] == call["name"] for c in earlier) + (call["name"] == f"ask_{request.runtime.context.failed}")
        if asks >= MAX_ASKS:
            return ToolMessage(ALREADY_ASKED, tool_call_id=call["id"], name=call["name"])
    return await handler(request)


def is_composing(messages: Sequence[AnyMessage]) -> bool:
    """True once the current turn has results to write up (tool results or research findings)."""
    for message in reversed(messages):
        if isinstance(message, ToolMessage) and message.name not in MEMORY_TOOL_NAMES:
            return True
        if isinstance(message, HumanMessage):
            return message.name in (FINDINGS, TEAM_MODE)
    return False


def is_first_turn(messages: Sequence[AnyMessage]) -> bool:
    """True while no reply precedes the latest visible user message: the chat's first turn."""
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage) and messages[i].name not in HIDDEN:
            return not any(isinstance(m, AIMessage) for m in messages[:i])
    return False


def compose_section(request: ModelRequest) -> str:
    """This request's notes, plus the format and citation rules once there is something to write
    up: the first (deciding) call stays short, and plain chat doesn't get research formatting.
    On an onboarding user's first turn, every call ends with the onboarding question."""
    question = [m for m in request.messages if not (isinstance(m, HumanMessage) and m.name in HIDDEN)]
    section = f"\n\n{request.runtime.context.notes}" if request.runtime.context.notes else ""
    section += REPLY_LANGUAGE.format(language=reply_language(question))
    if is_composing(request.messages):
        tables = artifacts_from(request.messages, "tables")  # e.g. from ask_sql, for chart_table
        charts = artifacts_from(request.messages, "charts")
        tables = TABLES_SECTION.format(tables=table_note(tables, charts)) if tables else ""
        section += tables + COMPOSE_SECTION.format(formats=FORMATS, language=reply_language(question))
    if request.runtime.context.bootstrap and is_first_turn(request.messages):
        section += BOOTSTRAP_SECTION
    return section


def agent_lines(specs: Sequence[AgentSpec]) -> str:
    lines = []
    for spec in specs:
        ways = ([f"`{HANDOFF}`"] if spec.answers_directly else []) + (
            [f"`{ask_tool_name(spec)}`"] if spec.tool_description else [])
        if ways:
            lines.append(f"- {spec.route}: {spec.description} Use: {' or '.join(ways)}.")
    return "\n".join(lines)


async def request_notes(
    user_id: str, specs: Sequence[AgentSpec], messages: list[AnyMessage]
) -> tuple[str, tuple[str, ...]]:
    """What the orchestrator should know about this user's specialists right now, and the
    specialists the latest message clearly points to."""
    available = await available_agents(user_id, specs)
    offered = {spec.route for spec, _ in available}
    latest = messages[-1].text if messages else ""
    mentioned = mentioned_routes(latest, available)
    lines = [UNAVAILABLE_NOTE.format(route=spec.route) for spec in specs if spec.route not in offered]
    lines += [info.prompt_note for _, info in available if info.prompt_note]
    if previous := previous_route(messages, MESSAGE_ROUTES):
        lines.append(PREVIOUS_NOTE.format(previous=previous))
    if len(mentioned) > 1:
        lines.append(MENTIONS_NOTE.format(routes=", ".join(mentioned)))
    return "\n".join(lines), mentioned


def web_search_rule(names: Sequence[str]) -> str:
    """How the prompt names the orchestrator's own search tools: web search, with Wikipedia
    alongside it in the same step."""
    web, wiki = "tavily_tavily_search" in names, "knowledge_wikipedia_search" in names
    if web and wiki:
        return "`tavily_tavily_search` (plus `knowledge_wikipedia_search` in the same step when background helps)"
    if web or wiki:
        return f"`{'tavily_tavily_search' if web else 'knowledge_wikipedia_search'}`"
    return "nothing (no web search is configured: say that your information may be out of date)"


def build_orchestrator(specs: Sequence[AgentSpec], agents: Mapping[str, Runnable]):
    """The orchestrator node and the routes it can hand off to."""
    settings = get_settings()
    targets = [spec.route for spec in specs if spec.answers_directly]
    web = [t for t in mcp_tools() if t.name in WEB_SEARCH_TOOLS]
    tools = [make_ask_tool(spec, agents[spec.route]) for spec in specs if spec.tool_description]
    tools += [*web, chart_table, chart_data]
    if targets:
        tools.append(make_handoff_tool(targets))
    web_search = web_search_rule([t.name for t in web])
    agent = build_agent(
        name=ORCHESTRATOR,
        system_prompt=ORCHESTRATOR_SYSTEM.format(
            today=date.today().isoformat(), agents=agent_lines(specs), web_search=web_search,
            language_rule=LANGUAGE_RULE, memory_rule=MEMORY_RULE,
        ),
        tools=tools,
        max_model_calls=settings.orchestrator_max_model_calls,
        tail=compose_section,
        last_call_writes=False,
        middleware=[make_prefer_handoff(targets, frozenset(
            ask_tool_name(spec) for spec in specs if spec.tool_description and not spec.answers_directly)),
            no_repeats],
    )

    async def orchestrator(state: AssistantState, runtime: Runtime[AgentContext]) -> Command:
        messages = state["messages"]
        hidden = [HumanMessage(FINDINGS_MESSAGE.format(findings=f, language=reply_language(messages)), name=FINDINGS)
                  ] if (f := state.get("findings")) else []
        if failed := state.get("failed", ""):
            hidden.append(HumanMessage(ATTEMPT_MESSAGE.format(route=failed, attempt=state.get("attempt", "")),
                                       name=ATTEMPT))
        if note := state.get("team_note"):
            hidden.append(HumanMessage(note, name=TEAM_MODE))
        notes, mentioned = await request_notes(runtime.context.user_id, specs, messages)
        context = dataclasses.replace(runtime.context, notes=notes, mentioned=mentioned, failed=failed,
                                      deny_tools=CHART_TOOLS if state.get("team_drawn") else ())
        result = await agent.ainvoke({"messages": [*messages, *hidden]}, context=context)
        new = result["messages"][len(messages) + len(hidden):]
        if target := handoff_target(new, targets):
            return Command(goto=target, update={"route": target, "direct": False, "findings": ""})
        answer = next((m for m in reversed(new) if isinstance(m, AIMessage) and m.text and not m.tool_calls), None)
        if answer is None or answer.text.startswith(LIMIT_MESSAGE):
            answer = AIMessage(SORRY, name=ORCHESTRATOR)
        # Only this message enters the thread: keep the turn's charts (shown again on reload) and
        # query results (for "plot that" later) on it.
        extra = collect(new)
        if asked := list(dict.fromkeys(r for m in new if isinstance(m, AIMessage) for c in m.tool_calls
                                       if (r := next((s.route for s in specs if c["name"] == ask_tool_name(s)), None)))):
            extra["asked"] = asked  # team mode picks critics that read other sources
        if extra:
            answer = answer.model_copy(update={"additional_kwargs": {**answer.additional_kwargs, **extra}})
        return Command(goto=END, update={"messages": [answer], "findings": "", "failed": "", "attempt": ""})

    return orchestrator, targets
