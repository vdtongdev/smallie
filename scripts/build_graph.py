"""Start each user's knowledge graph from what they had before it. Run once after deploying it.

Per user, once (`users.graph_built_at` marks them done, later runs skip them):
1. Sentence memories in the store ("users", <id>, "memories") become graph facts (source "import");
   each batch's items are deleted once its facts are saved.
2. Every turn of every past chat is mined for facts (source "import", with its conversation_id),
   CHAT_BATCH turns per call. `conversations.mined_turns` records progress, so a run stopped by a
   rate limit resumes where it stopped.
Then, for every ready document without a card: one card call, read from its stored chunks (the
uploaded files may be gone, e.g. on Cloud Run's ephemeral disk).

Last, a vector for the card of every ready document that has none (embedding calls only).

Spends free-quota model calls (printed before it starts). Back up the database first.

Usage:  uv run python -m scripts.build_graph
"""

import asyncio
import math
import sys
from contextlib import AsyncExitStack

import openai
from sqlalchemy import func, select, update

from app.core.db import conversations, documents, get_engine, init_db, users
from app.core.graph import embed_cards, extract_from_turns, format_turn
from app.core.memory import memory_namespace, open_persistence, turns
from app.core.model_settings import load_settings
from app.core.usage import UsageCounter, add_usage
from app.features.documents.card import write_card
from app.features.documents.ingest import chunk_ids, get_vector_store

MEMORY_BATCH = 5  # memory sentences per extraction call
CHAT_BATCH = 4  # chat turns per extraction call
CHAT_ANSWER_CHARS = 1500  # per answer, so a batch stays well inside the model's context
CARD_CHUNKS = 10  # 800-character chunks, enough for the card's opening text


async def extract(store, user_id: str, text: str, conversation_id: str | None = None) -> int:
    usage = UsageCounter()
    try:
        return await extract_from_turns(user_id, text, [usage], source="import", conversation_id=conversation_id)
    except openai.RateLimitError:
        raise  # stop here; the next run resumes at this batch
    except Exception as exc:  # noqa: BLE001 - e.g. a reply that fits no schema: skip the batch, don't retry forever
        print(f"    skipped a batch: {type(exc).__name__}: {str(exc)[:200]}", flush=True)
        return 0
    finally:
        await add_usage(store, user_id, usage)


async def pending_chats(checkpointer, user_id: str) -> list[tuple[str, int, list[tuple[str, str]]]]:
    """(conversation id, turns already mined, turns still to mine) for the user's chats."""
    with get_engine().connect() as conn:
        rows = conn.execute(select(conversations.c.id, conversations.c.mined_turns)
                            .where(conversations.c.user_id == user_id).order_by(conversations.c.created_at)).all()
    pending = []
    for conversation_id, mined in rows:
        saved = await checkpointer.aget_tuple({"configurable": {"thread_id": conversation_id}})
        pairs = turns(saved.checkpoint["channel_values"].get("messages", [])) if saved else []
        if pairs[mined:]:
            pending.append((conversation_id, mined, pairs[mined:]))
    return pending


async def build_user(store, checkpointer, user_id: str) -> int:
    """Memories and past chats of one user into the graph; returns facts saved."""
    if await asyncio.to_thread(load_settings, user_id) is None:  # not marked done: a later run builds them
        print(f"  skipped {user_id}: no model settings", flush=True)
        return 0
    facts = 0
    namespace = memory_namespace(user_id)
    items = await store.asearch(namespace, limit=10_000)
    for start in range(0, len(items), MEMORY_BATCH):
        batch = items[start:start + MEMORY_BATCH]
        told = "\n".join(f"- {item.value['text']}" for item in batch)
        facts += await extract(store, user_id, format_turn(f"Facts about me you noted earlier:\n{told}", "Noted."))
        for item in batch:  # only after their facts are saved
            await store.adelete(namespace, item.key)

    for conversation_id, mined, todo in await pending_chats(checkpointer, user_id):
        for start in range(0, len(todo), CHAT_BATCH):
            batch = todo[start:start + CHAT_BATCH]
            text = "\n\n---\n\n".join(format_turn(q, a, CHAT_ANSWER_CHARS) for q, a in batch)
            facts += await extract(store, user_id, text, conversation_id)
            with get_engine().begin() as conn:
                conn.execute(update(conversations).where(conversations.c.id == conversation_id)
                             .values(mined_turns=mined + start + len(batch)))

    with get_engine().begin() as conn:
        conn.execute(update(users).where(users.c.id == user_id).values(graph_built_at=func.now()))
    return facts


def users_to_build() -> list[str]:
    with get_engine().connect() as conn:
        return list(conn.execute(select(users.c.id).where(users.c.graph_built_at.is_(None))).scalars())


async def build_users(store, checkpointer) -> None:
    user_ids = users_to_build()
    plan = {}
    for user_id in user_ids:
        memories = len(await store.asearch(memory_namespace(user_id), limit=10_000))
        chats = await pending_chats(checkpointer, user_id)
        plan[user_id] = (math.ceil(memories / MEMORY_BATCH)
                         + sum(math.ceil(len(todo) / CHAT_BATCH) for _, _, todo in chats))
        print(f"  {user_id}: {memories} memories, {sum(len(t) for _, _, t in chats)} chat turns "
              f"in {len(chats)} chats -> {plan[user_id]} calls")
    print(f"  {len(user_ids)} users to build, {sum(plan.values())} calls in total", flush=True)
    for user_id in user_ids:
        print(f"  {user_id}: {await build_user(store, checkpointer, user_id)} facts", flush=True)


async def card_documents(store) -> int:
    with get_engine().connect() as conn:
        rows = conn.execute(select(documents.c.id, documents.c.user_id, documents.c.filename, documents.c.chunk_count)
                            .where(documents.c.status == "ready", documents.c.summary.is_(None))).all()
    print(f"  documents without a card: {len(rows)} -> {len(rows)} calls", flush=True)
    written = 0
    for document_id, user_id, filename, chunk_count in rows:
        chunks = await asyncio.to_thread(get_vector_store().get_by_ids, chunk_ids(document_id, min(chunk_count, CARD_CHUNKS)))
        chunks.sort(key=lambda chunk: int(chunk.id.rsplit(":", 1)[1]))  # get_by_ids doesn't keep the order
        if chunks and await write_card(store, document_id, user_id, filename, chunks):
            written += 1
            print(f"    {filename}", flush=True)
    return written


def card_vectors() -> int:
    with get_engine().begin() as conn:
        return embed_cards(conn, only_missing=True)


async def main() -> None:
    init_db()
    async with AsyncExitStack() as stack:
        checkpointer, store = await open_persistence(stack)
        print("Users (memories and past chats):")
        await build_users(store, checkpointer)
        print("Document cards:")
        print(f"  {await card_documents(store)} written")
        print(f"Card vectors: {await asyncio.to_thread(card_vectors)} embedded")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(main())
