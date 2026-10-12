import psycopg
import pytest

from app.config import get_settings


def _db_reachable() -> bool:
    try:
        with psycopg.connect(get_settings().database_url, connect_timeout=2):
            return True
    except psycopg.OperationalError:
        return False


requires_db = pytest.mark.skipif(not _db_reachable(), reason="Postgres not running (docker compose up -d)")
