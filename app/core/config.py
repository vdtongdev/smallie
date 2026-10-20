from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    openrouter_api_key: str = ""
    openrouter_chat_model: str = "google/gemma-4-31b-it:free"
    openrouter_fallback_model: str = ""  # comma-separated, tried in order
    openrouter_quantizations: str = ""  # comma-separated provider precisions, e.g. "bf16,fp8"; empty = any
    openrouter_provider_sort: str = ""  # provider order: "throughput", "latency" or "price"; empty = OpenRouter's default (price-weighted)
    openrouter_orchestrator_model: str = ""  # the front-door agent; empty = chat model
    openrouter_embed_model: str = "baai/bge-m3"
    embed_dim: int = 1024

    database_url: str = "postgresql://smallie:smallie@localhost:5433/smallie"
    readonly_database_url: str = "postgresql://agent_readonly:readonly@localhost:5433/smallie"

    # Password of the "admin" account. Empty = no admin account.
    app_password: str = ""
    session_secret: str = ""
    session_https_only: bool = False  # set true when served over HTTPS
    max_accounts: int = 20  # accounts people can create, not counting admin

    upload_dir: Path = Path("uploads")
    max_upload_mb: int = 25
    sql_max_rows: int = 200
    # Model calls one agent may make per user message (each tool round trip is one call).
    max_model_calls: int = 6
    # Let agents see the user's other chats (recent turns in the prompt + search_chat_history).
    cross_chat_history: bool = True
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
