"""Daily end-of-day stock update, run as a background task inside the app.

Every weekday at MARKET_UPDATE_TIME (New York time, after the US close) it fetches the
latest prices, and refreshes company profiles once they are a week old. On startup it
catches up if the last scheduled run was missed (app or PC was off).

A Postgres advisory lock makes sure only one process updates at a time, so several
uvicorn workers (or a manual `scripts.load_stocks` run) don't do the work twice.
"""

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import psycopg

from app.config import get_settings
from app.market.loader import load_market
from app.market.tickers import TICKERS

log = logging.getLogger(__name__)

MARKET_TZ = ZoneInfo("America/New_York")
LOCK_KEY = 0x736D616C6C6965  # arbitrary app-wide id for pg_try_advisory_lock
PROFILE_MAX_AGE = timedelta(days=7)
RETRY_DELAY = timedelta(minutes=15)
ATTEMPTS = 3


def parse_time(value: str) -> time:
    """"17:30" -> time(17, 30); raises ValueError on bad input."""
    hour, minute = value.strip().split(":")
    return time(int(hour), int(minute))


def latest_session(now: datetime, at: time) -> date:
    """The most recent weekday whose scheduled update time has passed."""
    now = now.astimezone(MARKET_TZ)
    day = now.date() if now.time() >= at else now.date() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def next_run(now: datetime, at: time) -> datetime:
    """The next weekday at `at` (New York time), strictly after `now`."""
    now = now.astimezone(MARKET_TZ)
    day = now.date()
    while True:
        candidate = datetime.combine(day, at, MARKET_TZ)
        if candidate > now and day.weekday() < 5:
            return candidate
        day += timedelta(days=1)


def market_state() -> tuple[date | None, datetime | None]:
    """(latest trade_date, oldest profile refresh), or (None, None) if not loaded yet."""
    with psycopg.connect(get_settings().database_url, autocommit=True) as conn:
        if not conn.execute("SELECT to_regclass('market.daily_prices')").fetchone()[0]:
            return None, None
        return conn.execute(
            "SELECT (SELECT max(trade_date) FROM market.daily_prices), (SELECT min(updated_at) FROM market.stocks)"
        ).fetchone()


@contextmanager
def update_lock() -> Iterator[bool]:
    """Yields True if this process may update the market schema now (held until exit)."""
    with psycopg.connect(get_settings().database_url, autocommit=True) as conn:
        acquired = conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,)).fetchone()[0]
        try:
            yield acquired
        finally:
            if acquired:
                conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))


def run_update() -> dict | None:
    """One update under the advisory lock. Returns None if another process holds it."""
    with update_lock() as acquired:
        if not acquired:
            log.info("Stock update skipped: another process is already updating.")
            return None
        _, oldest_profile = market_state()
        stale = oldest_profile is None or datetime.now(MARKET_TZ) - oldest_profile > PROFILE_MAX_AGE
        return load_market(TICKERS, profiles=stale)


async def _update_with_retries() -> None:
    for attempt in range(1, ATTEMPTS + 1):
        try:
            result = await asyncio.to_thread(run_update)
        except Exception:  # noqa: BLE001 - Yahoo/network/DB hiccups must not kill the job
            log.exception("Stock update failed (attempt %d/%d)", attempt, ATTEMPTS)
            if attempt < ATTEMPTS:
                await asyncio.sleep(RETRY_DELAY.total_seconds())
            continue
        if result:
            log.info("Stock update done: %d price rows since %s, profiles: %s%s",
                     result["price_rows"], result["since"], result["profiles"],
                     f", no data for {', '.join(result['missing'])}" if result["missing"] else "")
        return


async def run_daily(at: time) -> None:
    """Background task: catch up once if needed, then update every weekday at `at`."""
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)  # failures are summarized above
    try:
        last_date, _ = await asyncio.to_thread(market_state)
        if last_date is None or last_date < latest_session(datetime.now(MARKET_TZ), at):
            log.info("Stock data is behind (latest %s); updating now.", last_date)
            await _update_with_retries()
            if last_date is None:
                log.warning("First stock load finished: restart the app so the SQL agent sees the market schema.")
    except Exception:  # noqa: BLE001 - e.g. Postgres down at startup; the schedule still runs
        log.exception("Startup stock catch-up failed")

    while True:
        when = next_run(datetime.now(MARKET_TZ), at)
        log.info("Next stock update at %s", when.isoformat(timespec="minutes"))
        await asyncio.sleep((when - datetime.now(MARKET_TZ)).total_seconds())
        await _update_with_retries()
