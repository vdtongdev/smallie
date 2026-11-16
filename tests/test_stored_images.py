import io
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.documents import Document
from langgraph.store.memory import InMemoryStore
from PIL import Image
from sqlalchemy import select, text

from app.core.auth import get_current_user
from app.core.db import READER_TABLES, document_images, documents, get_engine
from app.features.documents import card as card_module
from app.features.documents import ingest as ingest_module
from app.features.documents import routes
from app.features.documents.stored_images import load_image, make_thumb
from tests.conftest import requires_db
from tests.test_graph import make_user  # noqa: F401  (fixture)


def _jpeg(width: int, height: int) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), "red").save(out, "JPEG")
    return out.getvalue()


class FakeStore:
    def add_documents(self, docs, ids=None):
        pass

    def delete(self, ids):
        pass


@pytest.fixture
def pipeline(monkeypatch):
    monkeypatch.setattr(ingest_module, "get_vector_store", lambda: FakeStore())

    async def write_card(*a, **k):
        return True

    monkeypatch.setattr(card_module, "write_card", write_card)


def _new_row(user_id: str, filename: str, status: str = "processing") -> str:
    document_id = str(uuid.uuid4())
    with get_engine().begin() as conn:
        conn.execute(documents.insert().values(id=document_id, user_id=user_id, filename=filename,
                                               path="x", status=status))
    return document_id


def _stored(document_id: str):
    with get_engine().connect() as conn:
        return conn.execute(select(document_images).where(document_images.c.document_id == document_id)).mappings().first()


def _fake_read(readings):
    async def read(user_id, images, callbacks):
        return readings
    return read


@requires_db
async def test_image_file_stores_image_and_thumb(make_user, pipeline, monkeypatch, tmp_path):
    user, path = make_user(), tmp_path / "a.png"
    path.write_bytes(b"x")
    big = _jpeg(1200, 600)
    monkeypatch.setattr(card_module, "images_in", lambda p: [(None, big)])
    monkeypatch.setattr(card_module, "read_images", _fake_read(["a red box"]))
    document_id = _new_row(user, "a.png")
    await card_module.process_document(InMemoryStore(), document_id, user, path, "a.png")
    row = _stored(document_id)
    assert row is not None and row["user_id"] == user and row["image"] == big
    assert max(Image.open(io.BytesIO(row["thumb"])).size) == 256


@requires_db
async def test_pdf_and_docx_store_nothing(make_user, pipeline, monkeypatch, tmp_path):
    user = make_user()
    monkeypatch.setattr(card_module, "ingest_document", lambda *a: [Document("Some text.")])
    monkeypatch.setattr(card_module, "images_in", lambda p: [(1, _jpeg(800, 600))])
    monkeypatch.setattr(card_module, "read_images", _fake_read(["a chart"]))
    for name in ("a.pdf", "a.docx"):
        path = tmp_path / name
        path.write_bytes(b"x")
        document_id = _new_row(user, name)
        await card_module.process_document(InMemoryStore(), document_id, user, path, name)
        assert _stored(document_id) is None


@requires_db
async def test_failed_file_stores_nothing(make_user, pipeline, monkeypatch, tmp_path):
    user, path = make_user(), tmp_path / "a.png"
    path.write_bytes(b"x")
    monkeypatch.setattr(card_module, "images_in", lambda p: [(None, _jpeg(800, 600))])
    monkeypatch.setattr(card_module, "read_images", _fake_read([None]))
    document_id = _new_row(user, "a.png")
    await card_module.process_document(InMemoryStore(), document_id, user, path, "a.png")
    with get_engine().connect() as conn:
        assert conn.execute(select(documents.c.status).where(documents.c.id == document_id)).scalar_one() == "failed"
    assert _stored(document_id) is None


def test_thumb_longest_side_is_256_and_small_images_keep_size():
    assert max(Image.open(io.BytesIO(make_thumb(_jpeg(900, 300)))).size) == 256
    assert max(Image.open(io.BytesIO(make_thumb(_jpeg(100, 50)))).size) == 100


def _save(user_id: str, document_id: str, jpeg: bytes):
    from app.features.documents.stored_images import save_image
    save_image(document_id, user_id, jpeg)


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    state = {"user": "nobody"}
    app.dependency_overrides[get_current_user] = lambda: state["user"]
    c = TestClient(app)
    c.state = state
    return c


@requires_db
def test_image_route_owner_gets_jpeg(make_user, client):
    user = make_user()
    document_id = _new_row(user, "a.png", "ready")
    full = _jpeg(1200, 600)
    _save(user, document_id, full)
    client.state["user"] = user
    r = client.get(f"/api/documents/{document_id}/image")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.headers["cache-control"] == "private, max-age=86400"
    assert r.content == full
    thumb = client.get(f"/api/documents/{document_id}/image?size=thumb")
    assert max(Image.open(io.BytesIO(thumb.content)).size) == 256


@requires_db
def test_image_route_other_user_404(make_user, client):
    owner, other = make_user(), make_user()
    document_id = _new_row(owner, "a.png", "ready")
    _save(owner, document_id, _jpeg(300, 300))
    client.state["user"] = other
    assert client.get(f"/api/documents/{document_id}/image").status_code == 404
    assert load_image(other, document_id, "full") is None


@requires_db
def test_image_route_pdf_id_404(make_user, client):
    user = make_user()
    document_id = _new_row(user, "a.pdf", "ready")
    client.state["user"] = user
    assert client.get(f"/api/documents/{document_id}/image").status_code == 404


@requires_db
def test_image_route_missing_404(make_user, client):
    client.state["user"] = make_user()
    assert client.get(f"/api/documents/{uuid.uuid4()}/image").status_code == 404


@requires_db
def test_delete_document_removes_image(make_user, client, tmp_path):
    user = make_user()
    document_id = _new_row(user, "a.png", "ready")
    _save(user, document_id, _jpeg(300, 300))
    client.state["user"] = user
    assert client.delete(f"/api/documents/{document_id}").status_code == 204
    assert _stored(document_id) is None


@requires_db
def test_list_has_image(make_user, client):
    user = make_user()
    with_image = _new_row(user, "a.png", "ready")
    without = _new_row(user, "a.pdf", "ready")
    _save(user, with_image, _jpeg(300, 300))
    client.state["user"] = user
    flags = {d["id"]: d["has_image"] for d in client.get("/api/documents").json()}
    assert flags == {with_image: True, without: False}


@requires_db
def test_reader_has_no_grant_on_document_images(make_user):
    make_user()  # runs init_db
    assert "document_images" not in READER_TABLES
    with get_engine().connect() as conn:
        if not conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = 'agent_reader'")).first():
            pytest.skip("no agent_reader role")
        assert not conn.execute(text("SELECT has_table_privilege('agent_reader', 'document_images', 'SELECT')")).scalar()
