"""The memory part of the prompt is frozen per chat, so the cached prompt prefix survives between turns."""

import json
from datetime import datetime, timezone
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.embeddings import DeterministicFakeEmbedding
from langgraph.store.memory import InMemoryStore
from sqlalchemy import delete, select, text, update

from app.agents import factory
from app.api import routes_chat
from app.api.main import app
from app.core import graph
from app.core.auth import get_current_user
from app.core.config import get_settings
from app.core.conversations import create_conversation, get_conversation
from app.core.db import conversations, documents, get_engine, init_db, users
from app.core.graph import Fact
from app.core.memory import AgentContext
from app.core.model_settings import ModelSettings
from app.core.snapshot import chat_snapshot, clear_snapshots, save_snapshot
from tests.conftest import requires_db
from tests.scripted import scripted

pytestmark = requires_db


@pytest.fixture
def env(monkeypatch):
    init_db()
    fake = DeterministicFakeEmbedding(size=get_settings().embed_dim)
    monkeypatch.setattr(graph, "get_embeddings", lambda: fake)
    created: list[str] = []
    current = {"id": None}
    app.dependency_overrides[get_current_user] = lambda: current["id"]

    def make_user() -> str:
        user_id = f"test-snapshot-{uuid.uuid4()}"
        with get_engine().begin() as conn:
            conn.execute(users.insert().values(id=user_id, display_name="Test"))
        created.append(user_id)
        current["id"] = user_id
        return user_id

    yield SimpleNamespace(client=TestClient(app), user=make_user)
    app.dependency_overrides.pop(get_current_user, None)
    with get_engine().begin() as conn:
        conn.execute(delete(users).where(users.c.id.in_(created)))


def learn(user: str, obj: str) -> None:
    graph.save_facts(user, [Fact(subject="User", relation="likes", object=obj)], "chat")


def stored(chat: str):
    with get_engine().connect() as conn:
        return conn.execute(select(conversations.c.memory_snapshot).where(conversations.c.id == chat)).scalar()


async def test_snapshot_is_reused_within_the_window(env):
    user = env.user()
    chat = create_conversation(user, "auto", "hi")["id"]
    first = await chat_snapshot(None, user, get_conversation(user, chat))
    learn(user, "tea")
    second = await chat_snapshot(None, user, get_conversation(user, chat))
    assert "tea" not in second["memories"]
    assert second == first == stored(chat)


async def test_snapshot_rebuilt_after_idle_gap(env):
    user = env.user()
    chat = create_conversation(user, "auto", "hi")["id"]
    await chat_snapshot(None, user, get_conversation(user, chat))
    learn(user, "tea")
    with get_engine().begin() as conn:
        conn.execute(update(conversations).where(conversations.c.id == chat)
                     .values(updated_at=text("now() - interval '61 minutes'")))
    again = await chat_snapshot(None, user, get_conversation(user, chat))
    assert "tea" in again["memories"]
    assert stored(chat) == again


async def test_snapshot_built_when_missing(env):
    user = env.user()
    learn(user, "tea")
    created = create_conversation(user, "auto", "hi")  # no memory_snapshot or updated_at keys
    snapshot = await chat_snapshot(None, user, created)
    assert "tea" in snapshot["memories"]
    assert stored(created["id"]) == snapshot


def test_clear_snapshots_is_per_user(env):
    a, b = env.user(), env.user()
    chat_a, chat_b = create_conversation(a, "auto", "hi")["id"], create_conversation(b, "auto", "hi")["id"]
    for user, chat in ((a, chat_a), (b, chat_b)):
        save_snapshot(user, chat, {"user": "", "memories": "x", "recent_chats": ""})
    clear_snapshots(a)
    assert stored(chat_a) is None
    assert stored(chat_b) is not None


def test_deleting_a_fact_document_or_chat_clears_snapshots(env, monkeypatch, tmp_path):
    user = env.user()
    keep = create_conversation(user, "auto", "keep")["id"]
    other = create_conversation(user, "auto", "other")["id"]
    learn(user, "tea")
    fact_id = graph.list_facts(user)[0]["id"]
    path = tmp_path / "a.pdf"
    path.write_text("x")
    doc_id = str(uuid.uuid4())
    with get_engine().begin() as conn:
        conn.execute(documents.insert().values(id=doc_id, user_id=user, filename="a.pdf", path=str(path),
                                               status="ready", chunk_count=0))
    monkeypatch.setattr("app.features.documents.routes.delete_document_chunks", lambda *a: None)

    class Checkpointer:
        async def adelete_thread(self, thread_id):
            pass

    app.state.checkpointer, app.state.store = Checkpointer(), InMemoryStore()

    def freeze():
        save_snapshot(user, keep, {"user": "", "memories": "x", "recent_chats": ""})
        assert stored(keep) is not None

    for url in (f"/api/memories/{fact_id}", f"/api/documents/{doc_id}", f"/api/conversations/{other}"):
        freeze()
        assert env.client.delete(url).status_code == 204, url
        assert stored(keep) is None, url


def test_two_turns_send_the_same_system_prompt(env, monkeypatch):
    user = env.user()
    model = scripted(AIMessage("one"), AIMessage("two"))
    monkeypatch.setattr(factory, "get_chat_model", lambda *a, **k: model)
    agent = factory.build_agent(name="t", system_prompt="x", tools=[])

    class Assistant:
        async def astream(self, input, config, context, **kwargs):
            await agent.ainvoke({"messages": [HumanMessage("go")]}, context=context)
            return
            yield

        async def aget_state(self, config):
            return SimpleNamespace(values={"messages": []}, interrupts=[])

    monkeypatch.setattr(routes_chat, "load_settings", lambda uid: ModelSettings("https://x/v1", "m", "k"))
    app.state.assistant, app.state.store, app.state.checkpointer = Assistant(), InMemoryStore(), None
    learn(user, "coffee")
    first = env.client.post("/api/chat/auto", json={"message": "hi"})
    chat = next(json.loads(l[6:]) for l in first.text.splitlines() if l.startswith("data: "))["id"]
    learn(user, "tea")
    env.client.post("/api/chat/auto", json={"message": "again", "conversation_id": chat})
    assert len(model.prompts) == 2
    assert "coffee" in model.prompts[0][0].content
    assert model.prompts[0][0].content == model.prompts[1][0].content


def test_snapshot_built_before_a_clear_is_not_saved(env):
    user = env.user()
    chat = create_conversation(user, "auto", "hi")["id"]
    snapshot = {"user": "", "memories": "x", "recent_chats": ""}
    started = datetime.now(timezone.utc)
    clear_snapshots(user)
    save_snapshot(user, chat, snapshot, built_at=started)
    assert stored(chat) is None
    save_snapshot(user, chat, snapshot, built_at=datetime.now(timezone.utc))
    assert stored(chat) == snapshot


def test_snapshot_built_at_the_clear_instant_is_not_saved(env):
    user = env.user()
    chat = create_conversation(user, "auto", "hi")["id"]
    clear_snapshots(user)
    with get_engine().connect() as conn:
        cleared = conn.execute(select(conversations.c.snapshot_at).where(conversations.c.id == chat)).scalar()
    save_snapshot(user, chat, {"user": "", "memories": "x", "recent_chats": ""}, built_at=cleared)
    assert stored(chat) is None
