"""GET/PUT/DELETE /api/model-settings (app/api/routes_model_settings.py) against the real Postgres."""

import socket
import uuid

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.api import routes_model_settings
from app.api.main import app
from app.core.auth import get_current_user
from app.core.config import get_settings
from app.core.db import get_engine, init_db, users
from app.core.endpoint import UnsafeEndpoint
from app.core.model_settings import load_settings
from tests.conftest import requires_db

pytestmark = requires_db

URL = "https://api.example.test/v1"
KEY = "sk-secret-abcd1234"


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(get_settings(), "model_key_secret", Fernet.generate_key().decode())
    init_db()
    created, current, requests = [], {"id": None}, []
    handler = {"fn": lambda request: httpx.Response(200, json={"data": [{"id": "m1"}]})}

    def transport_handler(request):
        requests.append(request)
        return handler["fn"](request)

    monkeypatch.setattr(routes_model_settings, "_transport", httpx.MockTransport(transport_handler))
    monkeypatch.setattr(routes_model_settings, "check_endpoint", lambda url: None)
    app.dependency_overrides[get_current_user] = lambda: current["id"]

    def make_user() -> str:
        user_id = f"test-api-{uuid.uuid4()}"
        with get_engine().begin() as conn:
            conn.execute(users.insert().values(id=user_id, display_name="Test"))
        created.append(user_id)
        current["id"] = user_id
        return user_id

    class Env:
        client = TestClient(app)
        sent = requests

        def user(self):
            return make_user()

        def respond(self, fn):
            handler["fn"] = fn

    yield Env()
    app.dependency_overrides.pop(get_current_user, None)
    with get_engine().begin() as conn:
        conn.execute(delete(users).where(users.c.id.in_(created)))


def put(env, **body):
    body = {"base_url": URL, "model": "m1", "api_key": KEY, **body}
    return env.client.put("/api/model-settings", json=body)


def test_get_never_returns_the_key(env):
    env.user()
    assert env.client.get("/api/model-settings").status_code == 404
    assert put(env).json() == {"saved": True, "warning": None, "warnings": {}}
    r = env.client.get("/api/model-settings")
    assert r.json() == {
        "base_url": URL,
        "model": "m1",
        "small_model": None,
        "strong_model": None,
        "profile": "balanced",
        "key_hint": "sk-…1234",
    }
    assert KEY not in r.text


def test_openrouter_key_on_another_host_is_refused(env):
    uid = env.user()
    r = put(env, api_key="sk-or-v1-abcd")
    assert r.status_code == 400
    assert r.json()["detail"] == "This looks like an OpenRouter key; use https://openrouter.ai/api/v1"
    assert env.sent == []
    assert load_settings(uid) is None


def test_anthropic_key_hint(env):
    env.user()
    r = put(env, api_key="sk-ant-abcd")
    assert r.status_code == 400
    assert r.json()["detail"] == "This looks like an Anthropic key; use https://api.anthropic.com/v1/"
    assert env.sent == []


def test_rejected_key_is_not_saved(env):
    uid = env.user()
    env.respond(lambda request: httpx.Response(401))
    r = put(env)
    assert r.status_code == 400
    assert r.json()["detail"] == (
        "The key was rejected by this endpoint. Check that key and endpoint come from the same provider."
    )
    assert load_settings(uid) is None


def test_unreachable_or_not_openai_is_not_saved(env):
    uid = env.user()

    def refuse(request):
        raise httpx.ConnectError("boom")

    env.respond(refuse)
    r = put(env)
    assert r.status_code == 400
    assert KEY not in r.text
    env.respond(lambda request: httpx.Response(200, json={"ok": True}))
    r = put(env)
    assert r.status_code == 400
    assert KEY not in r.text
    assert load_settings(uid) is None


def test_unlisted_model_saves_with_a_warning(env):
    uid = env.user()
    r = put(env, model="other")
    assert r.status_code == 200
    assert r.json() == {
        "saved": True,
        "warning": "This server doesn't list that model; check the name.",
        "warnings": {},
    }
    assert load_settings(uid).model == "other"


def test_trailing_slash_endpoint(env):
    env.user()
    assert put(env, base_url="https://api.deepseek.com/v1/").status_code == 200
    assert str(env.sent[0].url) == "https://api.deepseek.com/v1/models"
    assert env.sent[0].headers["authorization"] == f"Bearer {KEY}"


def test_omitted_key_checks_with_the_stored_one(env):
    env.user()
    assert put(env).status_code == 200
    r = env.client.put("/api/model-settings", json={"base_url": URL, "model": "m1"})
    assert r.status_code == 200
    assert env.sent[1].headers["authorization"] == f"Bearer {KEY}"


def test_no_key_at_all_is_refused(env):
    env.user()
    r = env.client.put("/api/model-settings", json={"base_url": URL, "model": "m1"})
    assert r.status_code == 400
    assert r.json()["detail"] == "API key required"


def test_unsafe_endpoint_is_refused(env, monkeypatch):
    from app.core import endpoint

    monkeypatch.setattr(routes_model_settings, "check_endpoint", endpoint.check_endpoint)
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 443))]
    )
    env.user()
    r = put(env)
    assert r.status_code == 400
    assert env.sent == []
    assert UnsafeEndpoint  # the reason text comes from check_endpoint


def test_users_cannot_touch_each_others_settings(env):
    env.user()
    assert put(env).status_code == 200
    env.user()
    assert env.client.get("/api/model-settings").status_code == 404


def test_delete_removes_the_settings(env):
    uid = env.user()
    put(env)
    assert env.client.delete("/api/model-settings").status_code == 204
    assert load_settings(uid) is None
    assert env.client.get("/api/model-settings").status_code == 404


def test_get_returns_slots_and_profile_never_key(env):
    env.user()
    r = put(env, small_model=" s1 ", strong_model="", profile="economy")
    assert r.status_code == 200
    r = env.client.get("/api/model-settings")
    assert r.json() == {
        "base_url": URL,
        "model": "m1",
        "small_model": "s1",
        "strong_model": None,
        "profile": "economy",
        "key_hint": "sk-…1234",
    }
    assert KEY not in r.text


def test_invalid_profile_400(env):
    uid = env.user()
    r = put(env, profile="turbo")
    assert r.status_code == 400
    assert load_settings(uid) is None


def test_unlisted_slot_model_warns_naming_slot(env):
    uid = env.user()
    r = put(env, strong_model="big")
    assert r.status_code == 200
    body = r.json()
    assert body["warning"] is None
    assert set(body["warnings"]) == {"strong_model"}
    assert "strong" in body["warnings"]["strong_model"]
    assert load_settings(uid).strong_model == "big"


def test_models_list_uses_saved_key(env):
    env.user()
    put(env)
    env.respond(lambda request: httpx.Response(200, json={"data": [{"id": "a"}, {"id": "b"}]}))
    r = env.client.get("/api/model-settings/models")
    assert r.json() == {"models": ["a", "b"]}
    assert env.sent[-1].headers["authorization"] == f"Bearer {KEY}"
    assert KEY not in r.text


def test_models_list_empty_on_failure(env):
    env.user()
    put(env)
    env.respond(lambda request: httpx.Response(500))
    assert env.client.get("/api/model-settings/models").json() == {"models": []}


def test_other_user_cannot_read_slots(env):
    env.user()
    put(env, small_model="s1")
    env.user()
    assert env.client.get("/api/model-settings").status_code == 404
    assert env.client.get("/api/model-settings/models").json() == {"models": []}
