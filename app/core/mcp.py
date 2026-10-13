"""MCP tools for agents (langchain-mcp-adapters).

- Remote servers (streamable HTTP / SSE, e.g. Tavily) are stateless: each tool call opens
  its own short session, so a server restart or a dropped connection doesn't break later calls.
- Local servers (our own FastMCP objects) run in-process over MCP's memory transport. That
  is still the MCP protocol, but needs no subprocess: stdio servers can't start on Windows
  under the SelectorEventLoop psycopg needs. Their sessions live on the app's exit stack.

A server that fails to load is logged and skipped, so one unreachable server never stops
the app from starting.
"""

import logging
from contextlib import AsyncExitStack

from langchain_core.tools import BaseTool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

log = logging.getLogger(__name__)


async def open_mcp_tools(
    stack: AsyncExitStack, servers: dict[str, dict], local: dict[str, FastMCP]
) -> list[BaseTool]:
    """LangChain tools of every server that loads, named "<server>_<tool>"."""
    tools: list[BaseTool] = []
    client = MultiServerMCPClient(servers, tool_name_prefix=True)
    for name in servers:
        try:
            tools += await client.get_tools(server_name=name)
        except Exception as exc:  # noqa: BLE001 - an unreachable server must not stop startup
            log.warning("MCP server %r skipped: %s", name, _reason(exc))
    for name, server in local.items():
        try:
            session = await stack.enter_async_context(create_connected_server_and_client_session(server))
            tools += await load_mcp_tools(session, server_name=name, tool_name_prefix=True)
        except Exception as exc:  # noqa: BLE001
            log.warning("Local MCP server %r skipped: %s", name, _reason(exc))
    log.info("MCP tools: %s", ", ".join(tool.name for tool in tools) or "(none)")
    return tools


def _reason(exc: BaseException) -> str:
    # anyio wraps connection failures in ExceptionGroups; show the first real cause.
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return f"{type(exc).__name__}: {exc}"
