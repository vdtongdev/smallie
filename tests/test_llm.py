from app.core.config import get_settings
from app.core.llm import get_chat_model


def test_fallback_models_are_tried_in_order(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "openrouter_quantizations", "")
    monkeypatch.setattr(settings, "openrouter_provider_sort", "")
    monkeypatch.setattr(settings, "openrouter_fallback_model", " b:free, main:free ,c:free,")
    model = get_chat_model("main:free")
    assert model.extra_body == {"models": ["main:free", "b:free", "c:free"]}
    assert model.temperature == 0

    monkeypatch.setattr(settings, "openrouter_fallback_model", "main:free")
    assert get_chat_model("main:free").extra_body is None


def test_quantizations_limit_the_providers(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "openrouter_fallback_model", "")
    monkeypatch.setattr(settings, "openrouter_provider_sort", "")
    monkeypatch.setattr(settings, "openrouter_quantizations", "bf16, fp8,")
    assert get_chat_model("main").extra_body == {"provider": {"quantizations": ["bf16", "fp8"]}}


def test_provider_sort_joins_the_provider_preferences(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "openrouter_fallback_model", "")
    monkeypatch.setattr(settings, "openrouter_quantizations", "")
    monkeypatch.setattr(settings, "openrouter_provider_sort", "throughput")
    assert get_chat_model("main").extra_body == {"provider": {"sort": "throughput"}}

    monkeypatch.setattr(settings, "openrouter_quantizations", "bf16,fp8")
    assert get_chat_model("main").extra_body == {
        "provider": {"quantizations": ["bf16", "fp8"], "sort": "throughput"}
    }
