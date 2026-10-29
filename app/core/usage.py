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
        self.model_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost = 0.0
        self.cost_known = False  # a reply reported a cost (0.0 is a cost; a missing field is not)
        self.cached_tokens = 0
        self.cache_known = False  # a reply reported cached input tokens (0 is a count; a missing field is not)

    async def on_llm_end(self, response: LLMResult, **kwargs) -> None:
        for generations in response.generations:
            for generation in generations:
                message = getattr(generation, "message", None)
                if message is not None:
                    self.count(message)

    def count(self, message: BaseMessage) -> None:
        """Add one model reply."""
        usage = getattr(message, "usage_metadata", None) or {}
        self.model_calls += 1
        self.input_tokens += usage.get("input_tokens", 0)
        self.output_tokens += usage.get("output_tokens", 0)
        self.add_cost(message.response_metadata.get("cost"))
        details = usage.get("input_token_details") or {}
        if "cache_read" in details:
            self.add_cached(details["cache_read"])

    def add_cached(self, tokens: int | None) -> None:
        if tokens is not None:
            self.cached_tokens, self.cache_known = self.cached_tokens + tokens, True

    def add_cost(self, cost: float | None) -> None:
        if cost is not None:
            self.cost, self.cost_known = self.cost + cost, True

    def totals(self) -> dict:
        totals = {"model_calls": self.model_calls, "input_tokens": self.input_tokens,
                  "output_tokens": self.output_tokens, "cost": self.cost if self.cost_known else None}
        if self.cache_known:
            totals["cached_tokens"] = self.cached_tokens
        return totals

    def add(self, totals: dict) -> None:
        """Add another counter's totals: what an agent server reports for its run, whose model
        calls this process never sees (app/agents/remote.py)."""
        for key in ("model_calls", "input_tokens", "output_tokens"):
            setattr(self, key, getattr(self, key) + (totals.get(key) or 0))
        self.add_cost(totals.get("cost"))
        self.add_cached(totals.get("cached_tokens"))


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
