"""Short-term memory (per conversation) and long-term memory (per user, across chats).

- Short-term: LangGraph AsyncPostgresSaver checkpoints every message of a thread;
  thread_id == conversation id.
- Long-term: LangGraph AsyncPostgresStore with a semantic index. Memories live under
  the namespace ("users", <user_id>, "memories"), so each user only ever sees their own.
- Cross-chat history: the latest turns of the user's other chats are read from their
  checkpoints for every request (recency), and every finished turn is also indexed under
  ("users", <user_id>, "history") for `search_chat_history` (older or specific chats).
"""

import uuid
from contextlib import AsyncExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from langchain.tools import ToolRuntime, tool
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.base import BaseStore
from langgraph.store.postgres.aio import AsyncPostgresStore
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.llm import get_chat_model, get_embeddings
from app.core.prompts import EXTRACT_PROMPT
from app.core.usage import usage_namespace


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
    failed: str = ""  # route that already gave up on the latest message (the orchestrator's fallback)


def memory_namespace(user_id: str) -> tuple[str, ...]:
    return ("users", user_id, "memories")


def history_namespace(user_id: str) -> tuple[str, ...]:
    return ("users", user_id, "history")


RECENT_CHATS = 3  # other chats shown in the prompt, most recently used first
RECENT_TURNS = 3  # turns shown per chat
QUESTION_CHARS = 300
ANSWER_CHARS = 200
HISTORY_ANSWER_CHARS = 500
# Cosine similarity at which a new memory replaces the closest saved one. Measured on the default
# embedding model: rewordings 0.86-0.98, a moved city 0.90, different stocks or styles 0.78-0.82.
# shortcut: tuned for one model, re-measure after changing OPENROUTER_EMBED_MODEL.
SAME_MEMORY = 0.85
EXTRACT_ANSWER_CHARS = 3000  # enough for a questionnaire's conclusion


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
    s = get_settings()
    pool = AsyncConnectionPool(
        s.database_url,
        min_size=1,
        max_size=10,
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
    checkpointer = AsyncPostgresSaver(pool)
    store = AsyncPostgresStore(pool, index=store_index_config())
    await checkpointer.setup()
    await store.setup()
    return checkpointer, store


async def recall(store: BaseStore, user_id: str, query: str, limit: int = 5) -> str:
    """Return the user's memories most related to `query`, as a bullet list."""
    items = await store.asearch(memory_namespace(user_id), query=query, limit=limit)
    return "\n".join(f"- {item.value['text']}" for item in items)


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
    """Remove all of a deleted account's memories, indexed chat history and token usage."""
    for namespace in (memory_namespace(user_id), history_namespace(user_id), usage_namespace(user_id)):
        while items := await store.asearch(namespace, limit=100):
            for item in items:
                await store.adelete(namespace, item.key)


async def remember(store: BaseStore, user_id: str, fact: str, kind: str) -> None:
    namespace = memory_namespace(user_id)
    closest = await store.asearch(namespace, query=fact, limit=1)
    # A rewording or a newer version of a saved fact replaces it instead of piling up.
    same = closest and closest[0].score is not None and closest[0].score >= SAME_MEMORY
    await store.aput(namespace, closest[0].key if same else str(uuid.uuid4()), {"text": fact, "kind": kind})


class Memory(BaseModel):
    kind: Literal["identity", "work", "goal", "preference", "investment_profile"]
    text: str = Field(min_length=6, max_length=200, description='One English sentence: "User <fact>".')


class MemoryUpdate(BaseModel):
    """Durable facts about the user from this chat turn; empty when there are none."""

    memories: list[Memory] = Field(default_factory=list, max_length=5)


async def extract_memories(store: BaseStore, user_id: str, question: str, answer: str, callbacks: list) -> None:
    """Save what one finished turn says about the user. A plain model call after the reply,
    because free models rarely chose to call a save tool themselves (AP-006)."""
    model = get_chat_model(get_settings().openrouter_orchestrator_model or None)
    extractor = model.with_structured_output(MemoryUpdate, method="function_calling")
    turn = f"User: {question}\n\nAssistant: {_short(answer, EXTRACT_ANSWER_CHARS)}"
    update = await extractor.ainvoke([SystemMessage(EXTRACT_PROMPT), HumanMessage(turn)], config={"callbacks": callbacks})
    for memory in update.memories:
        await remember(store, user_id, memory.text, memory.kind)


@tool
async def search_memory(query: str, runtime: ToolRuntime[AgentContext]) -> str:
    """Search what you remember about the user from earlier conversations.

    Args:
        query: What you want to know, e.g. "user's job" or "preferred language".
    """
    found = await recall(runtime.store, runtime.context.user_id, query)
    return found or "No related memories."


@tool
async def search_chat_history(query: str, runtime: ToolRuntime[AgentContext]) -> str:
    """Search the user's earlier chats (questions and your answers), e.g. when they ask
    "what did I ask about Apple last week?" or refer to something discussed in another chat.

    Args:
        query: What to look for, e.g. "Apple stock price" or "contract renewal date".
    """
    items = await runtime.store.asearch(history_namespace(runtime.context.user_id), query=query, limit=5)
    if not items:
        return "No earlier chats found."
    return "\n\n".join(
        f'[{item.value["created_at"]}, chat "{item.value["title"]}"]\n{item.value["text"]}' for item in items
    )


MEMORY_TOOLS = [search_memory, search_chat_history]
