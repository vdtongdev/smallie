import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.store.memory import InMemoryStore
from sqlalchemy import delete

from app.api import routes_chat
from app.api.main import app
from app.core.auth import get_current_user
from app.core.conversations import add_conversation_usage, create_conversation, get_conversation_usage
from app.core.db import get_engine, init_db, users
from app.core.model_settings import ModelSettings
from tests.conftest import requires_db
from tests.scripted import ScriptedModel

pytestmark = requires_db


@pytest.fixture
def env():
    init_db()
    created: list[str] = []
    current = {"id": None}
    app.dependency_overrides[get_current_user] = lambda: current["id"]

    def make_user() -> str:
        user_id = f"test-usage-{uuid.uuid4()}"
        with get_engine().begin() as conn:
            conn.execute(users.insert().values(id=user_id, display_name="Test"))
        created.append(user_id)
        current["id"] = user_id
        return user_id

    yield SimpleNamespace(client=TestClient(app), user=make_user, current=current)
    app.dependency_overrides.pop(get_current_user, None)
    with get_engine().begin() as conn:
        conn.execute(delete(users).where(users.c.id.in_(created)))


def test_usage_adds_up_and_keeps_the_last_reply(env):
    user = env.user()
    chat = create_conversation(user, "auto", "hi")["id"]
    add_conversation_usage(user, chat, {"input_tokens": 100, "output_tokens": 10, "cached_tokens": 60})
    result = add_conversation_usage(user, chat, {"input_tokens": 50, "output_tokens": 5, "cached_tokens": 40})
    assert result["chat"] == {"input_tokens": 150, "output_tokens": 15, "cached_tokens": 100}
    assert result["reply"] == {"input_tokens": 50, "output_tokens": 5, "cached_tokens": 40}
    assert get_conversation_usage(user, chat) == result


def test_unknown_cache_stays_none_until_reported(env):
    user = env.user()
    chat = create_conversation(user, "auto", "hi")["id"]
    result = add_conversation_usage(user, chat, {"input_tokens": 10, "output_tokens": 1})
    assert result["reply"]["cached_tokens"] is None
    assert result["chat"]["cached_tokens"] is None
    result = add_conversation_usage(user, chat, {"input_tokens": 10, "output_tokens": 1, "cached_tokens": 7})
    assert result["chat"]["cached_tokens"] == 7


def test_usage_is_per_user(env):
    owner = env.user()
    chat = create_conversation(owner, "auto", "hi")["id"]
    add_conversation_usage(owner, chat, {"input_tokens": 10, "output_tokens": 1, "cached_tokens": 2})
    before = get_conversation_usage(owner, chat)
    other = env.user()
    assert add_conversation_usage(other, chat, {"input_tokens": 99, "output_tokens": 9}) is None
    assert get_conversation_usage(other, chat) is None
    assert get_conversation_usage(owner, chat) == before
    assert env.client.get(f"/api/conversations/{chat}/usage").status_code == 404


def test_new_chat_usage_endpoint(env):
    user = env.user()
    chat = create_conversation(user, "auto", "hi")["id"]
    res = env.client.get(f"/api/conversations/{chat}/usage")
    assert res.status_code == 200
    assert res.json() == {"reply": None, "chat": {"input_tokens": 0, "output_tokens": 0, "cached_tokens": None}}


def test_chat_stream_sends_usage_before_done(env, monkeypatch):
    user = env.user()
    reply = AIMessage("hello", usage_metadata={"input_tokens": 120, "output_tokens": 8, "total_tokens": 128,
                                              "input_token_details": {"cache_read": 90}})
    model = ScriptedModel(replies=iter([reply]))

    class Assistant:
        async def astream(self, input, config, **kwargs):
            await model.ainvoke("hi", config={"callbacks": config["callbacks"]})
            return
            yield

        async def aget_state(self, config):
            return SimpleNamespace(values={"messages": []}, interrupts=[])

    monkeypatch.setattr(routes_chat, "load_settings", lambda uid: ModelSettings("https://x/v1", "m", "k"))
    app.state.assistant, app.state.store, app.state.checkpointer = Assistant(), InMemoryStore(), None
    res = env.client.post("/api/chat/auto", json={"message": "hi"})
    events = [json.loads(line[6:]) for line in res.text.splitlines() if line.startswith("data: ")]
    assert [e["type"] for e in events][-2:] == ["usage", "done"]
    assert events[-2]["reply"] == {"input_tokens": 120, "output_tokens": 8, "cached_tokens": 90}


def test_usage_by_model_adds_up_and_keeps_the_last_reply(env):
    user = env.user()
    chat = create_conversation(user, "auto", "hi")["id"]
    first = {"input_tokens": 10, "output_tokens": 1, "by_model": {
        "a/one": {"model_calls": 1, "input_tokens": 10, "output_tokens": 1, "cached_tokens": 4}}}
    second = {"input_tokens": 30, "output_tokens": 3, "by_model": {
        "a/one": {"model_calls": 1, "input_tokens": 10, "output_tokens": 1},
        "b/two": {"model_calls": 2, "input_tokens": 20, "output_tokens": 2}}}
    add_conversation_usage(user, chat, first)
    result = add_conversation_usage(user, chat, second)
    assert result["reply_by_model"] == second["by_model"]
    assert result["chat_by_model"]["a/one"] == {"model_calls": 2, "input_tokens": 20, "output_tokens": 2, "cached_tokens": 4}
    assert result["chat_by_model"]["b/two"] == {"model_calls": 2, "input_tokens": 20, "output_tokens": 2}
    assert result["chat"]["input_tokens"] == 40  # overall totals unchanged in meaning
    assert get_conversation_usage(user, chat) == result


def test_chat_without_breakdown_reads_empty(env):
    user = env.user()
    chat = create_conversation(user, "auto", "hi")["id"]
    result = add_conversation_usage(user, chat, {"input_tokens": 10, "output_tokens": 1})
    assert not result.get("chat_by_model") and not result.get("reply_by_model")


def test_usage_by_model_is_per_user(env):
    owner = env.user()
    chat = create_conversation(owner, "auto", "hi")["id"]
    by_model = {"a/one": {"model_calls": 1, "input_tokens": 10, "output_tokens": 1}}
    add_conversation_usage(owner, chat, {"input_tokens": 10, "output_tokens": 1, "by_model": by_model})
    other = env.user()
    assert add_conversation_usage(other, chat, {"input_tokens": 9, "output_tokens": 1, "by_model": by_model}) is None
    assert get_conversation_usage(owner, chat)["chat_by_model"] == by_model
