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


def test_openrouter_headers_only_for_openrouter(user_settings):
    user_settings["value"] = ModelSettings("https://openrouter.ai/api/v1", "m", "k")
    assert get_chat_model("u1").default_headers
    user_settings["value"] = ModelSettings("https://api.deepseek.com/v1", "m", "k")
    assert not get_chat_model("u1").default_headers
