from datetime import date

import pandas as pd

from app.features.market.loader import frame_to_rows, info_to_row
from app.features.market.tickers import TICKERS


def test_ticker_list_has_200_unique_symbols():
    assert len(TICKERS) == 200
    assert len(set(TICKERS)) == 200


def _frame(data: dict[str, list[list[float]]]) -> pd.DataFrame:
    """A frame shaped like yf.download(..., group_by="ticker", auto_adjust=False)."""
    fields = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
    index = pd.to_datetime(["2026-10-08", "2026-10-09"])
    columns = pd.MultiIndex.from_product([list(data), fields])
    values = [sum((data[t][i] for t in data), []) for i in range(len(index))]
    return pd.DataFrame(values, index=index, columns=columns)


def test_frame_to_rows_flattens_tickers_and_drops_missing_days():
    nan = float("nan")
    frame = _frame({
        "AAPL": [[1, 2, 0.5, 1.5, 1.4, 1000], [1.5, 2.5, 1, 2, 1.9, 2000]],
        "MSFT": [[nan] * 6, [10, 11, 9, 10.123456, 10, 500]],
    })
    rows = frame_to_rows(frame, ["AAPL", "MSFT", "GONE"])
    assert rows == [
        ("AAPL", date(2026, 10, 8), 1.0, 2.0, 0.5, 1.5, 1.4, 1000),
        ("AAPL", date(2026, 10, 9), 1.5, 2.5, 1.0, 2.0, 1.9, 2000),
        ("MSFT", date(2026, 10, 9), 10.0, 11.0, 9.0, 10.1235, 10.0, 500),
    ]


def test_frame_to_rows_handles_empty_download():
    assert frame_to_rows(pd.DataFrame(), ["AAPL"]) == []


def test_info_to_row_maps_fields_and_tolerates_gaps():
    row = info_to_row("AAPL", {
        "longName": "Apple Inc.", "sector": "Technology", "marketCap": 4.9e12,
        "dividendYield": 0.32, "trailingPE": "Infinity", "profitMargins": 0.276,
    })
    assert row["name"] == "Apple Inc."
    assert row["market_cap"] == 4_900_000_000_000
    assert row["dividend_yield_pct"] == 0.32
    assert row["trailing_pe"] is None
    assert row["industry"] is None
    assert info_to_row("XYZ", {})["name"] == "XYZ"
