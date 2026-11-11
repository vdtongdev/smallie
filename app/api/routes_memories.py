"""Let users see and delete what the assistant learned about them (their knowledge graph's facts)
and the notes the agents wrote for themselves, newest first (a note row carries its `agent`)."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import get_current_user
from app.core.graph import delete_fact, list_facts, list_notes
from app.core.snapshot import clear_snapshots

router = APIRouter(tags=["memories"])


@router.get("/memories")
async def list_memories(user_id: str = Depends(get_current_user)):
    facts = await asyncio.to_thread(list_facts, user_id)
    notes = await asyncio.to_thread(list_notes, user_id)
    return sorted(facts + notes, key=lambda row: row["created_at"], reverse=True)


@router.delete("/memories/{memory_id}", status_code=204)
async def forget(memory_id: str, user_id: str = Depends(get_current_user)):
    if not await asyncio.to_thread(delete_fact, user_id, memory_id):
        raise HTTPException(404, "Memory not found")
    await asyncio.to_thread(clear_snapshots, user_id)
