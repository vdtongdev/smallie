"""Give the chats, documents and memories of old anonymous users (anon-...) to the admin.

Before accounts existed, browsers without a login got an anonymous user. Run this once
after setting APP_PASSWORD; it is safe to run again.

Usage:  uv run python -m scripts.claim_anonymous_data
"""

import asyncio
import sys
from contextlib import AsyncExitStack

import psycopg

from app.accounts import ADMIN_USER_ID, ensure_admin
from app.config import get_settings
from app.db import init_db
from app.memory import open_persistence


def move_rows(conn: psycopg.Connection, anon_id: str) -> tuple[int, int]:
    chats = conn.execute("UPDATE conversations SET user_id = %s WHERE user_id = %s", (ADMIN_USER_ID, anon_id))
    docs = conn.execute("UPDATE documents SET user_id = %s WHERE user_id = %s", (ADMIN_USER_ID, anon_id))
    if conn.execute("SELECT to_regclass('public.langchain_pg_embedding')").fetchone()[0]:
        conn.execute(
            "UPDATE langchain_pg_embedding SET cmetadata = jsonb_set(cmetadata, '{user_id}', to_jsonb(%s::text)) "
            "WHERE cmetadata->>'user_id' = %s",
            (ADMIN_USER_ID, anon_id),
        )
    return chats.rowcount, docs.rowcount


async def move_store_items(anon_ids: list[str]) -> int:
    """Memories and indexed chat history; re-put through the store so embeddings stay valid."""
    moved = 0
    async with AsyncExitStack() as stack:
        _, store = await open_persistence(stack)
        for anon_id in anon_ids:
            while items := await store.asearch(("users", anon_id), limit=100):
                for item in items:
                    namespace = ("users", ADMIN_USER_ID, *item.namespace[2:])
                    await store.aput(namespace, item.key, item.value)
                    await store.adelete(item.namespace, item.key)
                    moved += 1
    return moved


def main() -> None:
    settings = get_settings()
    if not settings.app_password:
        sys.exit("Set APP_PASSWORD first; the admin account receives the data.")
    init_db()
    ensure_admin(settings.app_password)
    with psycopg.connect(settings.database_url) as conn:
        anon_ids = [r[0] for r in conn.execute("SELECT id FROM users WHERE id LIKE 'anon-%'")]
    if not anon_ids:
        print("No anonymous users found.")
        return
    print(f"Anonymous users: {len(anon_ids)}")

    if sys.platform == "win32":
        memories = asyncio.run(move_store_items(anon_ids), loop_factory=asyncio.SelectorEventLoop)
    else:
        memories = asyncio.run(move_store_items(anon_ids))
    print(f"  memories/history items moved: {memories}")

    chats = docs = 0
    with psycopg.connect(settings.database_url) as conn:  # one transaction
        for anon_id in anon_ids:
            c, d = move_rows(conn, anon_id)
            chats, docs = chats + c, docs + d
        conn.execute("DELETE FROM users WHERE id = ANY(%s)", (anon_ids,))
    print(f"  chats moved: {chats}\n  documents moved: {docs}")


if __name__ == "__main__":
    main()
