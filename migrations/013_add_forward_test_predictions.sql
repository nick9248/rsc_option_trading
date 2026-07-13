-- Migration 013: Forward-test predictions table for Phase 3 harness
-- Stores one prediction per (currency, snapshot_hour).
-- Predictions are made using Phase 2 validated signals (OI moneyness + max pain at 1h).
-- Resolutions are filled in ~1 hour later by the harness resolver.

CREATE TABLE IF NOT EXISTS forward_test_predictions (
    id SERIAL PRIMARY KEY,
    currency VARCHAR(10) NOT NULL,
    snapshot_hour TIMESTAMPTZ NOT NULL,

    -- Raw metric values at prediction time (Phase 2 survivors)
    itm_put_oi_pct FLOAT,
    otm_put_oi_pct FLOAT,
    itm_call_oi_pct FLOAT,
    otm_call_oi_pct FLOAT,
    max_pain_distance_pct FLOAT,
    pc_far_otm_ratio FLOAT,           -- ETH only; NULL for BTC

    spot_price_at_prediction FLOAT NOT NULL,

    -- Composite signal output
    signal_direction VARCHAR(10) NOT NULL,   -- 'bullish', 'bearish', 'neutral'
    signal_score FLOAT NOT NULL,             -- signed composite weighted z-score (positive = bullish)
    signal_confidence FLOAT NOT NULL,        -- abs(signal_score) capped to [0, 1]

    -- Per-metric z-scores stored for post-hoc analysis
    z_itm_put_oi_pct FLOAT,
    z_otm_put_oi_pct FLOAT,
    z_itm_call_oi_pct FLOAT,
    z_otm_call_oi_pct FLOAT,
    z_max_pain_distance_pct FLOAT,
    z_pc_far_otm_ratio FLOAT,

    -- Resolution (filled ~1h after snapshot_hour)
    resolved_at TIMESTAMPTZ,
    spot_price_at_resolution FLOAT,
    actual_1h_return_pct FLOAT,       -- (resolution - prediction) / prediction * 100
    signal_correct BOOLEAN,           -- direction matched actual return sign

    created_at TIMESTAMPTZ DEFAULT NOW(),

    UNIQUE (currency, snapshot_hour)
);

CREATE INDEX IF NOT EXISTS idx_ftp_currency_hour
    ON forward_test_predictions(currency, snapshot_hour DESC);

CREATE INDEX IF NOT EXISTS idx_ftp_unresolved
    ON forward_test_predictions(resolved_at)
    WHERE resolved_at IS NULL;

COMMENT ON TABLE forward_test_predictions IS
    'Phase 3 forward-test predictions. One row per currency per hour. '
    'Signals derived from Phase 2 validated metrics. Resolved when actual 1h return is available.';
