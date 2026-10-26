import logging
import re

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.agents.base import AgentSpec, RouteInfo, is_error
from app.agents.factory import build_agent
from app.agents.sql.prompts import REVIEW, ROUTE_DESCRIPTION, SQL_RULES, SQL_SYSTEM, TOOL_DESCRIPTION
from app.agents.sql.tools import describe_schema, run_sql
from app.core.db import get_readonly_engine
from app.core.prompts import LANGUAGE_RULE, MEMORY_RULE

log = logging.getLogger(__name__)

HINTS = re.compile(
    r"\b(stocks?|tickers?|market caps?|dividends?|p/e|stock prices?|share prices?|"
    r"cổ phiếu|cổ tức|vốn hóa|chứng khoán|mã ck)\b",
    re.IGNORECASE,
)
NAMES = re.compile(r"\b(sql|database|cơ sở dữ liệu)\b", re.IGNORECASE)
TICKER_LIKE = re.compile(r"\b[A-Z]{2,5}(?:[.-][A-Z])?\b")
_tickers: frozenset[str] = frozenset()


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


def no_rows(text: str) -> bool:
    """An error, or a result table without rows (`render_rows` footer)."""
    return is_error(text) or "\n(0 rows" in text


def build_sql_agent():
    system_prompt = SQL_SYSTEM.format(
        sql_rules=SQL_RULES, language_rule=LANGUAGE_RULE, memory_rule=MEMORY_RULE, schema=describe_schema()
    )
    return build_agent(name="sql_agent", system_prompt=system_prompt, tools=[run_sql])


SPEC = AgentSpec(
    route="sql",
    description=ROUTE_DESCRIPTION,
    labels={"en": "Database agent", "vi": "Trợ lý cơ sở dữ liệu"},
    build=build_sql_agent,
    message_names=("sql_agent",),
    hints=HINTS,
    names=NAMES,
    route_info=route_info,
    tool_description=TOOL_DESCRIPTION,
    review=REVIEW,
    sources=frozenset({"market_db"}),
    no_result=no_rows,
)
