from collections.abc import Callable, Sequence

from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ModelRequest,
    dynamic_prompt,
    wrap_model_call,
)
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore

from app.core.config import get_settings
from app.core.llm import get_chat_model
from app.core.memory import MEMORY_TOOLS, TOOL_SOURCE, AgentContext
from app.core.prompts import MEMORIES_SECTION, RECENT_CHATS_SECTION, USER_SECTION


@wrap_model_call
async def scope_tools(request: ModelRequest, handler):
    """Team requests limit an agent to the tools of its sources; untagged tools stay."""
    sources = request.runtime.context.sources
    if sources:
        request = request.override(tools=[
            t for t in request.tools
            if (tag := (getattr(t, "metadata", None) or {}).get(TOOL_SOURCE)) is None or tag in sources])
    return await handler(request)


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
        middleware=[prompt_with_memories, call_limit, scope_tools, *middleware],
        context_schema=AgentContext,
        checkpointer=checkpointer,
        store=store,
        name=name,
    )
