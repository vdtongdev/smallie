import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, UploadFile
from sqlalchemy import delete, select

from app.core.auth import get_current_user
from app.core.config import get_settings
from app.core.db import documents, get_engine
from app.core.graph import drop_orphans
from app.features.documents.card import process_document
from app.features.documents.ingest import SUPPORTED_SUFFIXES, delete_document_chunks

router = APIRouter(tags=["documents"])

_PUBLIC_COLUMNS = (documents.c.id, documents.c.filename, documents.c.status,
                   documents.c.error, documents.c.chunk_count, documents.c.created_at)


@router.post("/documents", status_code=202)
def upload(files: list[UploadFile], background: BackgroundTasks, request: Request,
           user_id: str = Depends(get_current_user)):
    settings = get_settings()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    for f in files:
        if Path(f.filename or "").suffix.lower() not in SUPPORTED_SUFFIXES:
            raise HTTPException(400, f"{f.filename}: only PDF and DOCX files are supported")
        if f.size is not None and f.size > max_bytes:
            raise HTTPException(413, f"{f.filename}: larger than {settings.max_upload_mb} MB")

    created = []
    user_dir = settings.upload_dir / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    for f in files:
        doc_id = str(uuid.uuid4())
        filename = Path(f.filename).name
        path = user_dir / f"{doc_id}{Path(filename).suffix.lower()}"
        with path.open("wb") as out:
            shutil.copyfileobj(f.file, out)
        row = {"id": doc_id, "user_id": user_id, "filename": filename, "path": str(path), "status": "processing"}
        with get_engine().begin() as conn:
            conn.execute(documents.insert().values(**row))
        background.add_task(process_document, request.app.state.store, doc_id, user_id, path, filename)
        created.append({"id": doc_id, "filename": filename, "status": "processing"})
    return created


@router.get("/documents")
def list_documents(user_id: str = Depends(get_current_user)):
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(*_PUBLIC_COLUMNS).where(documents.c.user_id == user_id).order_by(documents.c.created_at.desc())
        ).mappings().all()
    return [dict(r) for r in rows]


@router.delete("/documents/{document_id}", status_code=204)
def remove(document_id: str, user_id: str = Depends(get_current_user)):
    with get_engine().begin() as conn:
        row = conn.execute(
            select(documents).where(documents.c.id == document_id, documents.c.user_id == user_id)
        ).mappings().first()
        if not row:
            raise HTTPException(404, "Document not found")
        if row["status"] == "processing":
            raise HTTPException(409, "Document is still being processed")
        delete_document_chunks(document_id, row["chunk_count"])
        conn.execute(delete(documents).where(documents.c.id == document_id))  # cascades to its graph node
    drop_orphans(user_id)  # entities only this file mentioned
    Path(row["path"]).unlink(missing_ok=True)
