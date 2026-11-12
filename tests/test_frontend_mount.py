import logging

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.main import app as real_app
from app.api.main import mount_frontend


def make_dist(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text('<div id="root"></div>')
    (dist / "assets" / "index-abc123.js").write_text("console.log(1)")
    return dist


def make_client(tmp_path):
    app = FastAPI()
    assert mount_frontend(app, make_dist(tmp_path)) is True
    return TestClient(app)  # no `with`: the lifespan is not started


def test_mount_serves_index_without_cache(tmp_path):
    r = make_client(tmp_path).get("/next/")
    assert r.status_code == 200
    assert 'id="root"' in r.text
    assert r.headers["cache-control"] == "no-cache"


def test_assets_are_cached_for_a_year(tmp_path):
    r = make_client(tmp_path).get("/next/assets/index-abc123.js")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_next_without_slash_reaches_the_page(tmp_path):
    r = make_client(tmp_path).get("/next", follow_redirects=True)
    assert r.status_code == 200
    assert 'id="root"' in r.text


def test_missing_asset_is_404(tmp_path):
    assert make_client(tmp_path).get("/next/assets/nope.js").status_code == 404


def test_missing_dist_skips_mount(tmp_path, caplog):
    app = FastAPI()
    with caplog.at_level(logging.WARNING):
        assert mount_frontend(app, tmp_path / "absent") is False
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    assert TestClient(app).get("/next/").status_code == 404


def test_real_app_keeps_old_ui_at_root():
    r = TestClient(real_app).get("/")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"
