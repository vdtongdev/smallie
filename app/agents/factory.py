import asyncio
import json
import logging
from collections.abc import Callable, Sequence
from typing import NotRequired

import openai

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ExtendedModelResponse,
    ModelCallLimitMiddleware,
    ModelRequest,
    after_model,
    dynamic_prompt,
    wrap_model_call,
    wrap_tool_call,
)
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.config import get_stream_writer
from langgraph.errors import GraphBubbleUp
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore
from langgraph.types import Command

from app.core.config import get_settings
from app.core.llm import get_chat_model, pick_model, placeholder_model
from app.core.memory import MEMORY_TOOLS, TEAM_MODE, TOOL_SOURCE, AgentContext
from app.core.model_settings import load_settings
from app.core.prompts import LAST_CALL_NOTE, MEMORIES_SECTION, RECENT_CHATS_SECTION, SAME_RESULT, USER_SECTION, reply_language

log = logging.getLogger(__name__)

# Tool calls of one model reply that may run; the rest are dropped.
MAX_TOOL_CALLS_PER_STEP = 8


def call_key(call: dict) -> tuple[str, str]:
    """A tool call ignoring case, spacing and argument order."""
    args = json.dumps(call["args"], sort_keys=True, ensure_ascii=False)
    return call["name"], " ".join(args.lower().split())


def _in_scope(tool, sources) -> bool:
    tag = (getattr(tool, "metadata", None) or {}).get(TOOL_SOURCE)
    return not sources or tag is None or tag in sources


def fallback_category(exc: BaseException) -> str | None:
    """Why a failed call may repeat on the standard model; None for errors that would fail there too
    (bad key) or that are not the model's (call budget, bugs). A rate limit is per model upstream, so
    the standard model can still answer."""
    if isinstance(exc, openai.RateLimitError):
        return "rate_limit"
    if isinstance(exc, openai.NotFoundError):
        return "model_not_found"
    if isinstance(exc, openai.BadRequestError):
        return "context_length" if "context_length_exceeded" in f"{exc.code} {exc.message}" else "unsupported"
    if isinstance(exc, (openai.InternalServerError, openai.APITimeoutError, openai.APIConnectionError)):
        return "server_error"
    return None


class _TokenSeen(AsyncCallbackHandler):
    """Notes that a model call streamed output."""

    seen = False

    async def on_llm_new_token(self, token, **kwargs):
        self.seen = True


class TierState(AgentState):
    tier_failed: NotRequired[bool]  # a call of this run fell back to the standard model: later calls start there


class UserModel(AgentMiddleware[TierState, AgentContext]):
    """Agents are built once at startup; the model is the calling user's, chosen per model call from
    the run's tier. A small or strong model that fails the server's way is repeated once on the
    standard model, and the run stays there."""

    state_schema = TierState

    async def awrap_model_call(self, request: ModelRequest, handler):
        context = request.runtime.context
        tier = "standard" if request.state.get("tier_failed") else context.tier
        model = await asyncio.to_thread(get_chat_model, context.user_id, tier=tier)
        if tier == "standard":
            return await handler(request.override(model=model))
        saved = await asyncio.to_thread(load_settings, context.user_id)
        if saved is None or pick_model(saved, tier)[1] == "standard":
            return await handler(request.override(model=model))  # an empty slot is the standard model already
        tokens = _TokenSeen()
        try:
            return await handler(request.override(model=model.model_copy(update={"callbacks": [tokens]})))
        except Exception as e:
            category = fallback_category(e)
            if category is None or tokens.seen:
                raise
            log.warning("Model call on the %s tier fell back to standard: %s (HTTP %s)",
                        tier, category, getattr(e, "status_code", None))
        standard = await asyncio.to_thread(get_chat_model, context.user_id)
        response = await handler(request.override(model=standard))
        from_model, to_model = getattr(model, "model_name", ""), getattr(standard, "model_name", "")
        message = response.result[-1] if response.result else None
        if isinstance(message, AIMessage):
            message.response_metadata["fallback"] = {"from_model": from_model, "to_model": to_model, "category": category}
        try:
            get_stream_writer()({"type": "model_fallback", "from_model": from_model, "to_model": to_model, "category": category})
        except RuntimeError:
            pass  # not inside a streamed run
        return ExtendedModelResponse(model_response=response, command=Command(update={"tier_failed": True}))


user_model = UserModel()


@after_model
def trim_tool_calls(state, runtime):
    """Keeps one reply from running a flood of tool calls: drops calls identical to an earlier call
    of the same reply, then keeps the first MAX_TOOL_CALLS_PER_STEP. A reply that repeated itself once
    held 1,392 calls of 6 distinct queries, and all of them ran at once."""
    message = state["messages"][-1]
    if not isinstance(message, AIMessage) or not message.tool_calls:
        return None
    seen, kept = set(), []
    for call in message.tool_calls:
        key = call_key(call)
        if key not in seen:
            seen.add(key)
            kept.append(call)
    kept = kept[:MAX_TOOL_CALLS_PER_STEP]
    if len(kept) == len(message.tool_calls):
        return None
    log.warning("Kept %d of %d tool calls of one model reply", len(kept), len(message.tool_calls))
    return {"messages": [message.model_copy(update={"tool_calls": kept})]}


def make_last_call(limit: int):
    """The last model call the limit allows writes the answer: no tool choice, a note naming the
    reply language (in the request only, not saved), and any tool call the model makes anyway is dropped."""

    @wrap_model_call
    async def last_call(request: ModelRequest, handler):
        if request.state.get("run_model_call_count", 0) != limit - 1:
            return await handler(request)
        note = HumanMessage(LAST_CALL_NOTE.format(language=reply_language(request.messages)))
        response = await handler(request.override(tool_choice="none", messages=[*request.messages, note]))
        message = response.result[-1] if response.result else None
        if isinstance(message, AIMessage) and message.tool_calls:
            response.result[-1] = message.model_copy(update={"tool_calls": [], "invalid_tool_calls": []})
        return response

    return last_call


@wrap_model_call
async def scope_tools(request: ModelRequest, handler):
    """Team requests limit an agent to the tools of its sources; untagged tools stay."""
    context = request.runtime.context
    return await handler(request.override(
        tools=[t for t in request.tools if _in_scope(t, context.sources) and t.name not in context.deny_tools]))


@wrap_model_call
async def source_first(request: ModelRequest, handler):
    """In a team request an agent with source tools in scope must call one on its first model call
    of the turn, so it cannot answer without looking in its sources. A request without scope (the
    orchestrator) or without a source tool (the drawer) is left free. The tool is named when there
    is only one, else any tool is required. A call already barred from tools stays barred."""
    if request.tool_choice == "none":
        return await handler(request)
    context = request.runtime.context
    last_human = max((i for i, m in enumerate(request.messages) if isinstance(m, HumanMessage)), default=-1)
    first_call = not any(isinstance(m, ToolMessage) for m in request.messages[last_human + 1:])
    names = [t.name for t in request.tools if (getattr(t, "metadata", None) or {}).get(TOOL_SOURCE)
             and _in_scope(t, context.sources)]
    if context.agent != TEAM_MODE or not context.sources or not first_call or not names:
        return await handler(request)
    return await handler(request.override(tool_choice=names[0] if len(names) == 1 else "required"))


@wrap_tool_call
async def refuse_hidden_tools(request, handler):
    """The model may call a tool it was not offered (the prompt names it); the tool node would
    still run it, so refuse here."""
    context = request.runtime.context
    if not _in_scope(request.tool, context.sources) or request.tool_call["name"] in context.deny_tools:
        return ToolMessage(f"Tool {request.tool_call['name']} is not available for this request.",
                           tool_call_id=request.tool_call["id"], name=request.tool_call["name"], status="error")
    return await handler(request)


@wrap_tool_call
async def tool_errors(request, handler):
    """A tool that raises comes back to the model as an error message it can correct, instead of
    ending the run. Interrupts and handoffs (GraphBubbleUp) and cancellation pass through."""
    try:
        return await handler(request)
    except GraphBubbleUp:
        raise
    except Exception as e:
        return ToolMessage(f"Tool {request.tool_call['name']} failed: {e}"[:500],
                           tool_call_id=request.tool_call["id"], name=request.tool_call["name"], status="error")


@wrap_tool_call
async def same_memory_results(request, handler):
    """A memory search whose result equals an earlier result of the same tool in this run (after the
    last user message) comes back as a short note: the model reads the same text only once. Equal
    means the same set of lines, in any order."""
    name = request.tool_call["name"]
    result = await handler(request)
    if name not in {t.name for t in MEMORY_TOOLS} or not isinstance(result, ToolMessage):
        return result
    messages = request.state["messages"]
    last_human = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1)
    if any(isinstance(m, ToolMessage) and m.name == name
           and set(m.text.split("\n")) == set(result.text.split("\n")) for m in messages[last_human + 1:]):
        return ToolMessage(SAME_RESULT.format(name=name), tool_call_id=request.tool_call["id"], name=name)
    return result


def build_agent(
    *,
    name: str,
    system_prompt: str,
    tools: Sequence[BaseTool],
    checkpointer: BaseCheckpointSaver | None = None,
    store: BaseStore | None = None,
    max_model_calls: int | None = None,
    prompt_extra: Callable[[ModelRequest], str] | None = None,
    middleware: Sequence[AgentMiddleware] = (),
    last_call_writes: bool = True,
):
    """An agent with the given tools plus long-term memory tools and a memory-aware prompt.

    Leave checkpointer/store unset when the agent runs as a subgraph of the assistant
    (app/orchestration/graph.py); it then uses the parent graph's. `prompt_extra(request)` is appended to the prompt on every model call;
    `middleware` runs after the built-in ones.
    """

    @dynamic_prompt
    def prompt_with_memories(request: ModelRequest) -> str:
        context = request.runtime.context
        prompt = system_prompt
        if context.user:
            prompt += USER_SECTION.format(user=context.user)
        prompt += MEMORIES_SECTION.format(memories=context.memories or "(nothing yet)")
        if context.recent_chats:
            prompt += RECENT_CHATS_SECTION.format(recent_chats=context.recent_chats)
        if prompt_extra:
            prompt += prompt_extra(request)
        return prompt

    # Stops runaway tool loops from burning the user's quota.
    limit = max_model_calls or get_settings().max_model_calls
    call_limit = ModelCallLimitMiddleware(run_limit=limit, exit_behavior="end")
    return create_agent(
        placeholder_model(),
        tools=[*tools, *MEMORY_TOOLS],
        middleware=[user_model, prompt_with_memories, *([make_last_call(limit)] if last_call_writes else []), call_limit, trim_tool_calls, scope_tools, source_first, tool_errors, same_memory_results, refuse_hidden_tools,
                    *middleware],
        context_schema=AgentContext,
        checkpointer=checkpointer,
        store=store,
        name=name,
    )
