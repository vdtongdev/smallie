"""Short-term memory (per conversation) and long-term memory (per user, across chats).

- Short-term: LangGraph AsyncPostgresSaver checkpoints every message of a thread;
  thread_id == conversation id.
- Long-term: the user's knowledge graph (app/core/graph.py). The LangGraph AsyncPostgresStore
  (semantic index) keeps the indexed chat history and token usage.
- Cross-chat history: the latest turns of the user's other chats are read from their
  checkpoints for every request (recency), and every finished turn is also indexed under
  ("users", <user_id>, "history") for `search_chat_history` (older or specific chats).
"""

import asyncio
import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime, timezone

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.base import BaseStore
from langgraph.store.postgres.aio import AsyncPostgresStore
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.core.config import get_settings
from app.core.graph import search_facts
from app.core.llm import get_embeddings
from app.core.usage import usage_namespace


# AgentContext.agent value for team mode (agents critique each other's drafts).
TEAM_MODE = "team"


TOOL_SOURCE = "source"  # tool.metadata key: the data source a tool reads ("market_db", "user_files", "web")


@dataclass
class AgentContext:
    """Per-request data passed to every agent run."""

    user_id: str
    user: str = ""  # "Name (username: x)" of the account; never the password
    memories: str = ""
    recent_chats: str = ""  # latest turns of the user's other chats
    agent: str = "auto"  # "auto" lets the router pick; a registered route (app/agents/registry.py) forces that agent
    notes: str = ""  # per-request notes for the orchestrator's prompt (specialists available to this user)
    mentioned: tuple[str, ...] = ()  # routes the latest message points to (hints, matches, mentions)
    sources: tuple[str, ...] = ()  # sources the agent may use (team mode); empty = no limit
    failed: str = ""  # route that already gave up on the latest message (the orchestrator's fallback)
    deny_tools: tuple[str, ...] = ()  # tools hidden from the agent and refused if called (team mode: charts have one drawer)


def memory_namespace(user_id: str) -> tuple[str, ...]:
    """Sentence memories from before the knowledge graph; scripts.build_graph converts and empties it."""
    return ("users", user_id, "memories")


def history_namespace(user_id: str) -> tuple[str, ...]:
    return ("users", user_id, "history")


RECENT_CHATS = 3  # other chats shown in the prompt, most recently used first
RECENT_TURNS = 3  # turns shown per chat
QUESTION_CHARS = 300
ANSWER_CHARS = 200
HISTORY_ANSWER_CHARS = 500


# pgvector can only build HNSW indexes for vectors of up to 2000 dimensions.
MAX_INDEXED_DIMS = 2000


def store_index_config() -> dict:
    s = get_settings()
    config = {"dims": s.embed_dim, "embed": get_embeddings(), "fields": ["text"]}
    if s.embed_dim > MAX_INDEXED_DIMS:
        config["ann_index_config"] = {"kind": "flat"}
    return config


async def open_persistence(stack: AsyncExitStack) -> tuple[AsyncPostgresSaver, AsyncPostgresStore]:
    """Open the checkpointer and store, creating their tables on first run.

    Both share a connection pool that health-checks connections before handing them
    out, so a Postgres restart doesn't leave the app stuck on a dead connection.
    """
    pool = await _open_pool(stack, max_size=10)
    checkpointer = AsyncPostgresSaver(pool)
    store = AsyncPostgresStore(pool, index=store_index_config())
    await checkpointer.setup()
    await store.setup()
    return checkpointer, store


async def open_history_store(stack: AsyncExitStack) -> AsyncPostgresStore:
    """The app's store, read by an agent server's `search_chat_history`: its own Agent Server
    store is empty. No `setup()`: the main app creates the tables, and this role may be read-only."""
    return AsyncPostgresStore(await _open_pool(stack, max_size=2), index=store_index_config())


async def _open_pool(stack: AsyncExitStack, max_size: int) -> AsyncConnectionPool:
    pool = AsyncConnectionPool(
        get_settings().database_url,
        min_size=1,
        max_size=max_size,
        open=False,
        check=AsyncConnectionPool.check_connection,
        timeout=15,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            # Detect half-open sockets instead of waiting on them forever.
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 3,
        },
    )
    await stack.enter_async_context(pool)
    return pool


def turns(messages: list[BaseMessage]) -> list[tuple[str, str]]:
    """(question, final answer text) for each user message of a thread."""
    pairs: list[list[str]] = []
    for message in messages:
        if isinstance(message, HumanMessage) and message.text:
            pairs.append([message.text, ""])
        elif isinstance(message, AIMessage) and message.text and pairs:
            pairs[-1][1] = message.text  # the last text reply of the turn is the answer
    return [(q, a) for q, a in pairs]


def _short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


async def load_recent_chats(checkpointer: BaseCheckpointSaver, conversations: list[dict]) -> str:
    """The last turns of `conversations` (already scoped to the user, newest first)."""
    blocks = []
    for conversation in conversations[:RECENT_CHATS]:
        saved = await checkpointer.aget_tuple({"configurable": {"thread_id": conversation["id"]}})
        if not saved:
            continue
        recent = turns(saved.checkpoint["channel_values"].get("messages", []))[-RECENT_TURNS:]
        if not recent:
            continue
        when = conversation["updated_at"].astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = [f'Chat "{conversation["title"]}" (last active {when}), oldest to newest:']
        for question, answer in recent:
            lines.append(f"  User: {_short(question, QUESTION_CHARS)}")
            if answer:
                lines.append(f"  Assistant: {_short(answer, ANSWER_CHARS)}")
        blocks.append("\n".join(lines))
    return "\n".join(blocks)


async def save_turn(store: BaseStore, user_id: str, conversation: dict, question: str, answer: str) -> None:
    """Index one finished turn so `search_chat_history` can find it from any chat."""
    text = f"Q: {question}\nA: {_short(answer, HISTORY_ANSWER_CHARS)}" if answer else f"Q: {question}"
    await store.aput(
        history_namespace(user_id),
        f"{conversation['id']}:{uuid.uuid4()}",
        {
            "text": text,
            "conversation_id": conversation["id"],
            "title": conversation["title"],
            "created_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        },
    )


async def forget_conversation(store: BaseStore, user_id: str, conversation_id: str) -> None:
    """Remove a deleted chat's turns from the searchable history."""
    namespace = history_namespace(user_id)
    while items := await store.asearch(namespace, filter={"conversation_id": conversation_id}, limit=100):
        for item in items:
            await store.adelete(namespace, item.key)


async def forget_user(store: BaseStore, user_id: str) -> None:
    """Remove a deleted account's store items: old sentence memories, indexed chat history and
    token usage. Its graph rows go with the users row (ON DELETE CASCADE)."""
    for namespace in (memory_namespace(user_id), history_namespace(user_id), usage_namespace(user_id)):
        while items := await store.asearch(namespace, limit=100):
            for item in items:
                await store.adelete(namespace, item.key)


_history_store: BaseStore | None = None  # set on an agent server (app/agents/server.py)


def set_history_store(store: BaseStore) -> None:
    global _history_store
    _history_store = store


@tool
async def search_memory(query: str, runtime: ToolRuntime[AgentContext]) -> str:
    """Search what you know about the user from earlier chats and their files, beyond the facts
    already listed in your instructions.

    Args:
        query: What you want to know, e.g. "user's job" or "preferred language".
    """
    found = await asyncio.to_thread(search_facts, runtime.context.user_id, query)
    return found or "No related memories."


@tool
async def search_chat_history(query: str, runtime: ToolRuntime[AgentContext]) -> str:
    """Search the user's earlier chats (questions and your answers), e.g. when they ask
    "what did I ask about Apple last week?" or refer to something discussed in another chat.

    Args:
        query: What to look for, e.g. "Apple stock price" or "contract renewal date".
    """
    store = _history_store or runtime.store
    # No TTLs here; the TTL refresh is an UPDATE, which the agent servers' agent_reader can't run.
    items = await store.asearch(history_namespace(runtime.context.user_id), query=query, limit=5, refresh_ttl=False)
    if not items:
        return "No earlier chats found."
    return "\n\n".join(
        f'[{item.value["created_at"]}, chat "{item.value["title"]}"]\n{item.value["text"]}' for item in items
    )


MEMORY_TOOLS = [search_memory, search_chat_history]
