from fastapi.testclient import TestClient

from app.api.main import app

client = TestClient(app)  # no `with`: the lifespan (DB, agents) is not started


def test_api_routes_keep_their_headers():
    r = client.get("/api/me")
    assert "cache-control" not in r.headers
