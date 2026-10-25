import asyncio
import re
import uuid
from datetime import date, datetime
from decimal import Decimal

from langchain.tools import tool
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.db import get_readonly_engine
from app.core.memory import TOOL_SOURCE

# Schemas the agent may read.
SCHEMAS = ("market",)
TABLE_MAX_ROWS = 2000  # rows kept in a query's table artifact (the model sees SQL_MAX_ROWS)
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


def fetch_readonly(sql: str, max_rows: int, engine: Engine | None = None) -> tuple[list[str], list, bool] | str:
    """Run one SELECT: (columns, up to max_rows rows, truncated), or an "Error: ..." string.

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
    return columns, rows[:max_rows], len(rows) > max_rows


def render_rows(columns: list[str], rows: list, max_rows: int, truncated: bool = False) -> str:
    """Rows as a pipe table for the model, capped at max_rows."""
    truncated = truncated or len(rows) > max_rows
    rows = rows[:max_rows]
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
        *("| " + " | ".join("" if v is None else str(v) for v in row) + " |" for row in rows),
    ]
    footer = f"\n({len(rows)} rows" + (f", truncated to the first {max_rows})" if truncated else ")")
    return "\n".join(lines) + footer


def execute_readonly(sql: str, max_rows: int, engine: Engine | None = None) -> str:
    """Run one SELECT and render the rows as a pipe table (capped at max_rows)."""
    result = fetch_readonly(sql, max_rows, engine)
    if isinstance(result, str):
        return result
    columns, rows, truncated = result
    return render_rows(columns, rows, max_rows, truncated)


def json_value(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def make_table(query: str, columns: list[str], rows: list, truncated: bool) -> dict:
    """A query result other tools can use without the model copying it (e.g. to draw a chart);
    it travels as a ToolMessage artifact, which the model never sees."""
    return {
        "id": f"t{uuid.uuid4().hex[:6]}",
        "query": query.strip().rstrip(";"),
        "columns": columns,
        "rows": [[json_value(v) for v in row] for row in rows],
        "truncated": truncated,
    }


def query_readonly(sql: str, max_rows: int = TABLE_MAX_ROWS, engine: Engine | None = None) -> dict | str:
    """Run one SELECT and return it as a table (see `make_table`), or an "Error: ..." string."""
    result = fetch_readonly(sql, max_rows, engine)
    return result if isinstance(result, str) else make_table(sql, *result)


@tool(response_format="content_and_artifact")
async def run_sql(query: str) -> tuple[str, dict]:
    """Execute a read-only PostgreSQL SELECT query and return the rows as a table.

    Args:
        query: A single SELECT (or WITH ... SELECT) statement.
    """
    result = await asyncio.to_thread(fetch_readonly, query, TABLE_MAX_ROWS)
    if isinstance(result, str):
        return result, {}
    columns, rows, truncated = result
    table = make_table(query, columns, rows, truncated)
    # The model sees the first SQL_MAX_ROWS rows; the table artifact keeps them all (for charts).
    content = render_rows(columns, rows, get_settings().sql_max_rows, truncated)
    return f"{content[:-1]}; table {table['id']})", {"tables": [table]}


run_sql.metadata = {TOOL_SOURCE: "market_db"}
