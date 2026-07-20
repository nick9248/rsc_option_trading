-- Migration 015: Defined-risk scanner history (iron condor / long butterfly)
-- Mirrors straddle_scan_history's shape; one shared table with a
-- structure_type column instead of two near-duplicate tables, since the
-- schema is otherwise identical (see docs/superpowers/specs/2026-07-20-defined-risk-scanner-design.md).

CREATE TABLE IF NOT EXISTS defined_risk_scan_history (
    id SERIAL PRIMARY KEY,

    scan_time TIMESTAMPTZ NOT NULL,
    currency VARCHAR(10) NOT NULL,
    expiration VARCHAR(20) NOT NULL,
    structure_type VARCHAR(20) NOT NULL,  -- 'iron_condor' | 'butterfly'

    dte FLOAT NOT NULL,
    future_price FLOAT NOT NULL,
    index_price FLOAT NOT NULL,

    -- Iron condor legs
    short_call FLOAT, long_call FLOAT, short_put FLOAT, long_put FLOAT,
    -- Butterfly legs
    k1 FLOAT, k2 FLOAT, k3 FLOAT,

    cost_or_credit FLOAT NOT NULL,
    max_loss FLOAT,
    max_profit FLOAT,
    breakeven_lo FLOAT NOT NULL,
    breakeven_hi FLOAT NOT NULL,
    prob_profit FLOAT,
    ev FLOAT,

    -- Regime snapshot at scan time (same value across both structure_types
    -- for a given currency+scan_time -- computed once, shared)
    net_gex FLOAT,
    rv_10d FLOAT,
    rv_30d FLOAT,
    rv_ratio FLOAT,
    gate_pass BOOLEAN,

    deribit_url TEXT,

    alert_sent BOOLEAN NOT NULL DEFAULT FALSE,
    alert_sent_at TIMESTAMPTZ,

    resolved_at TIMESTAMPTZ,
    settlement_index_price NUMERIC,
    settlement_pnl_usd NUMERIC,
    settlement_return_pct NUMERIC,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (currency, expiration, structure_type, scan_time)
);

CREATE INDEX IF NOT EXISTS idx_defined_risk_scan_history_currency_time
    ON defined_risk_scan_history(currency, structure_type, scan_time DESC);

CREATE INDEX IF NOT EXISTS idx_defined_risk_scan_history_unresolved
    ON defined_risk_scan_history(currency, structure_type, resolved_at)
    WHERE resolved_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_defined_risk_scan_history_last_alert
    ON defined_risk_scan_history(currency, expiration, structure_type, alert_sent_at DESC)
    WHERE alert_sent = TRUE;

COMMENT ON TABLE defined_risk_scan_history IS
    'Forward-testing record for the iron condor / long butterfly scanners. One row per '
    '(currency, expiration, structure_type, scan_time) best candidate. gate_pass is the '
    'ORIGINAL theoretically-motivated regime-gate flag (net_gex>0 AND rv_ratio<1) -- kept '
    'as-is even though a 2026-07-20 16-sample check came back backwards, so a larger live '
    'sample can judge the hypothesis cleanly. See docs/superpowers/specs/2026-07-20-defined-risk-scanner-design.md.';
