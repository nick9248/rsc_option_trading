-- Migration 012: Add on-chain volatility snapshots table
-- Companion to onchain_analysis_snapshots (same grain: snapshot_hour, currency, expiration).
-- Stores volatility-surface, VRP, IV-percentile, and expected-move metrics that are
-- computed live for the GUI report but were never persisted historically.
-- Populated retroactively by scripts/backfill_volatility_reconstruction.py by
-- re-running the existing calculator classes against hourly_snapshots/historical_trades.

CREATE TABLE IF NOT EXISTS onchain_volatility_snapshots (
    id SERIAL PRIMARY KEY,
    snapshot_hour TIMESTAMP NOT NULL,
    currency VARCHAR(10) NOT NULL,
    expiration VARCHAR(20) NOT NULL,

    -- Volatility surface (VolatilitySurfaceCalculator)
    atm_iv DECIMAL(8,4),
    skew_25d DECIMAL(8,4),               -- 25d Put IV - 25d Call IV
    put_25d_iv DECIMAL(8,4),
    call_25d_iv DECIMAL(8,4),
    net_vanna DECIMAL(20,8),             -- OI-weighted: sum(gamma * vega / spot * OI)
    net_charm DECIMAL(20,8),             -- OI-weighted: sum(-gamma * theta * OI)

    -- IV comparison (VWAP vs Mark)
    vwap_iv DECIMAL(8,4),                -- volume-weighted IV from trades in window
    mark_iv_avg DECIMAL(8,4),            -- average mark IV across instruments

    -- Volatility Risk Premium (VRPCalculator)
    vrp_absolute DECIMAL(8,4),           -- IV - RV
    vrp_percentage DECIMAL(8,2),         -- (IV - RV) / RV * 100
    realized_vol DECIMAL(8,4),

    -- IV percentile / rank
    iv_percentile_expiry DECIMAL(6,2),   -- per-expiry, 90-day trailing window on our own reconstructed ATM-IV series (two-pass: needs that series first)
    iv_percentile_365d DECIMAL(6,2),     -- market-wide, 365-day DVOL trailing window
    iv_rank_365d DECIMAL(6,2),           -- market-wide, true-range based (matches Deribit)

    -- Expected moves (DVOL-derived, pure formula: dvol/100/sqrt(period) * underlying_price)
    expected_daily_move DECIMAL(16,4),
    expected_weekly_move DECIMAL(16,4),
    expected_monthly_move DECIMAL(16,4),

    -- P/C ratio by moneyness bucket (VolatilitySurfaceCalculator)
    pc_atm_ratio DECIMAL(10,4),
    pc_near_otm_ratio DECIMAL(10,4),
    pc_far_otm_ratio DECIMAL(10,4),

    -- Underlying (denormalized for convenience, matches onchain_analysis_snapshots.underlying_price)
    underlying_price DECIMAL(16,4) NOT NULL,

    -- Metadata
    reconstructed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(snapshot_hour, currency, expiration)
);

CREATE INDEX IF NOT EXISTS idx_onchain_vol_snapshots_currency_time
    ON onchain_volatility_snapshots(currency, snapshot_hour DESC);

CREATE INDEX IF NOT EXISTS idx_onchain_vol_snapshots_expiration
    ON onchain_volatility_snapshots(expiration, snapshot_hour DESC);

COMMENT ON TABLE onchain_volatility_snapshots IS
    'Retroactively reconstructed volatility-surface/VRP/percentile metrics, joined to onchain_analysis_snapshots on (snapshot_hour, currency, expiration) for backtesting';
COMMENT ON COLUMN onchain_volatility_snapshots.skew_25d IS 'Positive = puts more expensive (hedging demand), negative = calls more expensive (upside speculation)';
COMMENT ON COLUMN onchain_volatility_snapshots.net_vanna IS 'Vanna approximation: gamma x vega / spot, OI-weighted and summed';
COMMENT ON COLUMN onchain_volatility_snapshots.net_charm IS 'Charm approximation: -gamma x theta, OI-weighted and summed';
COMMENT ON COLUMN onchain_volatility_snapshots.iv_percentile_expiry IS 'Backfilled in a second pass once the ATM IV time series exists for this expiration';
