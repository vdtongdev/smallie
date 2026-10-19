import psycopg
import pytest

from app.core.config import get_settings


def _db_reachable() -> bool:
    try:
        with psycopg.connect(get_settings().database_url, connect_timeout=2):
            return True
    except psycopg.OperationalError:
        return False


requires_db = pytest.mark.skipif(not _db_reachable(), reason="Postgres not running (docker compose up -d)")


@pytest.fixture(autouse=True)
def no_image_files(monkeypatch):
    """Routing never queries the DB for the image agent's files in tests; a test that needs some
    patches `image_files` again (its patch wins)."""
    from app.agents.image import agent as image_agent

    monkeypatch.setattr(image_agent, "image_files", lambda user_id: [])
