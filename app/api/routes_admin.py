"""Admin-only account management: list accounts, reset a password, delete an account."""

import asyncio
import shutil

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app import accounts, auth
from app.config import get_settings
from app.conversations import list_conversations
from app.db import documents, get_engine
from app.documents.ingest import delete_document_chunks
from app.memory import forget_user

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
    await asyncio.to_thread(_delete_documents, user_id)
    await asyncio.to_thread(accounts.delete_user_row, user_id)
    auth.forget_user(user_id)  # their open sessions now get 401


def _delete_documents(user_id: str) -> None:
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(documents.c.id, documents.c.chunk_count).where(documents.c.user_id == user_id)
        ).all()
    for document_id, chunk_count in rows:
        delete_document_chunks(document_id, chunk_count)
    shutil.rmtree(get_settings().upload_dir / user_id, ignore_errors=True)
