"""Tokens and cost (USD, as OpenRouter bills it) of the chat models, totalled per user.

Embedding calls (memories, chat history, documents) are not counted.
"""

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import LLMResult
from langgraph.store.base import BaseStore

TOTAL = "total"


def usage_namespace(user_id: str) -> tuple[str, ...]:
    return ("users", user_id, "usage")


class UsageCounter(AsyncCallbackHandler):
    """Sums the model calls of one chat request. Passed in the run's `callbacks`, so it also sees
    agents running inside other agents (the orchestrator's `ask_<route>` tools, the researcher)."""

    def __init__(self):
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost = 0.0

    async def on_llm_end(self, response: LLMResult, **kwargs) -> None:
        for generations in response.generations:
            for generation in generations:
                message = getattr(generation, "message", None)
                if message is not None:
                    self.count(message)

    def count(self, message: BaseMessage) -> None:
        """Add one model reply; also used for replies of remote agents, whose calls this
        counter never sees (app/agents/remote.py)."""
        usage = getattr(message, "usage_metadata", None) or {}
        self.input_tokens += usage.get("input_tokens", 0)
        self.output_tokens += usage.get("output_tokens", 0)
        self.cost += message.response_metadata.get("cost") or 0.0


async def get_usage(store: BaseStore, user_id: str) -> dict:
    item = await store.aget(usage_namespace(user_id), TOTAL)
    return {"input_tokens": 0, "output_tokens": 0, "cost": 0.0, **(item.value if item else {})}


async def add_usage(store: BaseStore, user_id: str, counter: UsageCounter) -> None:
    if not (counter.input_tokens or counter.output_tokens or counter.cost):
        return
    # shortcut: read-modify-write, so two of one user's chats finishing at the same moment can lose
    # one turn's numbers; move to a table with `UPDATE ... SET n = n + :x` if that ever matters.
    total = await get_usage(store, user_id)
    total = {key: total[key] + getattr(counter, key) for key in ("input_tokens", "output_tokens", "cost")}
    await store.aput(usage_namespace(user_id), TOTAL, total, index=False)
