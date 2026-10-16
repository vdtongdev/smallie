import pytest
from langchain_core.documents import Document
from langchain_core.embeddings import DeterministicFakeEmbedding
from langgraph.store.memory import InMemoryStore

from app.core.config import get_settings
from app.agents.docs import tools as docs_tools
from app.core.memory import memory_namespace, recall
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


async def test_recall_is_scoped_per_user(memory_store):
    await memory_store.aput(memory_namespace("alice"), "1", {"text": "User's name is Alice"})
    await memory_store.aput(memory_namespace("alice"), "2", {"text": "Prefers answers in Vietnamese"})
    await memory_store.aput(memory_namespace("bob"), "1", {"text": "User's name is Bob"})

    alice = await recall(memory_store, "alice", "what is my name?")
    assert "Alice" in alice and "Vietnamese" in alice
    assert "Bob" not in alice
    assert await recall(memory_store, "carol", "anything") == ""


async def test_recall_formats_bullets(memory_store):
    await memory_store.aput(memory_namespace("u"), "1", {"text": "Works in sales"})
    assert await recall(memory_store, "u", "job") == "- Works in sales"


async def test_save_memory_replaces_a_near_duplicate(memory_store):
    from types import SimpleNamespace

    from app.core.memory import AgentContext, save_memory

    async def save(user_id: str, fact: str) -> None:
        runtime = SimpleNamespace(store=memory_store, context=AgentContext(user_id=user_id))
        await save_memory.coroutine(fact=fact, runtime=runtime)

    async def texts(user_id: str) -> list[str]:
        return sorted(i.value["text"] for i in await memory_store.asearch(memory_namespace(user_id), limit=10))

    await save("alice", "fact one")
    await save("alice", "fact one")  # the fake embedding only matches identical text
    await save("alice", "fact two")
    await save("bob", "fact one")
    assert await texts("alice") == ["fact one", "fact two"]
    assert await texts("bob") == ["fact one"]


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
