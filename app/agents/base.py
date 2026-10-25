"""What the assistant needs to know about an agent to route to it and run it.

An entry is usually one `create_agent` agent, but `build` may also return a small
workflow graph; the router treats both the same. The orchestrator (app/orchestration/)
can also hand a message off to an entry or call it as a tool (`tool_description`).
"""

import re
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Literal

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, ToolMessage
from langchain_core.runnables import Runnable

from app.core.memory import MEMORY_TOOLS

LIMIT_MESSAGE = "Model call limits exceeded"  # ModelCallLimitMiddleware's closing message
MEMORY_TOOL_NAMES = frozenset(t.name for t in MEMORY_TOOLS)
TOOL_RESULT_PREVIEW = 4000  # characters of a tool result shown in the UI and kept in a team transcript


class ToolRecorder(AsyncCallbackHandler):
    """Records every tool run of one request as `{name, args, result}` (result capped), in start
    order, across nested runs (a workflow's inner agent), without the memory tools. Passed in the
    run's `callbacks`, like `UsageCounter`."""

    def __init__(self):
        self.runs: dict = {}

    async def on_tool_start(self, serialized, input_str, *, run_id, inputs=None, **kwargs) -> None:
        name = kwargs.get("name") or (serialized or {}).get("name", "")
        self.runs[run_id] = {"name": name, "args": inputs if isinstance(inputs, dict) else {"input": input_str}, "result": ""}

    async def on_tool_end(self, output, *, run_id, **kwargs) -> None:
        if run := self.runs.get(run_id):
            run["result"] = (output.text if isinstance(output, ToolMessage) else str(output))[:TOOL_RESULT_PREVIEW]

    async def on_tool_error(self, error, *, run_id, **kwargs) -> None:
        if run := self.runs.get(run_id):
            run["result"] = f"error: {error}"[:TOOL_RESULT_PREVIEW]

    @property
    def tools(self) -> list[dict]:
        return [r for r in self.runs.values() if r["name"] not in MEMORY_TOOL_NAMES]
GAVE_UP_CHARS = 600  # longer replies count as answers (often partial ones), whatever they say
# A reply that only says the search came up empty (not a negative answer such as "pays no dividend").
NOT_FOUND = re.compile(
    r"\b(couldn't|could not|can't|cannot|can not|wasn't able to|was unable to|unable to|did not|didn't) "
    r"(find|locate|retrieve)\b"
    r"|\bno (relevant |matching |specific )?(information|info|passages|mention)\b"
    r"|\b(isn't|is not|wasn't|was not|aren't|are not) mentioned\b"
    r"|\b(doesn't|does not|don't|do not) (seem to )?mention\b"
    r"|không (thể )?(tìm thấy|tìm được)|chưa tìm thấy|không có thông tin|không (được )?(đề cập|nhắc đến)",
    re.IGNORECASE,
)


def last_answer(state: dict) -> str:
    """The text of the last AIMessage in an agent's final state."""
    return next((m.text for m in reversed(state.get("messages", [])) if isinstance(m, AIMessage) and m.text), "")


def turn_messages(messages: Sequence[AnyMessage]) -> list[AnyMessage]:
    """The messages after the latest user message: one entry's reply to it."""
    start = next((i for i in range(len(messages) - 1, -1, -1) if isinstance(messages[i], HumanMessage)), -1)
    return list(messages[start + 1:])


def is_error(text: str) -> bool:
    return text.startswith("Error")


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
    # A tool result of this entry that holds nothing to answer from (an error, no rows, no passages).
    no_result: Callable[[str], bool] = is_error
    # What this entry can check when asked to critique another agent's draft; empty = never asked.
    review: str = ""
    # Data sources it reads (e.g. "market_db", "web"); a critic must read other ones than the author.
    sources: frozenset[str] = frozenset()
    # Team mode: "mentioned" = joins when the message points to it; "with_others" = also joins whenever another agent does.
    joins: Literal["mentioned", "with_others"] = "mentioned"

    async def info(self, user_id: str) -> RouteInfo | None:
        return await self.route_info(user_id) if self.route_info else RouteInfo()

    def is_obvious(self, message: str, info: RouteInfo) -> bool:
        return bool(self.hints and self.hints.search(message)) or info.matches(message)

    def is_mentioned(self, message: str, info: RouteInfo) -> bool:
        return self.is_obvious(message, info) or info.mentions(message)

    def gave_up(self, messages: Sequence[AnyMessage]) -> bool:
        """True when this entry's reply (`turn_messages`) has no answer: no final text (e.g. it
        ran out of model calls), every tool result held nothing, or a short reply that only says
        nothing was found."""
        last = messages[-1] if messages else None
        if not isinstance(last, AIMessage) or last.tool_calls or not last.text or last.text.startswith(LIMIT_MESSAGE):
            return True
        results = [m for m in messages if isinstance(m, ToolMessage) and m.name not in MEMORY_TOOL_NAMES]
        if results and all(m.status == "error" or self.no_result(m.text) for m in results):
            return True
        return len(last.text) <= GAVE_UP_CHARS and bool(NOT_FOUND.search(last.text))


def message_routes(specs: Sequence[AgentSpec]) -> dict[str, str]:
    """AIMessage.name -> route (`create_agent(name=...)` sets the name)."""
    return {name: spec.route for spec in specs for name in spec.message_names}
