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


def _reply(inp, out, cache_read=None):
    usage = {"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out}
    if cache_read is not None:
        usage["input_token_details"] = {"cache_read": cache_read}
    return AIMessage("hi", usage_metadata=usage)

def test_counter_sums_cached_tokens():
    counter = UsageCounter()
    counter.count(_reply(100, 5, cache_read=80))
    counter.count(_reply(50, 5, cache_read=0))
    assert counter.totals()["cached_tokens"] == 80

def test_cached_tokens_absent_when_no_reply_reports_them():
    counter = UsageCounter()
    counter.count(_reply(100, 5))
    assert "cached_tokens" not in counter.totals()

def test_reported_zero_cache_is_known():
    counter = UsageCounter()
    counter.count(_reply(100, 5, cache_read=0))
    assert counter.totals()["cached_tokens"] == 0

def test_add_sums_cached_tokens_and_ignores_servers_without_them():
    counter = UsageCounter()
    counter.add({"model_calls": 1, "input_tokens": 10, "output_tokens": 1})  # old agent server
    assert "cached_tokens" not in counter.totals()
    counter.add({"model_calls": 1, "input_tokens": 10, "output_tokens": 1, "cached_tokens": 4})
    counter.count(_reply(20, 1, cache_read=6))
    assert counter.totals()["cached_tokens"] == 10

def test_streamed_usage_chunk_keeps_cached_tokens():
    chunk = {"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
                                      "prompt_tokens_details": {"cached_tokens": 6}}}
    generation = OpenRouterChat(model="m", api_key="k")._convert_chunk_to_generation_chunk(chunk, AIMessageChunk, None)
    assert generation.message.usage_metadata["input_token_details"]["cache_read"] == 6


def _named(model, inp, out, cache_read=None):
    message = _reply(inp, out, cache_read)
    message.response_metadata = {"model_name": model}
    return message


def test_counter_keeps_numbers_per_model():
    counter = UsageCounter()
    counter.count(_named("a/one", 100, 5, cache_read=80))
    counter.count(_named("a/one", 50, 5, cache_read=0))
    counter.count(_named("b/two", 30, 2))
    by_model = counter.totals()["by_model"]
    assert by_model["a/one"] == {"model_calls": 2, "input_tokens": 150, "output_tokens": 10, "cached_tokens": 80}
    assert by_model["b/two"] == {"model_calls": 1, "input_tokens": 30, "output_tokens": 2}  # never reported cache: unknown


def test_reply_without_model_name_counts_as_unknown():
    counter = UsageCounter()
    counter.count(_reply(10, 1))
    assert counter.totals()["by_model"]["unknown"]["model_calls"] == 1


def test_add_merges_by_model_and_accepts_totals_without_it():
    counter = UsageCounter()
    counter.count(_named("a/one", 10, 1, cache_read=4))
    counter.add({"model_calls": 1, "input_tokens": 5, "output_tokens": 1})  # old agent server
    counter.add({"model_calls": 2, "input_tokens": 20, "output_tokens": 2, "by_model": {
        "a/one": {"model_calls": 1, "input_tokens": 12, "output_tokens": 1, "cached_tokens": 6},
        "c/three": {"model_calls": 1, "input_tokens": 8, "output_tokens": 1}}})
    by_model = counter.totals()["by_model"]
    assert by_model["a/one"] == {"model_calls": 2, "input_tokens": 22, "output_tokens": 2, "cached_tokens": 10}
    assert by_model["c/three"] == {"model_calls": 1, "input_tokens": 8, "output_tokens": 1}
    assert counter.totals()["input_tokens"] == 35


def test_totals_have_no_by_model_when_nothing_was_counted_per_model():
    assert "by_model" not in UsageCounter().totals()
