from app.core.config import get_settings
from app.core.llm import get_chat_model


def test_fallback_models_are_tried_in_order(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "openrouter_quantizations", "")
    monkeypatch.setattr(settings, "openrouter_fallback_model", " b:free, main:free ,c:free,")
    model = get_chat_model("main:free")
    assert model.extra_body == {"models": ["main:free", "b:free", "c:free"]}
    assert model.temperature == 0

    monkeypatch.setattr(settings, "openrouter_fallback_model", "main:free")
    assert get_chat_model("main:free").extra_body is None


def test_quantizations_limit_the_providers(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "openrouter_fallback_model", "")
    monkeypatch.setattr(settings, "openrouter_quantizations", "bf16, fp8,")
    assert get_chat_model("main").extra_body == {"provider": {"quantizations": ["bf16", "fp8"]}}
    assert get_chat_model("main", quantizations="").extra_body is None  # explicit "": no filter


def test_explicit_fallbacks_replace_the_chat_fallbacks(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "openrouter_quantizations", "")
    monkeypatch.setattr(settings, "openrouter_fallback_model", "chat-b")
    # The chat fallback is ignored; the model itself is dropped from its own fallback list.
    assert get_chat_model("vision-a", fallbacks="vision-b, vision-a").extra_body == {"models": ["vision-a", "vision-b"]}
    # An empty string means "no fallbacks at all", not "use the chat ones".
    assert get_chat_model("vision-a", fallbacks="").extra_body is None
    # Not passing it keeps today's behaviour.
    assert get_chat_model("vision-a").extra_body == {"models": ["vision-a", "chat-b"]}
