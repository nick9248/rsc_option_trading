-- Migration 034: recorder for the liquidation heatmap research (see documentation/liquidation_heatmap_research.md).
--
--   liquidation_events              real liquidations of a perpetual (OKX BTC-USDT-SWAP): when, which side was
--                                   liquidated, at what price, how much. Written by
--                                   coding/service/liquidation_heatmap/ inside the collector (unified_scheduler),
--                                   only with LIQUIDATION_RECORDER_ENABLED=1.
--   liquidation_heatmap_snapshots   the model's estimated levels, computed every hour on the VPS and stored at the
--                                   time they were computed (point in time), so a real liquidation can later be
--                                   checked against the levels that were published BEFORE it, without hindsight.
--
-- Binance blocks the USA VPS, so the real events come from OKX (cross-venue: liquidations happen at the same prices
-- on every venue) and the candles for the model come through the read-only Binance relay (BINANCE_FUTURES_BASE_URL).
--
-- liquidated_side   'long' = a long position was liquidated (a forced sell), 'short' = a short (a forced buy)
-- price             the bankruptcy price OKX reports for the order
-- size_btc          raw_size (contracts) times the contract value (0.01 BTC for BTC-USDT-SWAP)
-- levels            [[price, long_usd, short_usd], ...] within 10 % of reference_price, USD at the level's price
-- collected_at / computed_at   when the row was stored (the incremental VPS sync watermarks)
--
-- Idempotent (IF NOT EXISTS). No down-migration (house convention has none).

CREATE TABLE IF NOT EXISTS liquidation_events (
    id               serial PRIMARY KEY,
    venue            varchar(20) NOT NULL,
    instrument       varchar(40) NOT NULL,
    event_time       timestamptz NOT NULL,
    liquidated_side  varchar(5) NOT NULL CHECK (liquidated_side IN ('long', 'short')),
    price            numeric NOT NULL CHECK (price > 0),
    size_btc         numeric NOT NULL CHECK (size_btc > 0),
    raw_size         numeric NOT NULL CHECK (raw_size > 0),
    collected_at     timestamptz NOT NULL,
    CONSTRAINT unique_liquidation_event
        UNIQUE (venue, instrument, event_time, liquidated_side, price, raw_size)
);
CREATE INDEX IF NOT EXISTS idx_liquidation_events_time ON liquidation_events (venue, instrument, event_time);
CREATE INDEX IF NOT EXISTS idx_liquidation_events_collected ON liquidation_events (collected_at);

CREATE TABLE IF NOT EXISTS liquidation_heatmap_snapshots (
    id               serial PRIMARY KEY,
    symbol           varchar(20) NOT NULL,
    as_of_utc        timestamptz NOT NULL,
    computed_at      timestamptz NOT NULL,
    reference_price  numeric NOT NULL CHECK (reference_price > 0),
    bin_size_usd     numeric NOT NULL CHECK (bin_size_usd > 0),
    settings         jsonb NOT NULL,
    levels           jsonb NOT NULL,
    CONSTRAINT unique_liquidation_heatmap_snapshot UNIQUE (symbol, as_of_utc)
);
CREATE INDEX IF NOT EXISTS idx_liquidation_heatmap_snapshots_computed
    ON liquidation_heatmap_snapshots (computed_at);
