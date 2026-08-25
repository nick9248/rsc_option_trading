-- Migration 021: New flow_delta_hourly table (institutional_metrics_spec.md
-- Migration M5 / section 6, delta-adjusted premium-weighted net flow, and
-- section 1's flow-imbalance history metric).
--
-- historical_trades does not store delta, and buy_sell_flow_metrics is
-- GUI-cadence only (2 distinct hours over the last 14 days, per
-- infra_spec.md section 2) -- neither is a usable series source for
-- HIRO-style hourly aggregates or the flow-imbalance percentile in
-- section 1. This is a fresh table, sourced by recomputing signed BS
-- delta per trade from historical_trades' own iv/index_price/strike/
-- expiration columns (99.82% recomputable, per spec section 6(a)).
--
-- net_contracts/gross_contracts double as section 1's "flow imbalance"
-- metric (signed/gross taker contract counts), so a single hourly table
-- serves both section 6 and section 1 -- no separate table needed.
--
-- Schema-only in this migration; population (DeltaFlowCalculator, the
-- daemon writer in ProspectiveCollector.collect_hour, and the one-off
-- backfill from 2026-04-25) is Wave C's C6 task, not this one. The
-- existing buy_sell_flow_metrics table is untouched (left for the
-- flow-charts GUI window, per infra_spec.md section 2).

CREATE TABLE IF NOT EXISTS flow_delta_hourly (
    id                SERIAL PRIMARY KEY,
    snapshot_hour     TIMESTAMP    NOT NULL,       -- UTC hour bucket
    currency          VARCHAR(10)  NOT NULL,
    expiration        VARCHAR(20)  NOT NULL,       -- 'ALL' for the currency-level rollup
    hiro_usd          NUMERIC(24,2),               -- signed delta-notional
    premium_usd       NUMERIC(24,2),               -- signed premium
    gross_delta_usd   NUMERIC(24,2),               -- unsigned |delta| notional
    net_contracts     NUMERIC(20,4),               -- signed taker contracts (flow imbalance for section 1)
    gross_contracts   NUMERIC(20,4),
    trade_count       INTEGER,
    buy_count         INTEGER,
    sell_count        INTEGER,
    skipped_count     INTEGER,                     -- trades that failed BS enrichment
    created_at        TIMESTAMP DEFAULT NOW(),
    UNIQUE (snapshot_hour, currency, expiration)
);

CREATE INDEX IF NOT EXISTS idx_flow_delta_ccy_time
    ON flow_delta_hourly (currency, snapshot_hour DESC);

COMMENT ON TABLE flow_delta_hourly IS
    'Hourly delta-adjusted, premium-weighted taker flow (HIRO analog), one row per (snapshot_hour, currency, expiration), plus expiration=ALL for the currency-level rollup. Recomputed per trade from historical_trades (BS delta from iv/index_price/strike/expiration -- historical_trades itself has no delta column). Also serves institutional_metrics_spec.md section 1''s flow-imbalance history metric via net_contracts/gross_contracts. Schema only as of this migration; no daemon writer wired yet (Wave C C6 scope).';
COMMENT ON COLUMN flow_delta_hourly.hiro_usd IS
    'Signed delta-notional: sum(taker_side * delta * amount * index_price). Positive = net bullish hedging pressure on dealers. NOT the same sign convention as gross_delta_usd (which uses |delta|).';
COMMENT ON COLUMN flow_delta_hourly.gross_delta_usd IS
    'Unsigned hedging-impact magnitude: sum(|delta| * amount * index_price). This is the brief''s originally-specified formula; emitted alongside the signed hiro_usd so nothing is lost (institutional_metrics_spec.md section 11 item 1).';
COMMENT ON COLUMN flow_delta_hourly.net_contracts IS
    'Signed taker contracts (sum of +amount for buy, -amount for sell) -- the flow-imbalance series institutional_metrics_spec.md section 1 needs.';
COMMENT ON COLUMN flow_delta_hourly.skipped_count IS
    'Trades skipped because BS enrichment failed (missing/invalid iv, etc.) -- not defaulted to zero, counted separately so the skip rate is visible.';
