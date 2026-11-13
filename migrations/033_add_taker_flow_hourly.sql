-- Migration 033: hourly taker buy/sell volume of Binance USD-M perpetual futures.
--
--   taker_flow_hourly   one row per symbol and COMPLETE UTC hour: how much of the hour's
--                       traded volume was started by buyers (taker buy) and by sellers
--                       (taker sell), in USD. Written by
--                       coding/service/futures_flow/ inside the collector
--                       (unified_scheduler), only with TAKER_FLOW_ENABLED=1.
--
-- Source: Binance futures klines (public, no key). The VPS is in the USA, which Binance
-- blocks, so the collector reads them through the read-only relay on the Spanish server
-- (BINANCE_FUTURES_BASE_URL, see documentation/futures_taker_flow.md).
--
-- symbol              'BTCUSDT' or 'ETHUSDT' (USD-M perpetual)
-- hour_utc            start of the UTC hour the candle covers
-- taker_buy_usd       quote volume bought by takers (USDT, treated as USD)
-- taker_sell_usd      total quote volume minus taker_buy_usd
-- trade_count         number of trades in the hour
-- collected_at        when this row was stored (the incremental VPS sync watermark)
-- A missing hour is a missing row, never a row of zeros. Only closed candles are stored.
--
-- Idempotent (IF NOT EXISTS). No down-migration (house convention has none).

CREATE TABLE IF NOT EXISTS taker_flow_hourly (
    id              serial PRIMARY KEY,
    symbol          varchar(20) NOT NULL,
    hour_utc        timestamptz NOT NULL,
    taker_buy_usd   numeric NOT NULL CHECK (taker_buy_usd >= 0),
    taker_sell_usd  numeric NOT NULL CHECK (taker_sell_usd >= 0),
    trade_count     integer,
    collected_at    timestamptz NOT NULL,
    CONSTRAINT unique_taker_flow_hour UNIQUE (symbol, hour_utc)
);
CREATE INDEX IF NOT EXISTS idx_taker_flow_hourly_collected
    ON taker_flow_hourly (collected_at);
