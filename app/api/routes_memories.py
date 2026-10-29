"""Let users see and delete what the assistant learned about them (their knowledge graph's facts)."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import get_current_user
from app.core.graph import delete_fact, list_facts
from app.core.snapshot import clear_snapshots

router = APIRouter(tags=["memories"])


@router.get("/memories")
async def list_memories(user_id: str = Depends(get_current_user)):
    return await asyncio.to_thread(list_facts, user_id)


@router.delete("/memories/{memory_id}", status_code=204)
async def forget(memory_id: str, user_id: str = Depends(get_current_user)):
    if not await asyncio.to_thread(delete_fact, user_id, memory_id):
        raise HTTPException(404, "Memory not found")
    await asyncio.to_thread(clear_snapshots, user_id)
