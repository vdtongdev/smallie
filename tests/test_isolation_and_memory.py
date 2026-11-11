import pytest
from langchain_core.documents import Document
from langchain_core.embeddings import DeterministicFakeEmbedding
from langgraph.store.memory import InMemoryStore

from app.core.config import get_settings
from app.agents.docs import tools as docs_tools
from tests.conftest import requires_db


@requires_db
def test_document_search_only_returns_the_users_own_chunks(monkeypatch):
    from langchain_postgres import PGVector

    store = PGVector(
        embeddings=DeterministicFakeEmbedding(size=32),
        connection=get_settings().sqlalchemy_url,
        collection_name="test_isolation",
        pre_delete_collection=True,
    )
    store.add_documents(
        [
            Document("Alice secret plan", metadata={"user_id": "alice", "filename": "a.pdf", "page": 1}),
            Document("Bob secret plan", metadata={"user_id": "bob", "filename": "b.pdf", "page": 1}),
        ]
    )
    monkeypatch.setattr(docs_tools, "get_vector_store", lambda: store)
    try:
        result = docs_tools.search_user_chunks("alice", "secret plan")
        assert "a.pdf" in result and "Alice" in result
        assert "b.pdf" not in result and "Bob" not in result
        assert "No matching" in docs_tools.search_user_chunks("carol", "secret plan")
    finally:
        store.delete_collection()


@pytest.fixture
def memory_store() -> InMemoryStore:
    return InMemoryStore(index={"dims": 16, "embed": DeterministicFakeEmbedding(size=16), "fields": ["text"]})


def test_only_the_orchestrators_answers_are_mined():
    from langchain_core.messages import AIMessage, HumanMessage

    from app.api.routes_chat import _answered_by

    assert _answered_by([HumanMessage("q"), AIMessage("a", name="orchestrator")]) == "orchestrator"
    assert _answered_by([HumanMessage("q"), AIMessage("", name="orchestrator"), AIMessage("rows", name="sql_agent")]) == "sql_agent"
    assert _answered_by([HumanMessage("q")]) is None


async def test_search_chat_history_is_scoped_and_forgets_deleted_chats(memory_store):
    from types import SimpleNamespace

    from app.core.memory import AgentContext, forget_conversation, save_turn, search_chat_history

    chat_a = {"id": "a", "title": "Apple price"}
    chat_b = {"id": "b", "title": "Shop revenue"}
    await save_turn(memory_store, "alice", chat_a, "What is Apple's price?", "AAPL closed at 230 USD.")
    await save_turn(memory_store, "alice", chat_b, "Revenue last month?", "")
    await save_turn(memory_store, "bob", {"id": "c", "title": "Bob"}, "Bob question", "Bob answer")

    async def search(user_id: str) -> str:
        runtime = SimpleNamespace(store=memory_store, context=AgentContext(user_id=user_id))
        return await search_chat_history.coroutine(query="Apple", runtime=runtime)

    found = await search("alice")
    assert "Apple's price" in found and "230 USD" in found and "Revenue" in found
    assert "Bob" not in found
    assert await search("carol") == "No earlier chats found."

    await forget_conversation(memory_store, "alice", "a")
    found = await search("alice")
    assert "Apple" not in found and "Revenue" in found


async def test_recent_chats_show_latest_turns_of_other_chats():
    from datetime import datetime, timezone

    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import START, MessagesState, StateGraph

    from app.core.memory import load_recent_chats

    def reply(state: MessagesState) -> dict:
        tool_step = AIMessage(content="", tool_calls=[{"name": "run_sql", "args": {}, "id": "1"}])
        return {"messages": [tool_step, AIMessage(content=f"Answer to {state['messages'][-1].text}")]}

    checkpointer = InMemorySaver()
    graph = StateGraph(MessagesState)
    graph.add_node("reply", reply)
    graph.add_edge(START, "reply")
    app = graph.compile(checkpointer=checkpointer)
    for question in ["first question", "latest question"]:
        await app.ainvoke({"messages": [("user", question)]}, {"configurable": {"thread_id": "a"}})

    when = datetime(2026, 10, 12, 9, 30, tzinfo=timezone.utc)
    text = await load_recent_chats(checkpointer, [
        {"id": "a", "title": "Stocks", "updated_at": when},
        {"id": "missing", "title": "Empty", "updated_at": when},
    ])
    assert 'Chat "Stocks" (last active 2026-10-12 09:30 UTC)' in text
    assert text.index("first question") < text.index("latest question")
    assert "Assistant: Answer to latest question" in text
    assert "Empty" not in text
    assert await load_recent_chats(checkpointer, []) == ""


# ---------- extraction after every finished turn ----------

import json  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from langchain_core.messages import AIMessage, HumanMessage  # noqa: E402

from app.api import routes_chat  # noqa: E402
from app.api.main import app  # noqa: E402
from app.core.model_settings import ModelSettings  # noqa: E402
from tests.test_snapshot import env  # noqa: E402,F401  (fixture)


def drive_turn(env, monkeypatch, answered_by: str) -> list[dict]:
    """Run one chat turn whose final text is an AIMessage named `answered_by`; returns what the
    extraction call received (the background task has run when the client call returns)."""
    env.user()
    calls: list[dict] = []

    async def recorder(user_id, question, answer, callbacks, source="chat", conversation_id=None, agent=None):
        calls.append({"question": question, "answer": answer, "agent": agent})
        return 0

    class Assistant:
        async def astream(self, input, config, context, **kwargs):
            return
            yield

        async def aget_state(self, config):
            return SimpleNamespace(values={"messages": [HumanMessage("hi"), AIMessage("done", name=answered_by)]},
                                   interrupts=[])

    monkeypatch.setattr(routes_chat, "extract_facts", recorder)
    monkeypatch.setattr(routes_chat, "load_settings", lambda uid: ModelSettings("https://x/v1", "m", "k"))
    app.state.assistant, app.state.store, app.state.checkpointer = Assistant(), InMemoryStore(), None
    response = env.client.post("/api/chat/auto", json={"message": "hi"})
    assert any(json.loads(l[6:]).get("type") == "done" for l in response.text.splitlines() if l.startswith("data: "))
    return calls


@requires_db
def test_a_turn_a_specialist_answered_is_extracted_once_with_its_route(env, monkeypatch):
    calls = drive_turn(env, monkeypatch, "sql_agent")
    assert [c["agent"] for c in calls] == ["sql"]


@requires_db
def test_an_orchestrator_turn_is_extracted_with_the_orchestrator_route(env, monkeypatch):
    calls = drive_turn(env, monkeypatch, "orchestrator")
    assert [c["agent"] for c in calls] == ["orchestrator"]


# ---------- agent notes through /api/memories ----------

import time  # noqa: E402

from sqlalchemy import select  # noqa: E402

from app.core import graph  # noqa: E402
from app.core.conversations import create_conversation  # noqa: E402
from app.core.db import conversations, get_engine  # noqa: E402
from app.core.graph import AgentNote, Fact  # noqa: E402
from app.core.snapshot import save_snapshot  # noqa: E402


def learn_fact_then_note(user: str) -> None:
    graph.save_facts(user, [Fact(subject="User", relation="likes", object="tea")], "chat")
    time.sleep(0.05)  # the Windows clock ticks coarsely: the note must be strictly newer
    graph.save_notes(user, "sql", [AgentNote(text="Show prices in USD", about=[])])


def stored_snapshot(chat: str):
    with get_engine().connect() as conn:
        return conn.execute(select(conversations.c.memory_snapshot).where(conversations.c.id == chat)).scalar()


@requires_db
def test_memories_lists_notes_with_their_agent(env):
    user = env.user()
    learn_fact_then_note(user)
    rows = env.client.get("/api/memories").json()
    assert len(rows) == 2
    note = next(r for r in rows if r["agent"] == "sql")
    fact = next(r for r in rows if r["agent"] is None)
    assert "USD" in note["text"] and "tea" in fact["text"]
    assert [r["id"] for r in rows] == [note["id"], fact["id"]]  # newest first


@requires_db
def test_deleting_a_note_through_the_api(env):
    user = env.user()
    learn_fact_then_note(user)
    chat = create_conversation(user, "auto", "hi")["id"]
    time.sleep(0.05)
    save_snapshot(user, chat, {"user": "", "memories": "x", "recent_chats": ""})
    assert stored_snapshot(chat) is not None
    note_id = next(r["id"] for r in env.client.get("/api/memories").json() if r["agent"] == "sql")
    assert env.client.delete(f"/api/memories/{note_id}").status_code == 204
    assert [r for r in env.client.get("/api/memories").json() if r["agent"] == "sql"] == []
    assert stored_snapshot(chat) is None


@requires_db
def test_another_users_note_cannot_be_deleted(env):
    a = env.user()
    graph.save_notes(a, "sql", [AgentNote(text="Show prices in USD", about=[])])
    note_id = graph.list_notes(a)[0]["id"]
    env.user()  # user B is now the caller
    assert env.client.delete(f"/api/memories/{note_id}").status_code == 404
    assert [n["id"] for n in graph.list_notes(a)] == [note_id]
