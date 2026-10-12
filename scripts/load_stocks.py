"""Load or refresh the stock database (schema `market`) from Yahoo Finance.

Usage:
  uv run python -m scripts.load_stocks                 # first run: 5 years; later runs: only new days
  uv run python -m scripts.load_stocks --prices-only   # skip the 200 profile requests (fast daily update)
  uv run python -m scripts.load_stocks --full          # re-download all history (e.g. after a stock split)

The app already updates every weekday (MARKET_UPDATE_TIME); use this for the first load,
--full reloads, or when the background update is disabled. Safe to run while the app is
running; the SQL agent sees new rows immediately.
"""

import argparse
import logging
import sys

from app.market.loader import load_market
from app.market.scheduler import update_lock
from app.market.tickers import TICKERS


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--period", default="5y", help="history to download on a full load (yfinance period, default 5y)")
    parser.add_argument("--full", action="store_true", help="re-download the whole period instead of only recent days")
    parser.add_argument("--prices-only", action="store_true", help="don't refresh company profiles / key stats")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)  # failures are reported below
    with update_lock() as acquired:
        if not acquired:
            sys.exit("The app's background job is updating stock data right now; try again in a few minutes.")
        result = load_market(TICKERS, period=args.period, full=args.full, profiles=not args.prices_only)

    if result["profiles"] is not None:
        print(f"Profiles: {result['profiles']}/{len(TICKERS)}")
    print(f"Price rows upserted: {result['price_rows']} (since {result['since']})")
    if result["missing"]:
        print(f"No price data for: {', '.join(result['missing'])} (delisted or renamed? edit app/market/tickers.py)")


if __name__ == "__main__":
    main()
