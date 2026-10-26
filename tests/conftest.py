import psycopg
import pytest

from app.core.config import get_settings


@pytest.fixture(autouse=True)
def _no_judge_model_from_env(monkeypatch):
    # Tests run on the default config, independent of a developer's .env (env vars beat .env).
    monkeypatch.setenv("OPENROUTER_JUDGE_MODEL", "")
    get_settings.cache_clear()
    yield
    monkeypatch.undo()
    get_settings.cache_clear()


def _db_reachable() -> bool:
    try:
        with psycopg.connect(get_settings().database_url, connect_timeout=2):
            return True
    except psycopg.OperationalError:
        return False


requires_db = pytest.mark.skipif(not _db_reachable(), reason="Postgres not running (docker compose up -d)")


def local_agents(**overrides):
    """Every registered agent built in-process (call it after patching the model), instead of the
    remote agent servers; `overrides` replaces some, e.g. sql=RemoteAgent(...)."""
    from app.agents.registry import AGENTS

    return {spec.route: overrides.get(spec.route) or spec.build() for spec in AGENTS}
