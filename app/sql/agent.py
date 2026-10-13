import asyncio
import logging
import re
from functools import lru_cache

from langchain.tools import tool
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError

from app.agent_factory import build_agent
from app.agents.base import AgentSpec, RouteInfo
from app.config import get_settings
from app.prompts import LANGUAGE_RULE, MEMORY_RULE, SQL_SYSTEM

log = logging.getLogger(__name__)

# Schemas the agent may read.
SCHEMAS = ("market",)
_READ_ONLY_START = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)

ROUTE_DESCRIPTION = (
    "answers from the database: stock market data for about 200 popular stocks "
    "(prices, returns, market cap, sectors, P/E, dividends, comparisons, rankings)."
)
HINTS = re.compile(
    r"\b(stocks?|tickers?|market caps?|dividends?|p/e|stock prices?|share prices?|"
    r"cổ phiếu|cổ tức|vốn hóa|chứng khoán|mã ck)\b",
    re.IGNORECASE,
)
TICKER_LIKE = re.compile(r"\b[A-Z]{2,5}(?:[.-][A-Z])?\b")
_tickers: frozenset[str] = frozenset()


@lru_cache
def get_readonly_engine() -> Engine:
    # The agent_readonly role is already read-only with a 10s timeout; these
    # connection options repeat that in case the URL points at another role.
    return create_engine(
        get_settings().readonly_sqlalchemy_url,
        pool_pre_ping=True,
        connect_args={"options": "-c default_transaction_read_only=on -c statement_timeout=10000"},
    )


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


def execute_readonly(sql: str, max_rows: int) -> str:
    """Run one SELECT and render the rows as a pipe table (capped at max_rows)."""
    sql = sql.strip().rstrip(";")
    if not _READ_ONLY_START.match(sql) or ";" in sql:
        return "Error: only a single SELECT / WITH ... SELECT statement is allowed."
    try:
        with get_readonly_engine().connect() as conn:
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


def stock_tickers() -> frozenset[str]:
    """Tickers in market.stocks, cached after the first successful read."""
    global _tickers
    if not _tickers:
        try:
            with get_readonly_engine().connect() as conn:
                _tickers = frozenset(conn.execute(text("SELECT ticker FROM market.stocks")).scalars())
        except SQLAlchemyError as exc:
            log.warning("Could not load tickers for routing: %s", exc)
    return _tickers


def mentions_ticker(message: str) -> bool:
    candidates = set(TICKER_LIKE.findall(message))
    return bool(candidates) and bool(candidates & stock_tickers())


async def route_info(user_id: str) -> RouteInfo:
    return RouteInfo(matches=mentions_ticker)


def build_sql_agent():
    system_prompt = SQL_SYSTEM.format(
        language_rule=LANGUAGE_RULE, memory_rule=MEMORY_RULE, schema=describe_schema()
    )
    return build_agent(name="sql_agent", system_prompt=system_prompt, tools=[run_sql])


SPEC = AgentSpec(
    route="sql",
    description=ROUTE_DESCRIPTION,
    labels={"en": "Database agent", "vi": "Trợ lý cơ sở dữ liệu"},
    build=build_sql_agent,
    message_names=("sql_agent",),
    hints=HINTS,
    route_info=route_info,
)
