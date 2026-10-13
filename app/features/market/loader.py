"""Fill the `market` schema from Yahoo Finance via yfinance.

Writes use the owner connection (DATABASE_URL); the SQL agent only ever reads the
result through the agent_readonly role.
"""

import logging
import math
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import psycopg
import yfinance as yf

from app.core.config import get_settings

log = logging.getLogger(__name__)

SCHEMA_FILE = Path(__file__).resolve().parents[3] / "db" / "init" / "04_market_schema.sql"
DOWNLOAD_BATCH = 50
# Re-fetch a few days on incremental updates: Yahoo revises the most recent bars.
OVERLAP_DAYS = 7

PRICE_COLUMNS = ("ticker", "trade_date", "open", "high", "low", "close", "adj_close", "volume")
STOCK_COLUMNS = (
    "ticker", "name", "sector", "industry", "country", "exchange", "currency", "market_cap",
    "employees", "trailing_pe", "forward_pe", "eps", "dividend_yield_pct", "beta",
    "week52_high", "week52_low", "revenue", "profit_margin",
)


def _num(value) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(value) or math.isinf(value) else round(value, 4)


def _int(value) -> int | None:
    value = _num(value)
    return None if value is None else int(value)


def apply_schema(conn: psycopg.Connection) -> None:
    conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))


# ---------- prices ----------

def frame_to_rows(frame: pd.DataFrame, tickers: list[str]) -> list[tuple]:
    """Rows for market.daily_prices from a yf.download(group_by="ticker") frame."""
    if frame.empty:
        return []
    present = set(frame.columns.get_level_values(0))
    rows = []
    for ticker in tickers:
        if ticker not in present:
            continue
        part = frame[ticker].dropna(subset=["Close"])
        for day, o, h, l, c, adj, vol in zip(
            part.index, part["Open"], part["High"], part["Low"], part["Close"], part["Adj Close"], part["Volume"]
        ):
            rows.append((ticker, day.date(), _num(o), _num(h), _num(l), _num(c), _num(adj), _int(vol)))
    return rows


def fetch_prices(tickers: list[str], *, period: str | None = None, start: date | None = None) -> list[tuple]:
    rows = []
    for i in range(0, len(tickers), DOWNLOAD_BATCH):
        batch = tickers[i : i + DOWNLOAD_BATCH]
        frame = yf.download(
            batch, period=None if start else period, start=start, interval="1d",
            group_by="ticker", auto_adjust=False, threads=True, progress=False,
        )
        rows += frame_to_rows(frame, batch)
        log.info("prices: %d/%d tickers downloaded", min(i + DOWNLOAD_BATCH, len(tickers)), len(tickers))
    return rows


def upsert_prices(conn: psycopg.Connection, rows: list[tuple]) -> None:
    columns = ", ".join(PRICE_COLUMNS)
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE price_load (LIKE market.daily_prices) ON COMMIT DROP")
        with cur.copy(f"COPY price_load ({columns}) FROM STDIN") as copy:
            for row in rows:
                copy.write_row(row)
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in PRICE_COLUMNS[2:])
        cur.execute(
            f"INSERT INTO market.daily_prices ({columns}) SELECT {columns} FROM price_load "
            f"ON CONFLICT (ticker, trade_date) DO UPDATE SET {updates}, updated_at = now()"
        )


# ---------- company profiles ----------

def info_to_row(ticker: str, info: dict) -> dict:
    """Row for market.stocks from yf.Ticker(...).info."""
    return {
        "ticker": ticker,
        "name": info.get("longName") or info.get("shortName") or ticker,
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "country": info.get("country"),
        "exchange": info.get("exchange"),
        "currency": info.get("currency"),
        "market_cap": _int(info.get("marketCap")),
        "employees": _int(info.get("fullTimeEmployees")),
        "trailing_pe": _num(info.get("trailingPE")),
        "forward_pe": _num(info.get("forwardPE")),
        "eps": _num(info.get("trailingEps")),
        "dividend_yield_pct": _num(info.get("dividendYield")),  # yfinance already gives a percentage
        "beta": _num(info.get("beta")),
        "week52_high": _num(info.get("fiftyTwoWeekHigh")),
        "week52_low": _num(info.get("fiftyTwoWeekLow")),
        "revenue": _int(info.get("totalRevenue")),
        "profit_margin": _num(info.get("profitMargins")),
    }


def fetch_profile(ticker: str, attempts: int = 3) -> dict | None:
    for attempt in range(attempts):
        try:
            info = yf.Ticker(ticker).info
            if info and (info.get("longName") or info.get("shortName")):
                return info_to_row(ticker, info)
            return None
        except Exception as exc:  # noqa: BLE001 - Yahoo errors vary (rate limits, HTTP, parsing)
            log.warning("profile %s failed (attempt %d): %s", ticker, attempt + 1, exc)
            time.sleep(2 ** (attempt + 1))
    return None


def upsert_stocks(conn: psycopg.Connection, rows: list[dict]) -> None:
    columns = ", ".join(STOCK_COLUMNS)
    values = ", ".join(f"%({c})s" for c in STOCK_COLUMNS)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in STOCK_COLUMNS[1:])
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            f"INSERT INTO market.stocks ({columns}) VALUES ({values}) "
            f"ON CONFLICT (ticker) DO UPDATE SET {updates}, updated_at = now()",
            rows,
        )


def ensure_stock_rows(conn: psycopg.Connection, tickers: list[str]) -> None:
    """Placeholder rows so prices can reference tickers whose profile isn't loaded (yet)."""
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO market.stocks (ticker, name) VALUES (%s, %s) ON CONFLICT (ticker) DO NOTHING",
            [(t, t) for t in tickers],
        )


# ---------- entry point ----------

def load_market(
    tickers: list[str], *, period: str = "5y", full: bool = False, profiles: bool = True
) -> dict:
    """Create/refresh the market schema. Incremental (only recent days) unless `full` or empty."""
    with psycopg.connect(get_settings().database_url, autocommit=True) as conn:
        apply_schema(conn)
        ensure_stock_rows(conn, tickers)

        if profiles:
            rows = []
            for i, ticker in enumerate(tickers, 1):
                row = fetch_profile(ticker)
                if row:
                    rows.append(row)
                if i % 25 == 0:
                    log.info("profiles: %d/%d", i, len(tickers))
            upsert_stocks(conn, rows)
        else:
            rows = None

        last = conn.execute("SELECT max(trade_date) FROM market.daily_prices").fetchone()[0]
        start = None if full or last is None else last - timedelta(days=OVERLAP_DAYS)
        prices = fetch_prices(tickers, period=period, start=start)
        upsert_prices(conn, prices)

    loaded = {r[0] for r in prices}
    return {
        "profiles": None if rows is None else len(rows),
        "price_rows": len(prices),
        "since": start.isoformat() if start else period,
        "missing": [t for t in tickers if t not in loaded],
    }
