"""PDF/DOCX -> text chunks -> embeddings in pgvector, tagged with the owner's user_id."""

import logging
from functools import lru_cache
from pathlib import Path

import docx2txt
from langchain_core.documents import Document
from langchain_postgres import PGVector
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader
from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.db import documents, get_engine, pool_args
from app.core.llm import get_embeddings
from app.features.documents.images import IMAGE_SUFFIXES

log = logging.getLogger(__name__)

COLLECTION = "documents"
SUPPORTED_SUFFIXES = {".pdf", ".docx"} | IMAGE_SUFFIXES

# ~800 characters keeps chunks well under 512 tokens even for Vietnamese text.
_splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=120)


@lru_cache
def get_vector_store() -> PGVector:
    return PGVector(
        embeddings=get_embeddings(),
        connection=get_settings().sqlalchemy_url,
        collection_name=COLLECTION,
        use_jsonb=True,
        engine_args=pool_args(),
        # init_db creates it; the agent servers' agent_reader role can't (read-only transactions).
        create_extension=False,
    )


def load_and_split(path: Path, filename: str) -> list[Document]:
    """Extract text and split into chunks. Metadata: filename and 1-based page (PDF only)."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        pages = [
            Document(page.extract_text() or "", metadata={"filename": filename, "page": number})
            for number, page in enumerate(PdfReader(path).pages, start=1)
        ]
    elif suffix == ".docx":
        pages = [Document(docx2txt.process(str(path)), metadata={"filename": filename})]
    elif suffix in IMAGE_SUFFIXES:
        pages = []  # no text; the reading becomes image chunks (image_chunks)
    else:
        raise ValueError(f"Unsupported file type: {suffix}")

    pages = [p for p in pages if p.page_content.strip()]
    return _splitter.split_documents(pages)


def chunk_ids(document_id: str, count: int) -> list[str]:
    return [f"{document_id}:{i}" for i in range(count)]


def image_chunks(readings: list[str | None], pages: list[int | None], filename: str) -> list[Document]:
    """One chunk per reading, numbered 1..n in file order (an unread image keeps its number)."""
    found = []
    for number, (reading, page) in enumerate(zip(readings, pages), start=1):
        if not reading or not reading.strip():
            continue
        label = f"[Image {number}, p. {page}]" if page else f"[Image {number}]"
        metadata = {"filename": filename, "kind": "image", "image": number}
        if page:
            metadata["page"] = page
        found.append(Document(f"{label}\n{reading.strip()}", metadata=metadata))
    return _splitter.split_documents(found)


def ingest_document(document_id: str, user_id: str, path: Path, filename: str) -> list[Document] | None:
    """The text chunks of one uploaded file ([] when it has none), or None after marking it failed."""
    try:
        return load_and_split(path, filename)
    except Exception as exc:  # noqa: BLE001 - any failure is reported to the user via status
        log.exception("Ingestion failed for %s", filename)
        _set_status(document_id, "failed", error=str(exc)[:500])
        return None


def index_document(document_id: str, user_id: str, text_chunks: list[Document], image_chunks: list[Document],
                   image_count: int, images_unread: int) -> list[Document]:
    """Index text then image chunks and record the outcome on the file's row; returns the chunks
    ([] when the file failed or was deleted meanwhile)."""
    chunks = [*text_chunks, *image_chunks]
    try:
        if not chunks:
            raise ValueError("No text or readable images found.")
        with get_engine().connect() as conn:
            if conn.execute(select(documents.c.id).where(documents.c.id == document_id,
                                                         documents.c.user_id == user_id)).first() is None:
                return []  # deleted while the images were being read
        for chunk in chunks:
            chunk.metadata.update(user_id=user_id, document_id=document_id)
        get_vector_store().add_documents(chunks, ids=chunk_ids(document_id, len(chunks)))
        if not _set_status(document_id, "ready", chunk_count=len(chunks), image_count=image_count,
                           images_unread=images_unread, error=None):
            delete_document_chunks(document_id, len(chunks))  # deleted while indexing
            return []
        return chunks
    except Exception as exc:  # noqa: BLE001 - any failure is reported to the user via status
        log.exception("Indexing failed for %s", document_id)
        _set_status(document_id, "failed", error=str(exc)[:500])
        return []


def delete_document_chunks(document_id: str, chunk_count: int) -> None:
    if chunk_count:
        get_vector_store().delete(ids=chunk_ids(document_id, chunk_count))


def _set_status(document_id: str, status: str, **values) -> bool:
    """False when the row no longer exists."""
    with get_engine().begin() as conn:
        return conn.execute(update(documents).where(documents.c.id == document_id).values(status=status, **values)).rowcount > 0
