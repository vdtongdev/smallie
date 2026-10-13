"""Admin-only account management: list accounts, reset a password, delete an account."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.core import accounts, auth
from app.core.conversations import list_conversations
from app.features.registry import FEATURES
from app.core.memory import forget_user

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(auth.get_admin_user)])


class PasswordRequest(BaseModel):
    password: str = Field(min_length=8, max_length=128)


@router.get("/accounts")
def list_accounts():
    return accounts.list_accounts()


@router.post("/accounts/{user_id}/password", status_code=204)
def reset_password(user_id: str, body: PasswordRequest):
    if user_id == accounts.ADMIN_USER_ID:
        raise HTTPException(400, "The admin password is APP_PASSWORD in .env")
    if not accounts.set_password(user_id, body.password):
        raise HTTPException(404, "Account not found")


@router.delete("/accounts/{user_id}", status_code=204)
async def delete_account(user_id: str, request: Request):
    """Delete the account and everything it owns: chats, memories, documents and files."""
    if user_id == accounts.ADMIN_USER_ID:
        raise HTTPException(400, "The admin account can't be deleted")
    if not await asyncio.to_thread(accounts.user_exists, user_id):
        raise HTTPException(404, "Account not found")
    for conversation in await asyncio.to_thread(list_conversations, user_id):
        await request.app.state.checkpointer.adelete_thread(conversation["id"])
    await forget_user(request.app.state.store, user_id)
    for feature in FEATURES:
        if feature.delete_user_data:
            await feature.delete_user_data(user_id)
    await asyncio.to_thread(accounts.delete_user_row, user_id)
    auth.forget_user(user_id)  # their open sessions now get 401

