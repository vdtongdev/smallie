"""Let users see and delete what the assistant remembers about them."""

from fastapi import APIRouter, Depends, Request

from app.auth import get_current_user
from app.memory import memory_namespace

router = APIRouter(tags=["memories"])


@router.get("/memories")
async def list_memories(request: Request, user_id: str = Depends(get_current_user)):
    items = await request.app.state.store.asearch(memory_namespace(user_id), limit=200)
    return [{"id": i.key, "text": i.value.get("text", ""), "created_at": i.created_at} for i in items]


@router.delete("/memories/{memory_id}", status_code=204)
async def forget(memory_id: str, request: Request, user_id: str = Depends(get_current_user)):
    await request.app.state.store.adelete(memory_namespace(user_id), memory_id)
