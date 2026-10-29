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


def _usage_part(input_tokens: int, output_tokens: int, cached_tokens: int | None) -> dict:
    return {"input_tokens": input_tokens, "output_tokens": output_tokens, "cached_tokens": cached_tokens}


def _usage_of(row) -> dict:
    """`{"reply", "chat"}` from a conversation row; cached tokens are None while no reply reported them."""
    chat = _usage_part(row.input_tokens, row.output_tokens, row.cached_tokens if row.cache_known else None)
    return {"reply": row.last_usage, "chat": chat}


def add_conversation_usage(user_id: str, conversation_id: str, totals: dict) -> dict | None:
    """Add one reply's `UsageCounter.totals()` to the chat's numbers. None when the chat is not this user's."""
    cached = totals.get("cached_tokens")
    reply = _usage_part(totals.get("input_tokens", 0), totals.get("output_tokens", 0), cached)
    with get_engine().begin() as conn:
        row = conn.execute(
            update(conversations)
            .where(conversations.c.id == conversation_id, conversations.c.user_id == user_id)
            .values(
                input_tokens=conversations.c.input_tokens + reply["input_tokens"],
                output_tokens=conversations.c.output_tokens + reply["output_tokens"],
                cached_tokens=conversations.c.cached_tokens + (cached or 0),
                cache_known=conversations.c.cache_known | (cached is not None),
                last_usage=reply,
            )
            .returning(conversations.c.input_tokens, conversations.c.output_tokens, conversations.c.cached_tokens,
                       conversations.c.cache_known, conversations.c.last_usage)
        ).first()
    return _usage_of(row) if row else None


def get_conversation_usage(user_id: str, conversation_id: str) -> dict | None:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(conversations.c.input_tokens, conversations.c.output_tokens, conversations.c.cached_tokens,
                   conversations.c.cache_known, conversations.c.last_usage)
            .where(conversations.c.id == conversation_id, conversations.c.user_id == user_id)
        ).first()
    return _usage_of(row) if row else None
