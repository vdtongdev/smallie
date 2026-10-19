from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.main import STATIC_DIR, RevalidatedStaticFiles


def test_browsers_recheck_the_ui_files_on_every_load():
    app = FastAPI()
    app.mount("/", RevalidatedStaticFiles(directory=STATIC_DIR, html=True))
    client = TestClient(app)
    first = client.get("/app.js")
    # Without it browsers guess a freshness time and keep running an old app.js after an update.
    assert first.headers["cache-control"] == "no-cache"
    assert client.get("/", headers={"if-none-match": client.get("/").headers["etag"]}).status_code == 304  # cheap
