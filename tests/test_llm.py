from app.core.config import get_settings
from app.core.llm import get_chat_model


def test_fallback_models_are_tried_in_order(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "openrouter_fallback_model", " b:free, main:free ,c:free,")
    model = get_chat_model("main:free")
    assert model.extra_body == {"models": ["main:free", "b:free", "c:free"]}
    assert model.temperature == 0

    monkeypatch.setattr(settings, "openrouter_fallback_model", "main:free")
    assert get_chat_model("main:free").extra_body is None
