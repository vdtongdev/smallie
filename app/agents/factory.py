from collections.abc import Callable, Sequence

from langchain.agents import create_agent
from langchain.agents.middleware import (AgentMiddleware, ModelCallLimitMiddleware, ModelRequest, dynamic_prompt,
                                         wrap_model_call)
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore

from app.agents.base import turn_messages
from app.core.config import get_settings
from app.core.llm import get_chat_model
from app.core.memory import MEMORY_TOOLS, AgentContext
from app.core.prompts import MEMORIES_SECTION, RECENT_CHATS_SECTION, USER_SECTION


def search_first(tool_name: str) -> AgentMiddleware:
    """Makes the first model call of each turn call `tool_name`: weak models skip a search the prompt
    requires and answer from their own knowledge (AP-006). Later calls of the turn choose freely."""

    @wrap_model_call
    async def search_first(request: ModelRequest, handler):
        if not any(isinstance(m, ToolMessage) for m in turn_messages(request.messages)):
            request = request.override(tool_choice=tool_name)
        return await handler(request)

    return search_first


def build_agent(
    *,
    name: str,
    system_prompt: str,
    tools: Sequence[BaseTool],
    checkpointer: BaseCheckpointSaver | None = None,
    store: BaseStore | None = None,
    max_model_calls: int | None = None,
    model: str | None = None,
    prompt_extra: Callable[[ModelRequest], str] | None = None,
    middleware: Sequence[AgentMiddleware] = (),
):
    """An agent with the given tools plus long-term memory tools and a memory-aware prompt.

    Leave checkpointer/store unset when the agent runs as a subgraph of the assistant
    (app/orchestration/graph.py); it then uses the parent graph's. `model` overrides
    OPENROUTER_CHAT_MODEL; `prompt_extra(request)` is appended to the prompt on every model call;
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

    # Stops runaway tool loops from burning the free quota; the fallback model is handled
    # by OpenRouter inside each request (app/llm.py).
    call_limit = ModelCallLimitMiddleware(
        run_limit=max_model_calls or get_settings().max_model_calls, exit_behavior="end"
    )
    return create_agent(
        get_chat_model(model),
        tools=[*tools, *MEMORY_TOOLS],
        middleware=[prompt_with_memories, call_limit, *middleware],
        context_schema=AgentContext,
        checkpointer=checkpointer,
        store=store,
        name=name,
    )
