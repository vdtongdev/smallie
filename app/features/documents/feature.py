import asyncio
import shutil

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import documents, get_engine
from app.features.base import Feature
from app.features.documents import routes
from app.features.documents.ingest import delete_document_chunks, get_vector_store


def _delete_documents(user_id: str) -> None:
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(documents.c.id, documents.c.chunk_count).where(documents.c.user_id == user_id)
        ).all()
    for document_id, chunk_count in rows:
        delete_document_chunks(document_id, chunk_count)
    shutil.rmtree(get_settings().upload_dir / user_id, ignore_errors=True)


async def delete_user_data(user_id: str) -> None:
    """Chunks and uploaded files; the rows go with the user (ON DELETE CASCADE)."""
    await asyncio.to_thread(_delete_documents, user_id)


def start(stack) -> None:
    """Create the vector tables and collection now, not at the first upload: the agent servers'
    read-only role can't create them, and grant_reader needs them."""
    get_vector_store()


FEATURE = Feature(name="documents", routers=(routes.router,), start=start, delete_user_data=delete_user_data)
