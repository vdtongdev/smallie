---
name: market-data
description: Using the stock database (run_sql, 200 large US stocks with daily prices and company profiles) together with news or other sources.
---
1. The schema of the `market` tables is in your instructions. Use schema-qualified names (`market.stocks`, `market.daily_prices`).
2. Fetch exact numbers with `run_sql`: latest close, change over a period, market cap, P/E, dividend yield, sector peers. Combine what you need into one query where possible (e.g. one query for several tickers).
3. Always record the trade date the numbers are from. The data is end-of-day, not live.
4. For "why did it move" questions, pair the price change from the database with news from web search for the same dates.
5. Tag database findings as [market DB, <date>].
6. If a ticker isn't in `market.stocks`, say so and use web sources instead.
