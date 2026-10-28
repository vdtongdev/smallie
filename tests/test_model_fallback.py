"""A call on the small or strong model that fails the server's way repeats once on the standard model."""

from dataclasses import asdict
from types import SimpleNamespace

import httpx
import openai
import pytest
from langchain.agents.middleware import ExtendedModelResponse, ModelRequest, ModelResponse
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.chat_models import agenerate_from_stream
from langchain_core.messages import AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.tools import StructuredTool

from app.agents import factory
from app.agents.factory import fallback_category
from app.core.memory import AgentContext
from app.core.model_settings import ModelSettings

SAVED = ModelSettings("https://x/v1", "std", "k", small_model="tiny", strong_model="big")


def _error(cls, status, body=None, message="boom"):
    request = httpx.Request("POST", "https://x/v1/chat/completions")
    return cls(message, response=httpx.Response(status, request=request), body=body)


def _not_found():
    return _error(openai.NotFoundError, 404)


class Fake(BaseChatModel):
    """Streams `tokens`, then raises `error` if set, else replies with its own name."""

    model_name: str = "std"
    error: object = None
    tokens: list = []
    streaming: bool = True

    @property
    def _llm_type(self):
        return "fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise NotImplementedError

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return await agenerate_from_stream(self._astream(messages, stop, run_manager, **kwargs))

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        for token in self.tokens:
            chunk = ChatGenerationChunk(message=AIMessageChunk(content=token))
            await run_manager.on_llm_new_token(token, chunk=chunk)
            yield chunk
        if self.error:
            raise self.error
        text = f"from {self.model_name}"
        chunk = ChatGenerationChunk(message=AIMessageChunk(content=text))
        await run_manager.on_llm_new_token(text, chunk=chunk)
        yield chunk


def _setup(monkeypatch, **models):
    """get_chat_model hands out the model of the tier asked for."""
    monkeypatch.setattr(factory, "get_chat_model", lambda user_id, *, tier="standard", streaming=True: models[tier])
    monkeypatch.setattr(factory, "load_settings", lambda user_id: SAVED)


def _request(tier, state=None):
    runtime = SimpleNamespace(context=AgentContext(user_id="u", tier=tier))
    return ModelRequest(model=Fake(), messages=[HumanMessage("hi")], state=state or {}, runtime=runtime)


def _unwrap(result):
    return result.model_response if isinstance(result, ExtendedModelResponse) else result


class Handler:
    """What the model node does with a request: call its model."""

    def __init__(self):
        self.models = []

    async def __call__(self, request):
        self.models.append(request.model.model_name)
        message = await request.model.ainvoke(request.messages)
        return ModelResponse(result=[message])


def test_fallback_category_mapping():
    assert fallback_category(_not_found()) == "model_not_found"
    assert fallback_category(_error(openai.BadRequestError, 400, {"code": "context_length_exceeded"})) == "context_length"
    assert fallback_category(_error(openai.BadRequestError, 400, message="too long: context_length_exceeded")) == "context_length"
    assert fallback_category(_error(openai.BadRequestError, 400, {"message": "nope"})) == "unsupported"
    assert fallback_category(_error(openai.InternalServerError, 500)) == "server_error"
    assert fallback_category(openai.APITimeoutError(httpx.Request("POST", "https://x"))) == "server_error"
    assert fallback_category(openai.APIConnectionError(request=httpx.Request("POST", "https://x"))) == "server_error"
    assert fallback_category(_error(openai.RateLimitError, 429)) == "rate_limit"
    assert fallback_category(_error(openai.AuthenticationError, 401)) is None
    assert fallback_category(ValueError("x")) is None


async def test_small_404_repeats_on_standard(monkeypatch):
    _setup(monkeypatch, small=Fake(model_name="tiny", error=_not_found()), standard=Fake(model_name="std"))
    handler = Handler()
    result = _unwrap(await factory.user_model.awrap_model_call(_request("small"), handler))
    assert handler.models == ["tiny", "std"]
    assert result.result[-1].text == "from std"
    assert result.result[-1].response_metadata["fallback"] == {"from_model": "tiny", "category": "model_not_found"}


async def test_fallback_sticks_for_the_run(monkeypatch):
    _setup(monkeypatch, small=Fake(model_name="tiny", error=_not_found()), standard=Fake(model_name="std"))
    first = await factory.user_model.awrap_model_call(_request("small"), Handler())
    assert first.command.update == {"tier_failed": True}
    handler = Handler()
    result = _unwrap(await factory.user_model.awrap_model_call(_request("small", state=first.command.update), handler))
    assert handler.models == ["std"]
    assert "fallback" not in result.result[-1].response_metadata


async def test_fallback_sticks_through_a_real_agent_run(monkeypatch):
    """The state key survives the graph: the second model call of the run does not try the small model."""

    async def lookup(query: str) -> str:
        return "ok"

    tool = StructuredTool.from_function(coroutine=lookup, name="lookup", description="look something up")
    calls = []

    class Counting(Fake):
        def bind_tools(self, tools, **kwargs):
            return self

        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            calls.append(self.model_name)
            if self.model_name == "std" and calls.count("std") == 1:
                call = {"name": "lookup", "args": '{"query": "a"}', "id": "c1", "index": 0}
                yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[call]))
                return
            async for chunk in super()._astream(messages, stop, run_manager, **kwargs):
                yield chunk

    _setup(monkeypatch, small=Counting(model_name="tiny", error=_not_found()), standard=Counting(model_name="std"))
    agent = factory.build_agent(name="t", system_prompt="x", tools=[tool])
    result = await agent.ainvoke({"messages": [HumanMessage("go")]}, context=AgentContext(user_id="u", tier="small"))
    assert calls == ["tiny", "std", "std"]
    assert result["messages"][-1].text == "from std"


async def test_no_retry_on_standard_rate_limit_auth(monkeypatch):
    _setup(monkeypatch, small=Fake(model_name="tiny"), standard=Fake(model_name="std", error=_not_found()))
    handler = Handler()
    with pytest.raises(openai.NotFoundError):
        await factory.user_model.awrap_model_call(_request("standard"), handler)
    assert handler.models == ["std"]
    for status, cls in ((401, openai.AuthenticationError),):
        _setup(monkeypatch, small=Fake(model_name="tiny", error=_error(cls, status)), standard=Fake(model_name="std"))
        handler = Handler()
        with pytest.raises(cls):
            await factory.user_model.awrap_model_call(_request("small"), handler)
        assert handler.models == ["tiny"]


class OpenAIRateLimitError(openai.RateLimitError):
    """A subclass, like langchain_openai's."""


@pytest.mark.parametrize("cls", [openai.RateLimitError, OpenAIRateLimitError])
@pytest.mark.parametrize("tier", ["small", "strong"])
async def test_rate_limit_repeats_on_standard(monkeypatch, cls, tier):
    _setup(monkeypatch, **{tier: Fake(model_name="tiny", error=_error(cls, 429))}, standard=Fake(model_name="std"))
    handler = Handler()
    first = await factory.user_model.awrap_model_call(_request(tier), handler)
    assert handler.models == ["tiny", "std"]
    result = _unwrap(first)
    assert result.result[-1].text == "from std"
    assert result.result[-1].response_metadata["fallback"] == {"from_model": "tiny", "category": "rate_limit"}
    assert first.command.update == {"tier_failed": True}


async def test_standard_rate_limit_still_raises(monkeypatch):
    _setup(monkeypatch, standard=Fake(model_name="std", error=_error(openai.RateLimitError, 429)))
    handler = Handler()
    with pytest.raises(openai.RateLimitError):
        await factory.user_model.awrap_model_call(_request("standard"), handler)
    assert handler.models == ["std"]


async def test_no_rate_limit_fallback_after_first_token(monkeypatch):
    _setup(monkeypatch, small=Fake(model_name="tiny", tokens=["Half an"], error=_error(openai.RateLimitError, 429)),
           standard=Fake(model_name="std"))
    handler = Handler()
    with pytest.raises(openai.RateLimitError):
        await factory.user_model.awrap_model_call(_request("small"), handler)
    assert handler.models == ["tiny"]


async def test_no_retry_when_the_slot_is_empty(monkeypatch):
    """An empty slot already resolves to the standard model: its failure is the standard model's."""
    _setup(monkeypatch, small=Fake(model_name="std", error=_not_found()), standard=Fake(model_name="std"))
    monkeypatch.setattr(factory, "load_settings", lambda user_id: ModelSettings("https://x/v1", "std", "k"))
    handler = Handler()
    with pytest.raises(openai.NotFoundError):
        await factory.user_model.awrap_model_call(_request("small"), handler)
    assert handler.models == ["std"]


async def test_failure_of_repeated_call_propagates(monkeypatch):
    _setup(monkeypatch, small=Fake(model_name="tiny", error=_not_found()),
           standard=Fake(model_name="std", error=_error(openai.InternalServerError, 500)))
    handler = Handler()
    with pytest.raises(openai.InternalServerError):
        await factory.user_model.awrap_model_call(_request("small"), handler)
    assert handler.models == ["tiny", "std"]


async def test_no_fallback_after_first_token(monkeypatch):
    """The failed call already streamed part of an answer: repeating it would glue two answers together."""
    _setup(monkeypatch, small=Fake(model_name="tiny", tokens=["Half an"], error=_error(openai.InternalServerError, 500)),
           standard=Fake(model_name="std"))
    handler = Handler()
    with pytest.raises(openai.InternalServerError):
        await factory.user_model.awrap_model_call(_request("small"), handler)
    assert handler.models == ["tiny"]


async def test_model_fallback_event_is_emitted(monkeypatch):
    _setup(monkeypatch, small=Fake(model_name="tiny", error=_not_found()), standard=Fake(model_name="std"))
    events = []
    monkeypatch.setattr(factory, "get_stream_writer", lambda: events.append)
    await factory.user_model.awrap_model_call(_request("small"), Handler())
    assert events == [{"type": "model_fallback", "from_model": "tiny", "category": "model_not_found"}]


def test_context_crosses_to_agent_server():
    assert asdict(AgentContext(user_id="u", tier="strong"))["tier"] == "strong"
    assert AgentContext(user_id="u").tier == "standard"
