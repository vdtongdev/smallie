"""What the assistant needs to know about an agent to route to it and run it.

An entry is usually one `create_agent` agent, but `build` may also return a small
workflow graph; the router treats both the same. The orchestrator (app/orchestration/)
can also hand a message off to an entry or call it as a tool (`tool_description`).
"""

import re
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable


def last_answer(state: dict) -> str:
    """The text of the last AIMessage in an agent's final state."""
    return next((m.text for m in reversed(state.get("messages", [])) if isinstance(m, AIMessage) and m.text), "")


@dataclass(frozen=True)
class RouteInfo:
    """Per-request routing data for one available agent."""

    prompt_note: str = ""  # extra line for the orchestrator prompt, e.g. the user's file names
    matches: Callable[[str], bool] = lambda message: False  # message clearly points here (file name, ticker)
    # A weaker sign that the message is (partly) about this agent's data, e.g. one word of a file
    # name. It never routes here, but stops the router from sending the message to another agent alone.
    mentions: Callable[[str], bool] = lambda message: False


@dataclass(frozen=True)
class AgentSpec:
    route: str  # URL segment (/api/chat/<route>), router answer, SSE `agent` event, graph node
    description: str  # one bullet in the router prompt
    labels: dict[str, str]  # badge text per UI language: {"en": ..., "vi": ...}
    build: Callable[[], Runnable]  # compiled agent or workflow, without checkpointer/store
    message_names: tuple[str, ...]  # AIMessage.name values written by this entry
    hints: re.Pattern | None = None  # words that only fit this entry; anything ambiguous goes to the model
    # Returns None when the agent has nothing to offer this user (e.g. no documents).
    route_info: Callable[[str], Awaitable[RouteInfo | None]] | None = field(default=None)
    # Async setup awaited in the app lifespan before `build` (e.g. loading MCP tools); register
    # cleanup on the stack.
    prepare: Callable[[AsyncExitStack], Awaitable[None]] | None = None
    # Workflow steps (node names inside `build`'s graph) whose tokens are not streamed to the
    # UI, e.g. a researcher whose notes only feed the orchestrator. Their tool calls still show.
    silent_steps: tuple[str, ...] = ()
    # Workflow steps shown in the UI when they start: node name -> {"en": ..., "vi": ...}.
    steps: dict[str, dict[str, str]] = field(default_factory=dict)
    # When the orchestrator should call this entry as a tool (`ask_<route>`); empty = never.
    tool_description: str = ""
    # False: the entry only gathers material (e.g. research findings); the orchestrator writes
    # the answer, so the router sends its output on to the orchestrator and it can't be handed off to.
    answers_directly: bool = True
    # The tool result when the orchestrator calls this entry: its final state -> text.
    result: Callable[[dict], str] = last_answer
    # Routes this entry wins over when both are obvious, e.g. chart over sql for "chart AAPL".
    overrides: tuple[str, ...] = ()

    async def info(self, user_id: str) -> RouteInfo | None:
        return await self.route_info(user_id) if self.route_info else RouteInfo()

    def is_obvious(self, message: str, info: RouteInfo) -> bool:
        return bool(self.hints and self.hints.search(message)) or info.matches(message)

    def is_mentioned(self, message: str, info: RouteInfo) -> bool:
        return self.is_obvious(message, info) or info.mentions(message)


def message_routes(specs: Sequence[AgentSpec]) -> dict[str, str]:
    """AIMessage.name -> route (`create_agent(name=...)` sets the name)."""
    return {name: spec.route for spec in specs for name in spec.message_names}
