"""MCP server "external_db": read-only access to another Postgres (EXTERNAL_DATABASE_URL).

Every connection is read-only with a 10s statement timeout, and queries pass the same
single-SELECT check and row cap as the SQL agent (`execute_readonly`). Still, point it at a
role that can only SELECT what the agent may see.
"""

import asyncio

from mcp.server.fastmcp import FastMCP
from sqlalchemy import Engine, inspect

from app.agents.sql.tools import execute_readonly
from app.core.config import get_settings
from app.core.db import readonly_engine

SYSTEM_SCHEMAS = ("information_schema", "pg_catalog", "pg_toast")


def build_server(sqlalchemy_url: str) -> FastMCP:
    engine = readonly_engine(sqlalchemy_url)
    mcp = FastMCP("external_db")

    @mcp.tool()
    async def list_tables() -> str:
        """List the tables of the external database as schema.table, with their comments."""
        return await asyncio.to_thread(_list_tables, engine)

    @mcp.tool()
    async def describe_table(table: str) -> str:
        """Columns and types of one table of the external database.

        Args:
            table: "schema.table" (or just "table" for the public schema).
        """
        return await asyncio.to_thread(_describe_table, engine, table)

    @mcp.tool()
    async def query(sql: str) -> str:
        """Run one read-only SELECT on the external database; rows come back as a table.

        Args:
            sql: A single SELECT (or WITH ... SELECT) statement with schema-qualified table names.
        """
        return await asyncio.to_thread(execute_readonly, sql, get_settings().sql_max_rows, engine)

    return mcp


def _list_tables(engine: Engine) -> str:
    inspector = inspect(engine)
    lines = []
    for schema in inspector.get_schema_names():
        if schema in SYSTEM_SCHEMAS or schema.startswith("pg_"):
            continue
        for table in sorted(inspector.get_table_names(schema=schema)):
            comment = inspector.get_table_comment(table, schema=schema).get("text")
            lines.append(f"- {schema}.{table}" + (f": {comment}" if comment else ""))
    return "\n".join(lines) or "No tables visible."


def _describe_table(engine: Engine, table: str) -> str:
    schema, _, name = table.rpartition(".")
    schema = schema or "public"
    inspector = inspect(engine)
    if name not in inspector.get_table_names(schema=schema):
        return f"Error: no table {schema}.{name}. Call list_tables first."
    columns = inspector.get_columns(name, schema=schema)
    return f"{schema}.{name}:\n" + "\n".join(f"  {col['name']} {col['type']}" for col in columns)
