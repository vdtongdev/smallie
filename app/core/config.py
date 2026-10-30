import json
import logging
import os
from functools import lru_cache
from pathlib import Path

from langsmith import utils as ls_utils
from pydantic_settings import BaseSettings, SettingsConfigDict

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

log = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    embed_api_key: str = ""
    openrouter_embed_model: str = "baai/bge-m3"
    embed_dim: int = 1024

    model_key_secret: str = ""  # Fernet key; encrypts users' model API keys
    database_url: str = "postgresql://smallie:smallie@localhost:5433/smallie"
    readonly_database_url: str = "postgresql://agent_readonly:readonly@localhost:5433/smallie"

    # Password of the "admin" account. Empty = no admin account.
    app_password: str = ""
    session_secret: str = ""
    session_https_only: bool = False  # set true when served over HTTPS
    max_accounts: int = 20  # accounts people can create, not counting admin
    # Dev only: lets http and private addresses through as a user's model server (e.g. a local Ollama).
    allow_private_model_urls: bool = False

    upload_dir: Path = Path("uploads")
    max_upload_mb: int = 25
    sql_max_rows: int = 200
    # Model calls one agent may make per user message (each tool round trip is one call).
    max_model_calls: int = 6
    # Output tokens one model reply may produce, reasoning tokens included; ends a reply that repeats itself, e.g. the same tool calls over and over.
    max_output_tokens: int = 32768
    # Sampling temperature of chat models. At 0 a reply that starts repeating keeps repeating; higher values let weaker models slip words of another script into Vietnamese answers.
    chat_temperature: float = 0.3
    # Let agents see the user's other chats (recent turns in the prompt + search_chat_history).
    cross_chat_history: bool = True
    # A chat keeps its memory snapshot (facts, profile, other chats in the prompt) while it was active within this many minutes.
    memory_snapshot_minutes: int = 60
    # The orchestrator asks a new user about their work and goals in up to this many chats while the profile lacks them. 0 = off.
    bootstrap_chats: int = 3
    # Daily stock update, weekdays at this New York time (HH:MM). Empty = no background update.
    market_update_time: str = "17:30"

    # Research agent. Web search through Tavily's remote MCP server; empty = no web search.
    tavily_api_key: str = ""
    # Extra remote MCP servers as JSON, in MultiServerMCPClient format:
    # {"name": {"transport": "streamable_http", "url": "...", "headers": {...}}}
    mcp_servers: str = ""
    # Another Postgres the researcher may read through the external_db MCP server; empty = none.
    external_database_url: str = ""
    research_max_model_calls: int = 8
    # Model calls of the orchestrator per message: decide, then write up results (specialists' own calls not included).
    orchestrator_max_model_calls: int = 4

    # LangSmith tracing (debugging): every model and tool call of a chat, sent to smith.langchain.com.
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "smallie"

    # Remote agents: each specialist runs as its own Agent Server (app/agents/remote.py).
    agent_urls: str = ""  # JSON object route -> URL, e.g. {"sql": "http://localhost:2102"}
    service_token: str = ""  # shared secret the agent servers require (x-service-token)
    agent_timeout_s: float = 300  # read timeout of one specialist call; generous for cold starts
    db_pool_size: int = 5  # connections per SQLAlchemy engine; the agent services use 2
    # Loop guard: model calls one chat message may use across the orchestrator and every agent
    # server (they report theirs); past it, no further specialist is called.
    max_message_model_calls: int = 30
    # Team mode: conflict rounds per meeting point, tasks per message (follow-ups included), and its own model-call cap (same guard as above).
    team_max_rounds: int = 2
    team_max_tasks: int = 6
    team_max_message_model_calls: int = 30

    @property
    def agent_url_map(self) -> dict[str, str]:
        if not self.agent_urls.strip():
            return {}
        try:
            urls = json.loads(self.agent_urls)
        except ValueError as exc:
            raise ValueError(f"AGENT_URLS is not valid JSON: {exc}") from exc
        if not isinstance(urls, dict):
            raise ValueError("AGENT_URLS must be a JSON object of route -> URL")
        return urls

    @property
    def sqlalchemy_url(self) -> str:
        """Same database, but with the psycopg3 driver prefix SQLAlchemy needs."""
        return _with_psycopg_driver(self.database_url)

    @property
    def readonly_sqlalchemy_url(self) -> str:
        return _with_psycopg_driver(self.readonly_database_url)


def _with_psycopg_driver(url: str) -> str:
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def export_tracing(settings: Settings) -> None:
    """Turn on LangSmith tracing when the settings say so. The SDK reads os.environ, while .env
    only fills Settings, so the values are copied over."""
    if not settings.langsmith_tracing:
        return
    if not settings.langsmith_api_key:
        log.warning("LANGSMITH_TRACING is on but LANGSMITH_API_KEY is empty; tracing stays off.")
        return
    os.environ.update(LANGSMITH_TRACING="true", LANGSMITH_API_KEY=settings.langsmith_api_key,
                      LANGSMITH_PROJECT=settings.langsmith_project)
    ls_utils.get_env_var.cache_clear()  # the SDK caches what it read before
