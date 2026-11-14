import logging
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from app.api.main import add_next_redirect, mount_frontend

INDEX = '<div id="root"></div>'


def make_dist(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX)
    (dist / "assets" / "index-abc123.js").write_text("console.log(1)")
    return dist


def make_app(tmp_path, redirect=False):
    app = FastAPI()
    router = APIRouter()

    @router.get("/api/ping")
    def ping():
        return {"ok": True}

    app.include_router(router)
    if redirect:
        add_next_redirect(app)
    assert mount_frontend(app, make_dist(tmp_path)) is True
    return app


def make_client(tmp_path, redirect=False):
    return TestClient(make_app(tmp_path, redirect))  # no `with`: the lifespan is not started


def test_index_served_at_root(tmp_path):
    r = make_client(tmp_path).get("/")
    assert r.status_code == 200
    assert r.text == INDEX
    assert r.headers["cache-control"] == "no-cache"


def test_assets_cached_for_a_year(tmp_path):
    client = make_client(tmp_path)
    r = client.get("/assets/index-abc123.js")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert client.get("/assets/nope.js").status_code == 404


def test_api_routes_win_over_the_root_mount(tmp_path):
    client = make_client(tmp_path)
    assert client.get("/api/ping").json() == {"ok": True}
    r = client.get("/api/nope")
    assert r.status_code == 404
    assert INDEX not in r.text


def test_next_redirects_to_root(tmp_path):
    client = make_client(tmp_path, redirect=True)
    for src, dst in [
        ("/next", "/"),
        ("/next/", "/"),
        ("/next/?theme=shadcn", "/?theme=shadcn"),
        ("/next/assets/x.js", "/"),
    ]:
        r = client.get(src, follow_redirects=False)
        assert r.status_code == 308, src
        assert r.headers["location"] == dst, src


def test_next_redirects_on_head(tmp_path):
    client = make_client(tmp_path, redirect=True)
    for src, dst in [("/next", "/"), ("/next/?theme=shadcn", "/?theme=shadcn")]:
        r = client.head(src, follow_redirects=False)
        assert r.status_code == 308, src
        assert r.headers["location"] == dst, src


def test_missing_build_logs_and_api_still_works(tmp_path, caplog):
    app = FastAPI()
    router = APIRouter()

    @router.get("/api/ping")
    def ping():
        return {"ok": True}

    app.include_router(router)
    with caplog.at_level(logging.WARNING):
        assert mount_frontend(app, tmp_path / "absent") is False
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "npm run build" in warnings[0].getMessage()
    assert TestClient(app).get("/api/ping").status_code == 200


def test_real_app_has_no_old_ui():
    import app.api.main as main

    assert not hasattr(main, "STATIC_DIR")
    assert not (Path(main.__file__).resolve().parent.parent / "static").exists()
