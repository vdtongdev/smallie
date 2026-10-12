-- Stock market data for the SQL agent, filled by `uv run python -m scripts.load_stocks`.
-- Idempotent: the loader re-applies this file, so existing databases pick it up too.
CREATE SCHEMA IF NOT EXISTS market;

CREATE TABLE IF NOT EXISTS market.stocks (
    ticker              TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    sector              TEXT,
    industry            TEXT,
    country             TEXT,
    exchange            TEXT,
    currency            TEXT,
    market_cap          BIGINT,
    employees           INT,
    trailing_pe         NUMERIC,
    forward_pe          NUMERIC,
    eps                 NUMERIC,
    dividend_yield_pct  NUMERIC,
    beta                NUMERIC,
    week52_high         NUMERIC,
    week52_low          NUMERIC,
    revenue             BIGINT,
    profit_margin       NUMERIC,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE market.stocks IS
    'The 200 most popular (mostly US) stocks (cổ phiếu): company profile and key stats as of updated_at. '
    'market_cap and revenue are in the stock''s currency; dividend_yield_pct is a percentage (0.4 = 0.4%); '
    'profit_margin is a fraction (0.25 = 25%)';

CREATE TABLE IF NOT EXISTS market.daily_prices (
    ticker      TEXT NOT NULL REFERENCES market.stocks(ticker),
    trade_date  DATE NOT NULL,
    open        NUMERIC(14, 4),
    high        NUMERIC(14, 4),
    low         NUMERIC(14, 4),
    close       NUMERIC(14, 4),
    adj_close   NUMERIC(14, 4),
    volume      BIGINT,
    updated_at  TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (ticker, trade_date)
);
-- Databases created before updated_at existed: rows stay NULL until the next update reloads them.
ALTER TABLE market.daily_prices ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;
ALTER TABLE market.daily_prices ALTER COLUMN updated_at SET DEFAULT now();
COMMENT ON TABLE market.daily_prices IS
    'Daily OHLCV prices (giá cổ phiếu theo ngày), one row per stock per trading day, in the stock''s currency. '
    'adj_close is adjusted for splits and dividends: use it for returns over time. '
    'The latest trading day in the data is max(trade_date); updated_at is when the row was last loaded, not a trading time';
CREATE INDEX IF NOT EXISTS daily_prices_trade_date_idx ON market.daily_prices (trade_date);

GRANT USAGE ON SCHEMA market TO agent_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA market TO agent_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA market GRANT SELECT ON TABLES TO agent_readonly;
ALTER ROLE agent_readonly SET search_path = market;
