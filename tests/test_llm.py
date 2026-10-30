from types import SimpleNamespace

import pytest

from app.core import llm
from app.core.config import get_settings
from app.core.endpoint import UnsafeEndpoint
from app.core.llm import NoModelSettings, get_chat_model
from app.core.model_settings import ModelSettings


@pytest.fixture(autouse=True)
def fresh_cache():
    llm._build_chat_model.cache_clear()
    yield
    llm._build_chat_model.cache_clear()


@pytest.fixture
def user_settings(monkeypatch):
    """Patch the user's saved settings; returns a dict to change them."""
    state = {"value": ModelSettings("https://api.deepseek.com/v1", "deepseek-chat", "sk-user")}
    monkeypatch.setattr(llm, "load_settings", lambda user_id: state["value"])
    monkeypatch.setattr(llm, "check_endpoint", lambda url: None)
    return state


def test_no_settings_raises_and_never_uses_the_embed_key(monkeypatch):
    monkeypatch.setattr(get_settings(), "embed_api_key", "sk-or-operator")
    monkeypatch.setattr(llm, "load_settings", lambda user_id: None)
    with pytest.raises(NoModelSettings, match="Set your API key in Settings"):
        get_chat_model("u1")


def test_client_uses_the_users_endpoint_key_and_model(user_settings):
    model = get_chat_model("u1")
    assert model.openai_api_base == "https://api.deepseek.com/v1"
    assert model.model_name == "deepseek-chat"
    assert model.openai_api_key.get_secret_value() == "sk-user"
    assert model.temperature == get_settings().chat_temperature
    assert model.extra_body is None


def test_unsafe_endpoint_is_not_used(user_settings, monkeypatch):
    def refuse(url):
        raise UnsafeEndpoint("private address")

    monkeypatch.setattr(llm, "check_endpoint", refuse)
    with pytest.raises(UnsafeEndpoint):
        get_chat_model("u1")


def test_changed_key_gets_a_new_client(user_settings):
    first = get_chat_model("u1")
    assert get_chat_model("u1") is first
    user_settings["value"] = ModelSettings("https://api.deepseek.com/v1", "deepseek-chat", "sk-new")
    second = get_chat_model("u1")
    assert second is not first
    assert second.openai_api_key.get_secret_value() == "sk-new"


def test_tier_selects_the_slot_model(user_settings):
    user_settings["value"] = ModelSettings("https://api.deepseek.com/v1", "std", "k", small_model="sm", strong_model="st")
    assert get_chat_model("u1", tier="strong").model_name == "st"
    assert get_chat_model("u1", tier="small").model_name == "sm"


def test_no_tier_is_standard(user_settings):
    user_settings["value"] = ModelSettings("https://api.deepseek.com/v1", "std", "k", small_model="sm", strong_model="st")
    assert get_chat_model("u1").model_name == "std"


def test_openrouter_headers_only_for_openrouter(user_settings):
    user_settings["value"] = ModelSettings("https://openrouter.ai/api/v1", "m", "k")
    assert get_chat_model("u1").default_headers
    user_settings["value"] = ModelSettings("https://api.deepseek.com/v1", "m", "k")
    assert not get_chat_model("u1").default_headers


def _structured(result):
    """A model whose structured output returns `result` (or raises it); `calls` counts the model calls."""
    calls, schemas = [], []

    async def ainvoke(messages, config=None):
        calls.append((messages, config))
        if isinstance(result, Exception):
            raise result
        return result

    model = SimpleNamespace(with_structured_output=lambda schema, method: schemas.append((schema, method)) or SimpleNamespace(ainvoke=ainvoke))
    return model, calls, schemas


async def test_ask_structured_returns_the_parsed_object_after_one_call():
    model, calls, schemas = _structured(obj := object())
    config = {"callbacks": []}
    assert await llm.ask_structured(model, dict, ["m"], config) is obj
    assert calls == [(["m"], config)] and schemas == [(dict, "function_calling")]


async def test_ask_structured_returns_none_when_the_model_made_no_tool_call():
    model, calls, _ = _structured(None)
    assert await llm.ask_structured(model, dict, ["m"], {}) is None
    assert len(calls) == 1


async def test_ask_structured_lets_errors_propagate():
    model, _, _ = _structured(RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        await llm.ask_structured(model, dict, ["m"], {})
