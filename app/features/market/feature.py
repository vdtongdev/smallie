import asyncio
from contextlib import AsyncExitStack

from app.core.config import get_settings
from app.features.base import Feature
from app.features.market import routes
from app.features.market.scheduler import parse_time, run_daily


def start(stack: AsyncExitStack) -> None:
    """Daily stock update (app/features/market/scheduler.py), when MARKET_UPDATE_TIME is set."""
    at = get_settings().market_update_time
    if at:
        job = asyncio.create_task(run_daily(parse_time(at)), name="market-update")
        stack.callback(job.cancel)


FEATURE = Feature(name="market", routers=(routes.router,), start=start)
