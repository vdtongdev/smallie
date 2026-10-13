from collections.abc import Sequence

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware, ModelRequest, dynamic_prompt
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore

from app.core.config import get_settings
from app.core.llm import get_chat_model
from app.core.memory import MEMORY_TOOLS, AgentContext
from app.core.prompts import MEMORIES_SECTION, RECENT_CHATS_SECTION, USER_SECTION


def build_agent(
    *,
    name: str,
    system_prompt: str,
    tools: Sequence[BaseTool],
    checkpointer: BaseCheckpointSaver | None = None,
    store: BaseStore | None = None,
):
    """An agent with the given tools plus long-term memory tools and a memory-aware prompt.

    Leave checkpointer/store unset when the agent runs as a subgraph of the assistant
    (app/orchestration/graph.py); it then uses the parent graph's.
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
        return prompt

    # Stops runaway tool loops from burning the free quota; the fallback model is handled
    # by OpenRouter inside each request (app/llm.py).
    call_limit = ModelCallLimitMiddleware(run_limit=get_settings().max_model_calls, exit_behavior="end")
    middleware = [prompt_with_memories, call_limit]

    return create_agent(
        get_chat_model(),
        tools=[*tools, *MEMORY_TOOLS],
        middleware=middleware,
        context_schema=AgentContext,
        checkpointer=checkpointer,
        store=store,
        name=name,
    )
