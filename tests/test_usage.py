from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, LLMResult
from langgraph.store.memory import InMemoryStore

from app.core.llm import OpenRouterChat
from app.core.usage import UsageCounter, add_usage, get_usage


def test_streamed_usage_chunk_keeps_the_cost():
    chunk = {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15, "cost": 0.002}}
    generation = OpenRouterChat(model="m", api_key="k")._convert_chunk_to_generation_chunk(chunk, AIMessageChunk, None)
    assert generation.message.usage_metadata["input_tokens"] == 10
    assert generation.message.response_metadata["cost"] == 0.002


async def test_counter_sums_calls_and_store_keeps_totals():
    def call(inp, out, cost):
        message = AIMessage("hi", usage_metadata={"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out},
                            response_metadata={"cost": cost} if cost is not None else {})
        return LLMResult(generations=[[ChatGeneration(message=message)]])

    counter = UsageCounter()
    await counter.on_llm_end(call(100, 20, 0.01))
    await counter.on_llm_end(call(50, 5, None))  # free model: no cost
    assert (counter.input_tokens, counter.output_tokens, counter.cost) == (150, 25, 0.01)

    store = InMemoryStore()
    assert await get_usage(store, "u1") == {"input_tokens": 0, "output_tokens": 0, "cost": 0.0}
    await add_usage(store, "u1", counter)
    await add_usage(store, "u1", counter)
    assert await get_usage(store, "u1") == {"input_tokens": 300, "output_tokens": 50, "cost": 0.02}
    assert (await get_usage(store, "u2"))["input_tokens"] == 0


async def test_unknown_cost_is_none():
    counter = UsageCounter()
    assert counter.totals()["cost"] is None
    free = AIMessage("hi", usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2})
    counter.count(free)
    assert counter.totals()["cost"] is None
    counter.count(AIMessage("hi", response_metadata={"cost": 0.0}))
    assert counter.totals()["cost"] == 0.0  # a reported zero is known
    other = UsageCounter()
    other.add({"model_calls": 1, "cost": None})
    assert other.totals()["cost"] is None
    other.add({"model_calls": 1, "cost": 0.5})
    assert other.totals()["cost"] == 0.5
