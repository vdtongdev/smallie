"""Conversation rows (the chat sidebar). Messages themselves live in the LangGraph checkpointer."""

import uuid

from sqlalchemy import delete, func, select, update

from app.core.db import conversations, get_engine


def create_conversation(user_id: str, kind: str, first_message: str) -> dict:
    title = " ".join(first_message.split())[:60] or "New chat"
    row = {"id": str(uuid.uuid4()), "user_id": user_id, "kind": kind, "title": title}
    with get_engine().begin() as conn:
        conn.execute(conversations.insert().values(**row))
    return row


def get_conversation(user_id: str, conversation_id: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(conversations).where(
                conversations.c.id == conversation_id, conversations.c.user_id == user_id
            )
        ).mappings().first()
    return dict(row) if row else None


def list_conversations(user_id: str, kind: str | None = None) -> list[dict]:
    query = select(conversations.c.id, conversations.c.title, conversations.c.kind, conversations.c.updated_at).where(
        conversations.c.user_id == user_id
    )
    if kind:
        query = query.where(conversations.c.kind == kind)
    with get_engine().connect() as conn:
        rows = conn.execute(query.order_by(conversations.c.updated_at.desc())).mappings().all()
    return [dict(r) for r in rows]


def touch_conversation(conversation_id: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            update(conversations).where(conversations.c.id == conversation_id).values(updated_at=func.now())
        )


def delete_conversation(user_id: str, conversation_id: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            delete(conversations).where(
                conversations.c.id == conversation_id, conversations.c.user_id == user_id
            )
        )
