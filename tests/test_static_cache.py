from fastapi.testclient import TestClient

from app.api.main import app

client = TestClient(app)  # no `with`: the lifespan (DB, agents) is not started


def test_static_files_must_be_revalidated():
    for path in ("/", "/index.html", "/app.js"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.headers["cache-control"] == "no-cache", path


def test_api_routes_keep_their_headers():
    r = client.get("/api/me")
    assert "cache-control" not in r.headers
