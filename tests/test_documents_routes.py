from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.features.documents import routes


class _Engine:
    @contextmanager
    def begin(self):
        yield SimpleNamespace(execute=lambda *a, **k: None)


@pytest.fixture
def env(monkeypatch, tmp_path):
    saved = {"value": object()}
    monkeypatch.setattr(routes, "load_settings", lambda user_id: saved["value"])
    monkeypatch.setattr(routes, "process_document", lambda *a, **k: None)
    monkeypatch.setattr(routes, "get_engine", lambda: _Engine())
    real = routes.get_settings()
    monkeypatch.setattr(routes, "get_settings", lambda: SimpleNamespace(
        max_upload_mb=real.max_upload_mb, upload_dir=tmp_path))
    app = FastAPI()
    app.state.store = None
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: "u1"
    return SimpleNamespace(client=TestClient(app), saved=saved, tmp=tmp_path)


def test_image_upload_accepted(env):
    r = env.client.post("/api/documents", files=[("files", ("a.jpg", b"x", "image/jpeg"))])
    assert r.status_code == 202


def test_unsupported_type_message(env):
    r = env.client.post("/api/documents", files=[("files", ("a.txt", b"x", "text/plain"))])
    assert r.status_code == 400
    assert "only PDF, DOCX or image files are supported" in r.json()["detail"]


def test_image_upload_needs_model_settings(env):
    env.saved["value"] = None
    r = env.client.post("/api/documents", files=[
        ("files", ("a.pdf", b"x", "application/pdf")), ("files", ("a.png", b"x", "image/png"))])
    assert r.status_code == 400
    assert "set your model in Settings before uploading images" in r.json()["detail"]
    assert not list(env.tmp.rglob("*.pdf"))  # nothing saved
