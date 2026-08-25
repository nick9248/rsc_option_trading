-- Migration 018: New volatility_skew_history table (institutional_metrics_spec.md
-- Migration M3 / section 3, RR25 + BF25 term structure).
--
-- The existing stored 25-delta history (onchain_volatility_snapshots.skew_25d/
-- call_25d_iv/put_25d_iv) is degenerate -- it is derived from
-- hourly_snapshots, which is a trade aggregate (~49 instruments/hour), not a
-- chain snapshot (~900 instruments/hour). For 6 of 9 BTC expiries the "25
-- delta" quotes and ATM IV collapse to the same 2-4 traded instruments,
-- producing BF25 = 0.0000 identically (verified 2026-07-24, see spec
-- section 0.1 finding F2). Decision D10: that old history is discarded, not
-- migrated/repaired -- this is a fresh table sourced from the full-chain
-- delta-interpolation method (linear-in-delta, no nearest-delta pick, no
-- extrapolation), populated going forward once C4 wires the daemon writer.
--
-- Schema-only in this migration; population (VolatilitySurfaceCalculator.
-- calculate_risk_reversal_butterfly() + the daemon writer) is Wave C's C4
-- task, not this one.

CREATE TABLE IF NOT EXISTS volatility_skew_history (
    id                SERIAL PRIMARY KEY,
    snapshot_hour     TIMESTAMP    NOT NULL,
    currency          VARCHAR(10)  NOT NULL,
    expiration        VARCHAR(20)  NOT NULL,
    dte_years         NUMERIC(10,6),
    atm_iv_interp     NUMERIC(10,4),   -- interpolated at |delta| = 0.50
    call_25d_iv       NUMERIC(10,4),
    put_25d_iv        NUMERIC(10,4),
    call_25d_strike   NUMERIC(20,2),   -- strike implied by the interpolation bracket midpoint
    put_25d_strike    NUMERIC(20,2),
    rr_25d            NUMERIC(10,4),   -- call25 - put25
    bf_25d            NUMERIC(10,4),   -- (call25+put25)/2 - atm_iv_interp
    n_quotes_used     SMALLINT,        -- chain breadth that produced it
    interp_method     VARCHAR(20) DEFAULT 'linear_delta',
    created_at        TIMESTAMP DEFAULT NOW(),
    UNIQUE (snapshot_hour, currency, expiration)
);

CREATE INDEX IF NOT EXISTS idx_vol_skew_ccy_time
    ON volatility_skew_history (currency, snapshot_hour DESC);
CREATE INDEX IF NOT EXISTS idx_vol_skew_exp_time
    ON volatility_skew_history (expiration, snapshot_hour DESC);

COMMENT ON TABLE volatility_skew_history IS
    'Full-chain-sourced 25-delta risk-reversal/butterfly term structure, one row per (snapshot_hour, currency, expiration). Replaces the degenerate onchain_volatility_snapshots.skew_25d/call_25d_iv/put_25d_iv history (thin-sample bug, see institutional_metrics_spec.md section 0.1 F2) -- that history is kept as-is (frozen, unchanged) and this is a fresh, unrelated series. NULL history before this migration; no backfill (requires snapshots.mark_iv, which only starts populating from Migration M1 forward).';
COMMENT ON COLUMN volatility_skew_history.rr_25d IS
    'RR25 = IV(25-delta call) - IV(25-delta put). Note this is the sign-flip of the legacy skew_25d convention (put - call). Negative = puts bid = downside skew.';
COMMENT ON COLUMN volatility_skew_history.bf_25d IS
    'BF25 = (IV(25-delta call) + IV(25-delta put))/2 - atm_iv_interp. Positive = smile convexity / wings bid.';
COMMENT ON COLUMN volatility_skew_history.n_quotes_used IS
    'Chain breadth (number of quoted instruments) that produced this row. Filter WHERE n_quotes_used >= 8 before feeding percentile windows (institutional_metrics_spec.md section 3c).';
COMMENT ON COLUMN volatility_skew_history.call_25d_iv IS
    'NULL when the chain does not reach 25-delta on the call side (never extrapolated) -- see institutional_metrics_spec.md section 3(b) step 5.';
COMMENT ON COLUMN volatility_skew_history.put_25d_iv IS
    'NULL when the chain does not reach 25-delta on the put side (never extrapolated) -- see institutional_metrics_spec.md section 3(b) step 5.';
