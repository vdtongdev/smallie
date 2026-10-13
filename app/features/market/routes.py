import asyncio

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.auth import get_current_user
from app.db import get_readonly_engine

router = APIRouter(tags=["market"])


def _status() -> dict:
    try:
        with get_readonly_engine().connect() as conn:
            row = conn.execute(text(
                "SELECT (SELECT count(*) FROM market.stocks) AS stocks, max(trade_date) AS last_trade_date, "
                # Last successful load of prices or profiles (greatest() skips NULLs).
                "greatest(max(updated_at), (SELECT max(updated_at) FROM market.stocks)) AS updated_at "
                "FROM market.daily_prices"
            )).mappings().one()
    except SQLAlchemyError:
        return {"stocks": 0, "last_trade_date": None, "updated_at": None}  # not loaded yet
    return dict(row)


@router.get("/market/status")
async def market_status(_: str = Depends(get_current_user)):
    """How much stock data is loaded and how fresh it is (shown in the Readme)."""
    return await asyncio.to_thread(_status)
