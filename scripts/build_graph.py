"""Fill the knowledge graph from data that predates it. Safe to run again (e.g. after a rate limit).

1. Sentence memories in the store ("users", <id>, "memories") become graph facts (source "import");
   each batch's items are deleted once its facts are saved.
2. Ready documents without a card get one, read from their stored chunks (the uploaded files may
   be gone, e.g. on Cloud Run's ephemeral disk).

Spends free-quota model calls: one per 5 memories plus one per document. Back up the database first.

Usage:  uv run python -m scripts.build_graph
"""

import asyncio
import sys
from contextlib import AsyncExitStack

from sqlalchemy import select

from app.core.db import documents, get_engine, init_db, users
from app.core.graph import extract_facts
from app.core.memory import open_persistence
from app.core.usage import UsageCounter, add_usage
from app.features.documents.card import write_card
from app.features.documents.ingest import chunk_ids, get_vector_store

BATCH = 5  # memories per extraction call
CARD_CHUNKS = 10  # 800-character chunks, enough for the card's opening text


async def convert_memories(store, user_ids: set[str]) -> tuple[int, int]:
    users_done = facts = 0
    for namespace in await store.alist_namespaces(prefix=("users",), suffix=("memories",)):
        user_id = namespace[1]
        if user_id not in user_ids:
            continue  # account deleted; forget_user removes its items
        items = await store.asearch(namespace, limit=10_000)
        print(f"  {user_id}: {len(items)} memories", flush=True)
        for start in range(0, len(items), BATCH):
            batch = items[start:start + BATCH]
            usage = UsageCounter()
            told = "\n".join(f"- {item.value['text']}" for item in batch)
            facts += await extract_facts(user_id, f"Facts about me you noted earlier:\n{told}", "Noted.",
                                         [usage], source="import")
            await add_usage(store, user_id, usage)
            for item in batch:  # only after their facts are saved
                await store.adelete(namespace, item.key)
        users_done += 1
    return users_done, facts


async def card_documents(store) -> int:
    with get_engine().connect() as conn:
        rows = conn.execute(select(documents.c.id, documents.c.user_id, documents.c.filename, documents.c.chunk_count)
                            .where(documents.c.status == "ready", documents.c.summary.is_(None))).all()
    print(f"  documents without a card: {len(rows)}", flush=True)
    written = 0
    for document_id, user_id, filename, chunk_count in rows:
        chunks = await asyncio.to_thread(get_vector_store().get_by_ids, chunk_ids(document_id, min(chunk_count, CARD_CHUNKS)))
        chunks.sort(key=lambda chunk: int(chunk.id.rsplit(":", 1)[1]))  # get_by_ids doesn't keep the order
        if chunks and await write_card(store, document_id, user_id, filename, chunks):
            written += 1
            print(f"    {filename}", flush=True)
    return written


async def main() -> None:
    init_db()
    with get_engine().connect() as conn:
        user_ids = set(conn.execute(select(users.c.id)).scalars())
    async with AsyncExitStack() as stack:
        _, store = await open_persistence(stack)
        print("Memories:")
        converted, facts = await convert_memories(store, user_ids)
        print(f"  {converted} users, {facts} facts saved")
        print("Document cards:")
        print(f"  {await card_documents(store)} written")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.run(main(), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(main())
