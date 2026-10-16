"""Re-embed every document chunk, store item and graph entity after changing OPENROUTER_EMBED_MODEL or EMBED_DIM.

Usage (app stopped):  uv run python -m scripts.reindex
"""

import asyncio
import sys
from contextlib import AsyncExitStack
from pathlib import Path

import psycopg
from sqlalchemy import select, text, update

from app.core.config import get_settings
from app.core.db import documents, get_engine, graph_nodes, init_db
from app.core.llm import get_embeddings
from app.features.documents.ingest import delete_document_chunks, ingest_document
from app.core.memory import MAX_INDEXED_DIMS, open_persistence


def reindex_documents() -> None:
    with get_engine().connect() as conn:
        rows = conn.execute(select(documents).where(documents.c.status != "processing")).mappings().all()
    for row in rows:
        print(f"  {row['filename']} ...", end=" ", flush=True)
        delete_document_chunks(row["id"], row["chunk_count"])
        ingest_document(row["id"], row["user_id"], Path(row["path"]), row["filename"])
        with get_engine().connect() as conn:
            status = conn.execute(select(documents.c.status).where(documents.c.id == row["id"])).scalar_one()
        print(status)


def resize_memory_vectors(dims: int) -> None:
    """Clear stored memory embeddings and change the column to the new dimension."""
    with psycopg.connect(get_settings().database_url, autocommit=True) as conn:
        exists = conn.execute("SELECT to_regclass('public.store_vectors')").fetchone()[0]
        if not exists:
            return
        conn.execute("DROP INDEX IF EXISTS store_vectors_embedding_idx")
        conn.execute("DELETE FROM store_vectors")
        conn.execute(f"ALTER TABLE store_vectors ALTER COLUMN embedding TYPE vector({int(dims)})")
        if dims <= MAX_INDEXED_DIMS:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS store_vectors_embedding_idx "
                "ON store_vectors USING hnsw (embedding vector_cosine_ops)"
            )


def reembed_graph(dims: int) -> int:
    """Resize graph_nodes.embedding and re-embed every entity name."""
    with get_engine().begin() as conn:
        conn.execute(text(f"ALTER TABLE graph_nodes ALTER COLUMN embedding TYPE vector({int(dims)}) USING NULL"))
        rows = conn.execute(select(graph_nodes.c.id, graph_nodes.c.name).where(graph_nodes.c.kind == "entity")).all()
        for start in range(0, len(rows), 64):
            batch = rows[start:start + 64]
            for (node_id, _), vector in zip(batch, get_embeddings().embed_documents([name for _, name in batch])):
                conn.execute(update(graph_nodes).where(graph_nodes.c.id == node_id).values(embedding=vector))
    return len(rows)


async def reembed_memories() -> int:
    async with AsyncExitStack() as stack:
        _, store = await open_persistence(stack)
        items = await store.asearch(("users",), limit=100_000)
        for item in items:
            await store.aput(item.namespace, item.key, item.value)
        return len(items)


def main() -> None:
    settings = get_settings()
    if not settings.openrouter_api_key:
        sys.exit("OPENROUTER_API_KEY is not set.")
    print(f"Embedding model: {settings.openrouter_embed_model} ({settings.embed_dim} dims)")
    init_db()
    print("Documents:")
    reindex_documents()
    print("Memories:")
    resize_memory_vectors(settings.embed_dim)
    if sys.platform == "win32":
        count = asyncio.run(reembed_memories(), loop_factory=asyncio.SelectorEventLoop)
    else:
        count = asyncio.run(reembed_memories())
    print(f"  re-embedded {count} memories")
    print(f"Knowledge graph: re-embedded {reembed_graph(settings.embed_dim)} entities")


if __name__ == "__main__":
    main()
