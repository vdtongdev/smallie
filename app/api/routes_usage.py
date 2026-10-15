"""Tokens and cost the current user's chats have used (app/core/usage.py)."""

from fastapi import APIRouter, Depends, Request

from app.core.auth import get_current_user
from app.core.usage import get_usage

router = APIRouter(tags=["usage"])


@router.get("/usage")
async def usage(request: Request, user_id: str = Depends(get_current_user)):
    return await get_usage(request.app.state.store, user_id)
