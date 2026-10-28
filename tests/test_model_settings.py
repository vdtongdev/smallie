"""Per-user model settings (app/core/model_settings.py) against the real Postgres."""

import logging
import uuid

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import delete, select, text

from app.core import model_settings
from app.core.config import get_settings
from app.core.db import get_engine, init_db, users
from app.core.db import model_settings as table
from app.core.model_settings import ModelSettings
from tests.conftest import requires_db

pytestmark = requires_db

URL = "https://api.example.test/v1"


@pytest.fixture
def secret(monkeypatch):
    monkeypatch.setattr(get_settings(), "model_key_secret", Fernet.generate_key().decode())


@pytest.fixture
def make_user(secret):
    init_db()
    created = []

    def make() -> str:
        user_id = f"test-model-{uuid.uuid4()}"
        with get_engine().begin() as conn:
            conn.execute(users.insert().values(id=user_id, display_name="Test"))
        created.append(user_id)
        return user_id

    yield make
    with get_engine().begin() as conn:
        conn.execute(delete(users).where(users.c.id.in_(created)))


def raw_key(user_id: str) -> str:
    with get_engine().connect() as conn:
        return conn.execute(select(table.c.api_key).where(table.c.user_id == user_id)).scalar()


def test_saved_key_is_encrypted_at_rest(make_user):
    uid = make_user()
    model_settings.save_settings(uid, URL, "m1", "sk-plainkey1234")
    assert raw_key(uid) != "sk-plainkey1234"
    assert "plainkey" not in raw_key(uid)
    assert model_settings.load_settings(uid) == ModelSettings(URL, "m1", "sk-plainkey1234")


def test_omitted_key_keeps_the_stored_one(make_user):
    uid = make_user()
    model_settings.save_settings(uid, URL, "m1", "sk-plainkey1234")
    model_settings.save_settings(uid, URL + "/2", "m2", None)
    assert model_settings.load_settings(uid) == ModelSettings(URL + "/2", "m2", "sk-plainkey1234")


def test_first_save_without_key_is_refused(make_user):
    uid = make_user()
    with pytest.raises(ValueError, match="API key required"):
        model_settings.save_settings(uid, URL, "m1", None)
    assert model_settings.load_settings(uid) is None


def test_settings_are_per_user(make_user):
    a, b = make_user(), make_user()
    model_settings.save_settings(a, URL, "ma", "key-a")
    assert model_settings.load_settings(b) is None
    model_settings.save_settings(b, URL, "mb", "key-b")
    assert model_settings.load_settings(a).api_key == "key-a"
    model_settings.delete_settings(a)
    assert model_settings.load_settings(a) is None
    assert model_settings.load_settings(b).api_key == "key-b"


def test_wrong_secret_reads_as_no_settings(make_user, monkeypatch, caplog):
    uid = make_user()
    model_settings.save_settings(uid, URL, "m1", "sk-plainkey1234")
    token = raw_key(uid)
    monkeypatch.setattr(get_settings(), "model_key_secret", Fernet.generate_key().decode())
    with caplog.at_level(logging.WARNING):
        assert model_settings.load_settings(uid) is None
    assert uid in caplog.text
    assert token not in caplog.text and "plainkey" not in caplog.text and token[:20] not in caplog.text


def test_deleting_the_account_deletes_the_settings(make_user):
    uid = make_user()
    model_settings.save_settings(uid, URL, "m1", "key")
    with get_engine().begin() as conn:
        conn.execute(delete(users).where(users.c.id == uid))
    with get_engine().connect() as conn:
        assert conn.execute(select(table).where(table.c.user_id == uid)).first() is None


def test_new_columns_round_trip(make_user):
    uid = make_user()
    model_settings.save_settings(uid, URL, "m1", "k", small_model="sm", strong_model="st", profile="advanced")
    assert model_settings.load_settings(uid) == ModelSettings(URL, "m1", "k", "sm", "st", "advanced")
    model_settings.save_settings(uid, URL, "m1", None, profile="economy")  # slots written as given: None clears
    assert model_settings.load_settings(uid) == ModelSettings(URL, "m1", "k", None, None, "economy")


def test_profile_none_keeps_stored(make_user):
    uid = make_user()
    model_settings.save_settings(uid, URL, "m1", "k")
    assert model_settings.load_settings(uid).profile == "balanced"
    model_settings.save_settings(uid, URL, "m1", "k", profile="advanced")
    model_settings.save_settings(uid, URL, "m2", "k2")
    assert model_settings.load_settings(uid).profile == "advanced"
    model_settings.save_settings(uid, URL, "m2", None)
    assert model_settings.load_settings(uid).profile == "advanced"


def test_init_db_adds_columns_to_old_table(make_user):
    uid = make_user()
    model_settings.save_settings(uid, URL, "m1", "k")
    with get_engine().begin() as conn:
        conn.execute(text("ALTER TABLE model_settings DROP COLUMN small_model, DROP COLUMN strong_model, DROP COLUMN profile"))
    init_db()
    with get_engine().connect() as conn:
        cols = {r[0] for r in conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'model_settings'"))}
    assert {"small_model", "strong_model", "profile"} <= cols
    assert model_settings.load_settings(uid) == ModelSettings(URL, "m1", "k", None, None, "balanced")


def test_key_hint():
    assert model_settings.key_hint("sk-abcdef1234") == "sk-…1234"
    assert model_settings.key_hint("abcdef1234") == "…1234"


def test_require_secret(monkeypatch):
    monkeypatch.setattr(get_settings(), "model_key_secret", "")
    with pytest.raises(RuntimeError, match="MODEL_KEY_SECRET must be set"):
        model_settings.require_secret()


def test_malformed_secret_fails_at_startup(monkeypatch):
    monkeypatch.setattr(get_settings(), "model_key_secret", "not-a-fernet-key")
    with pytest.raises(RuntimeError, match="not a valid Fernet key"):
        model_settings.require_secret()
