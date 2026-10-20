"""PDF/DOCX -> text chunks -> embeddings in pgvector, tagged with the owner's user_id."""

import logging
from functools import lru_cache
from pathlib import Path

import docx2txt
from langchain_core.documents import Document
from langchain_postgres import PGVector
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader
from sqlalchemy import update

from app.core.config import get_settings
from app.core.db import documents, get_engine, pool_args
from app.core.llm import get_embeddings

log = logging.getLogger(__name__)

COLLECTION = "documents"
SUPPORTED_SUFFIXES = {".pdf", ".docx"}

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
    else:
        raise ValueError(f"Unsupported file type: {suffix}")

    pages = [p for p in pages if p.page_content.strip()]
    return _splitter.split_documents(pages)


def chunk_ids(document_id: str, count: int) -> list[str]:
    return [f"{document_id}:{i}" for i in range(count)]


def ingest_document(document_id: str, user_id: str, path: Path, filename: str) -> list[Document]:
    """Index one uploaded file and record the outcome on its row; returns its chunks ([] on failure)."""
    try:
        chunks = load_and_split(path, filename)
        if not chunks:
            raise ValueError("No text found. Scanned/image-only PDFs need OCR, which is not supported yet.")
        for chunk in chunks:
            chunk.metadata.update(user_id=user_id, document_id=document_id)
        get_vector_store().add_documents(chunks, ids=chunk_ids(document_id, len(chunks)))
        _set_status(document_id, "ready", chunk_count=len(chunks), error=None)
        return chunks
    except Exception as exc:  # noqa: BLE001 - any failure is reported to the user via status
        log.exception("Ingestion failed for %s", filename)
        _set_status(document_id, "failed", error=str(exc)[:500])
        return []


def delete_document_chunks(document_id: str, chunk_count: int) -> None:
    if chunk_count:
        get_vector_store().delete(ids=chunk_ids(document_id, chunk_count))


def _set_status(document_id: str, status: str, **values) -> None:
    with get_engine().begin() as conn:
        conn.execute(update(documents).where(documents.c.id == document_id).values(status=status, **values))
