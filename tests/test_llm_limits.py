from app.core import llm
from app.core.config import get_settings


def test_chat_models_cap_their_output_tokens(monkeypatch):
    monkeypatch.setattr(llm, "check_endpoint", lambda url: None)
    llm._build_chat_model.cache_clear()
    model = llm._build_chat_model("https://openrouter.ai/api/v1", "m", "k", True)
    assert model.max_tokens == get_settings().max_output_tokens
    assert type(get_settings()).model_fields["max_output_tokens"].default == 8192
