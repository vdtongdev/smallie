"""Model tiers: which tier a task difficulty gets under a profile, and which model fills a tier."""

import pytest

from app.core.llm import pick_model, pick_tier
from app.core.model_settings import ModelSettings

URL = "https://api.example.test/v1"


@pytest.mark.parametrize(
    "profile, easy, medium, hard",
    [
        ("economy", "small", "small", "standard"),
        ("balanced", "small", "standard", "strong"),
        ("advanced", "standard", "strong", "strong"),
    ],
)
def test_pick_tier_table(profile, easy, medium, hard):
    assert pick_tier("easy", profile) == easy
    assert pick_tier("medium", profile) == medium
    assert pick_tier("hard", profile) == hard


def test_pick_tier_unknown_values():
    assert pick_tier("weird", "balanced") == "standard"
    assert pick_tier("hard", "weird") == "strong"


def test_pick_model_slots():
    saved = ModelSettings(URL, "std", "k", small_model="sm", strong_model="st")
    assert pick_model(saved, "small") == ("sm", "small", "small model")
    assert pick_model(saved, "strong") == ("st", "strong", "strong model")
    assert pick_model(saved, "standard")[:2] == ("std", "standard")
    assert pick_model(saved, "weird")[:2] == ("std", "standard")


def test_pick_model_empty_slot_uses_standard():
    model, tier, reason = pick_model(ModelSettings(URL, "std", "k", strong_model="st"), "small")
    assert (model, tier) == ("std", "standard")
    assert "small" in reason


def test_existing_row_routes_everything_to_model():
    saved = ModelSettings(URL, "m", "k")
    assert saved.profile == "balanced"
    for tier in ("small", "standard", "strong"):
        assert pick_model(saved, tier)[0] == "m"
