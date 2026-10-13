"""MCP servers the research agent reads from, built from settings."""

import json
import logging

from mcp.server.fastmcp import FastMCP

from app.core.config import get_settings
from app.features.research.mcp_servers import knowledge, postgres

log = logging.getLogger(__name__)

TAVILY_MCP_URL = "https://mcp.tavily.com/mcp/"


def remote_servers() -> dict[str, dict]:
    """Tavily (when TAVILY_API_KEY is set) plus the servers in MCP_SERVERS."""
    s = get_settings()
    servers: dict[str, dict] = {}
    if s.tavily_api_key:
        servers["tavily"] = {
            "transport": "streamable_http",
            "url": TAVILY_MCP_URL,
            # A header rather than ?tavilyApiKey= keeps the key out of URLs and logs.
            "headers": {"Authorization": f"Bearer {s.tavily_api_key}"},
        }
    else:
        log.warning("TAVILY_API_KEY is not set; the research agent has no web search.")
    if s.mcp_servers.strip():
        try:
            extra = json.loads(s.mcp_servers)
            if not isinstance(extra, dict):
                raise ValueError("expected a JSON object of name -> connection")
            servers.update(extra)
        except ValueError as exc:
            log.warning("MCP_SERVERS ignored: %s", exc)
    return servers


def local_servers() -> dict[str, FastMCP]:
    """Our own in-process servers: public sources, plus EXTERNAL_DATABASE_URL when set."""
    s = get_settings()
    servers = {"knowledge": knowledge.mcp}
    if s.external_database_url:
        url = s.external_database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        servers["external_db"] = postgres.build_server(url)
    return servers
