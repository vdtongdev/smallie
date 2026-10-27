import psycopg
import pytest

from app.core.config import get_settings


@pytest.fixture(autouse=True)
def _no_judge_model_from_env(monkeypatch):
    # Tests run on the default config, independent of a developer's .env (env vars beat .env).
    monkeypatch.setenv("OPENROUTER_JUDGE_MODEL", "")
    monkeypatch.setenv("MODEL_KEY_SECRET", "x8bnmq0DdhOZ5kA3eWj1tTkpb3B8t7sT9bW2g1h0m4c=")  # startup requires one
    get_settings.cache_clear()
    yield
    monkeypatch.undo()
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _plan_call_fails(monkeypatch):
    """No test reaches a real model through `ask_plan`: the plan call fails unless a test scripts it, so the team runs the fallback plan (every participant, independent)."""
    async def boom(*args, **kwargs):
        raise RuntimeError("no plan model in tests")

    from types import SimpleNamespace

    from app.orchestration import team

    monkeypatch.setattr(team, "get_chat_model", lambda *a, **k: SimpleNamespace(
        with_structured_output=lambda schema, method: SimpleNamespace(ainvoke=boom)))


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
