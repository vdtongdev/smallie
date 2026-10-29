"""The memory part of a chat's prompt (profile, facts, other chats), frozen per chat so the prompt prefix stays cacheable."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy import or_, update

from app.core.accounts import get_profile
from app.core.config import get_settings
from app.core.conversations import list_conversations
from app.core.db import conversations, get_engine
from app.core.graph import recall_facts
from app.core.memory import load_recent_chats

log = logging.getLogger(__name__)


async def _user(user_id: str) -> str:
    profile = await asyncio.to_thread(get_profile, user_id)
    if not profile or not profile["username"]:
        return ""
    return f"{profile['name'] or profile['username']} (username: {profile['username']})"


async def _recent_chats(checkpointer: BaseCheckpointSaver, user_id: str, current_id: str) -> str:
    """Latest turns of the user's other chats; a failure here must not break the chat."""
    try:
        others = [c for c in await asyncio.to_thread(list_conversations, user_id) if c["id"] != current_id]
        return await load_recent_chats(checkpointer, others)
    except Exception:  # noqa: BLE001
        log.exception("Loading recent chats failed")
        return ""


async def _empty() -> str:
    return ""


async def build_snapshot(checkpointer: BaseCheckpointSaver | None, user_id: str, conversation_id: str) -> dict:
    recent = _recent_chats(checkpointer, user_id, conversation_id) \
        if get_settings().cross_chat_history and checkpointer else _empty()
    memories, recent_chats, user = await asyncio.gather(asyncio.to_thread(recall_facts, user_id), recent, _user(user_id))
    return {"user": user, "memories": memories, "recent_chats": recent_chats}


async def chat_snapshot(checkpointer: BaseCheckpointSaver | None, user_id: str, conversation: dict) -> dict:
    """The chat's saved snapshot while the chat was active within `memory_snapshot_minutes`; else a new one."""
    saved, active = conversation.get("memory_snapshot"), conversation.get("updated_at")
    window = timedelta(minutes=get_settings().memory_snapshot_minutes)
    if saved and active and active > datetime.now(timezone.utc) - window:
        return saved
    built_at = datetime.now(timezone.utc)
    snapshot = await build_snapshot(checkpointer, user_id, conversation["id"])
    await asyncio.to_thread(save_snapshot, user_id, conversation["id"], snapshot, built_at)
    return snapshot


def save_snapshot(user_id: str, conversation_id: str, snapshot: dict, built_at: datetime | None = None) -> None:
    """Not saved when a deletion cleared the chat after `built_at` (when the build started): it may hold deleted data."""
    now = datetime.now(timezone.utc)
    with get_engine().begin() as conn:
        conn.execute(update(conversations).where(
            conversations.c.id == conversation_id, conversations.c.user_id == user_id,
            or_(conversations.c.snapshot_at.is_(None), conversations.c.snapshot_at < (built_at or now)),
        ).values(memory_snapshot=snapshot, snapshot_at=now))


def clear_snapshots(user_id: str) -> None:
    """After a fact, file or chat is deleted: the next turn of every chat of the user rebuilds its snapshot."""
    with get_engine().begin() as conn:
        conn.execute(update(conversations).where(conversations.c.user_id == user_id).values(
            memory_snapshot=None, snapshot_at=datetime.now(timezone.utc)))  # the time marks the clear for save_snapshot
