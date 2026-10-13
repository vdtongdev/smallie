from datetime import date, datetime, time

import pytest

from app.features.market import scheduler
from app.features.market.scheduler import MARKET_TZ, latest_session, next_run, parse_time, update_lock
from tests.conftest import requires_db

AT = time(17, 30)


def ny(*args) -> datetime:
    return datetime(*args, tzinfo=MARKET_TZ)


def test_parse_time():
    assert parse_time("17:30") == AT
    assert parse_time(" 6:05 ") == time(6, 5)
    with pytest.raises(ValueError):
        parse_time("5pm")


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (ny(2026, 10, 14, 18, 0), ny(2026, 10, 15, 17, 30)),  # Wed after the run -> Thu
        (ny(2026, 10, 14, 9, 0), ny(2026, 10, 14, 17, 30)),   # Wed morning -> same day
        (ny(2026, 10, 14, 17, 30), ny(2026, 10, 15, 17, 30)),  # exactly at run time -> next day
        (ny(2026, 10, 16, 18, 0), ny(2026, 10, 19, 17, 30)),  # Fri evening -> Mon
        (ny(2026, 10, 17, 12, 0), ny(2026, 10, 19, 17, 30)),  # Saturday -> Mon
    ],
)
def test_next_run_skips_weekends(now, expected):
    assert next_run(now, AT) == expected


def test_next_run_accepts_other_timezones():
    utc_now = datetime.fromisoformat("2026-10-14T20:00:00+00:00")  # 16:00 in New York
    assert next_run(utc_now, AT) == ny(2026, 10, 14, 17, 30)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (ny(2026, 10, 14, 18, 0), date(2026, 10, 14)),  # after today's run
        (ny(2026, 10, 14, 9, 0), date(2026, 10, 13)),   # before it: yesterday
        (ny(2026, 10, 19, 9, 0), date(2026, 10, 16)),   # Monday morning: Friday
        (ny(2026, 10, 18, 12, 0), date(2026, 10, 16)),  # Sunday: Friday
    ],
)
def test_latest_session(now, expected):
    assert latest_session(now, AT) == expected


@requires_db
def test_update_lock_is_exclusive():
    with update_lock() as first:
        assert first
        with update_lock() as second:
            assert not second
    with update_lock() as again:
        assert again


@requires_db
def test_run_update_skips_while_locked(monkeypatch):
    monkeypatch.setattr(scheduler, "load_market", lambda *a, **k: pytest.fail("should not load"))
    with update_lock():
        assert scheduler.run_update() is None
