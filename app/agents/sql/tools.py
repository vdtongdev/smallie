import asyncio
import re

from langchain.tools import tool
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.db import get_readonly_engine

# Schemas the agent may read.
SCHEMAS = ("market",)
_READ_ONLY_START = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)


def describe_schema(sample_rows: int = 2) -> str:
    """Schema for the prompt: columns, keys, table comments and a few sample rows per table."""
    engine = get_readonly_engine()
    inspector = inspect(engine)
    blocks = []
    with engine.connect() as conn:
        for schema in SCHEMAS:
            for table in sorted(inspector.get_table_names(schema=schema)):
                blocks.append(_describe_table(conn, inspector, schema, table, sample_rows))
    return "\n\n".join(blocks)


def _describe_table(conn, inspector, schema: str, table: str, sample_rows: int) -> str:
    pk = set(inspector.get_pk_constraint(table, schema=schema).get("constrained_columns", []))
    fks = {
        fk["constrained_columns"][0]: f"{fk['referred_schema'] or schema}.{fk['referred_table']}.{fk['referred_columns'][0]}"
        for fk in inspector.get_foreign_keys(table, schema=schema)
    }
    lines = []
    for col in inspector.get_columns(table, schema=schema):
        line = f"  {col['name']} {col['type']}"
        if col["name"] in pk:
            line += " PRIMARY KEY"
        if col["name"] in fks:
            line += f" REFERENCES {fks[col['name']]}"
        lines.append(line)
    comment = inspector.get_table_comment(table, schema=schema).get("text")
    block = (f"-- {comment}\n" if comment else "") + f"CREATE TABLE {schema}.{table} (\n" + ",\n".join(lines) + "\n);"
    rows = conn.execute(text(f'SELECT * FROM "{schema}"."{table}" LIMIT {int(sample_rows)}')).mappings().all()
    if rows:
        block += "\n-- sample rows: " + "; ".join(str(dict(r)) for r in rows)
    return block


def execute_readonly(sql: str, max_rows: int, engine: Engine | None = None) -> str:
    """Run one SELECT and render the rows as a pipe table (capped at max_rows).

    `engine` defaults to the SQL agent's read-only engine; other read-only engines
    (e.g. the research agent's external database) share the same checks.
    """
    sql = sql.strip().rstrip(";")
    if not _READ_ONLY_START.match(sql) or ";" in sql:
        return "Error: only a single SELECT / WITH ... SELECT statement is allowed."
    try:
        with (engine or get_readonly_engine()).connect() as conn:
            result = conn.execute(text(sql))
            columns = list(result.keys())
            rows = result.fetchmany(max_rows + 1)
    except SQLAlchemyError as exc:
        return f"Error: {str(getattr(exc, 'orig', exc) or exc).strip()}"

    truncated = len(rows) > max_rows
    rows = rows[:max_rows]
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
        *("| " + " | ".join("" if v is None else str(v) for v in row) + " |" for row in rows),
    ]
    footer = f"\n({len(rows)} rows" + (f", truncated to the first {max_rows})" if truncated else ")")
    return "\n".join(lines) + footer


@tool
async def run_sql(query: str) -> str:
    """Execute a read-only PostgreSQL SELECT query and return the rows as a table.

    Args:
        query: A single SELECT (or WITH ... SELECT) statement.
    """
    return await asyncio.to_thread(execute_readonly, query, get_settings().sql_max_rows)
