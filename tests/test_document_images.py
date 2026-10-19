import uuid
from io import BytesIO
from pathlib import Path

import pytest
from fastapi import HTTPException
from PIL import Image

from app.core.db import documents, get_engine, init_db, users
from app.features.documents import ingest
from app.features.documents.images import ImageReading
from app.features.documents.routes import image, list_documents
from tests.conftest import requires_db

READING = ImageReading(description="A red square.", doc_type="photo", summary="A red square.")


@pytest.fixture
def two_users():
    init_db()
    alice, bob = f"test-preview-{uuid.uuid4()}", f"test-preview-{uuid.uuid4()}"
    with get_engine().begin() as conn:
        conn.execute(users.insert(), [{"id": alice, "display_name": "A"}, {"id": bob, "display_name": "B"}])
    yield alice, bob
    with get_engine().begin() as conn:  # cascades to their documents
        conn.execute(users.delete().where(users.c.id.in_([alice, bob])))


def add_document(user_id: str, filename: str, preview: bytes | None = None) -> str:
    document_id = str(uuid.uuid4())
    with get_engine().begin() as conn:
        conn.execute(documents.insert().values(id=document_id, user_id=user_id, filename=filename,
                                               path=f"/tmp/{document_id}", status="ready", preview=preview))
    return document_id


@requires_db
def test_the_owner_gets_the_image_as_a_long_cached_jpeg(two_users):
    alice, _ = two_users
    response = image(add_document(alice, "receipt.png", b"\xff\xd8jpeg"), user_id=alice)
    assert response.body == b"\xff\xd8jpeg" and response.media_type == "image/jpeg"
    assert response.headers["cache-control"] == "private, max-age=31536000, immutable"


@requires_db
def test_no_image_for_another_user_or_a_document_without_one(two_users):
    alice, bob = two_users
    for document_id in (add_document(bob, "bob.png", b"jpeg"), add_document(alice, "report.pdf"), str(uuid.uuid4())):
        with pytest.raises(HTTPException) as error:
            image(document_id, user_id=alice)
        assert error.value.status_code == 404


@requires_db
def test_the_list_says_which_files_have_an_image(two_users):
    alice, _ = two_users
    add_document(alice, "receipt.png", b"jpeg")
    add_document(alice, "report.pdf")
    listed = {d["filename"]: d["has_image"] for d in list_documents(user_id=alice)}
    assert listed == {"receipt.png": True, "report.pdf": False}
    assert all("preview" not in d for d in list_documents(user_id=alice))  # the bytes stay server-side


def test_ingest_keeps_a_preview_of_image_files_only(tmp_path: Path, monkeypatch):
    saved = {}
    monkeypatch.setattr(ingest, "read_file_images", lambda path, filename, callbacks: [(None, READING)])
    monkeypatch.setattr(ingest, "_set_status", lambda document_id, status, **values: saved.update(values))
    monkeypatch.setattr(ingest, "get_vector_store", lambda: type("Store", (), {"add_documents": lambda *a, **k: None})())
    photo = tmp_path / "photo.png"
    Image.new("RGB", (400, 300), (200, 30, 30)).save(photo)
    ingest.ingest_document("doc", "u1", photo, "photo.png")
    assert Image.open(BytesIO(saved["preview"])).format == "JPEG"
    monkeypatch.setattr(ingest, "load_and_split", lambda path, filename, readings: [ingest.Document("text")])
    ingest.ingest_document("doc", "u1", tmp_path / "report.pdf", "report.pdf")
    assert saved["preview"] is None
