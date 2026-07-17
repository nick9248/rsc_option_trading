-- Migration 014: Straddle scan history (increment 2 forward-testing + Telegram delivery)
-- Additive only -- does not touch onchain_volatility_snapshots or migration 012/013.
-- One row per (currency, expiration, scan_time): the best straddle candidate for that
-- expiry at the time StraddleScanService.scan() ran, recorded so its eventual outcome
-- (settlement P&L) can be resolved and the alert rule can rate-limit sends.

CREATE TABLE IF NOT EXISTS straddle_scan_history (
    id SERIAL PRIMARY KEY,

    scan_time TIMESTAMPTZ NOT NULL,
    currency VARCHAR(10) NOT NULL,
    expiration VARCHAR(20) NOT NULL,

    dte FLOAT NOT NULL,
    future_price FLOAT NOT NULL,
    index_price FLOAT NOT NULL,

    -- Best candidate at scan time
    strike FLOAT NOT NULL,
    call_ask_usd FLOAT,
    put_ask_usd FLOAT,
    cost_usd FLOAT NOT NULL,
    breakeven_down FLOAT NOT NULL,
    breakeven_up FLOAT NOT NULL,

    -- Entry-time metrics (StraddleScanService expiry entry)
    atm_iv FLOAT,
    iv_percentile FLOAT,
    iv_percentile_n_obs INTEGER,
    iv_percentile_window_days FLOAT,
    rv FLOAT,
    rv_iv_ratio FLOAT,
    vrp FLOAT,
    min_pnl_score FLOAT,

    deribit_url TEXT,

    -- Telegram delivery
    alert_sent BOOLEAN NOT NULL DEFAULT FALSE,
    alert_sent_at TIMESTAMPTZ,

    -- Settlement resolution (filled in once the expiry's calendar date has passed)
    resolved_at TIMESTAMPTZ,
    settlement_index_price NUMERIC,
    settlement_pnl_usd NUMERIC,
    settlement_return_pct NUMERIC,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (currency, expiration, scan_time)
);

CREATE INDEX IF NOT EXISTS idx_straddle_scan_history_currency_time
    ON straddle_scan_history(currency, scan_time DESC);

CREATE INDEX IF NOT EXISTS idx_straddle_scan_history_unresolved
    ON straddle_scan_history(currency, resolved_at)
    WHERE resolved_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_straddle_scan_history_last_alert
    ON straddle_scan_history(currency, expiration, alert_sent_at DESC)
    WHERE alert_sent = TRUE;

COMMENT ON TABLE straddle_scan_history IS
    'Increment-2 forward-testing record for the long-straddle scanner. One row per (currency, expiration, scan_time) '
    'best candidate. alert_sent/alert_sent_at track Telegram delivery + rate limiting; resolved_at/settlement_* '
    'fields are filled in once the expiry has settled.';
COMMENT ON COLUMN straddle_scan_history.iv_percentile_n_obs IS
    'Number of valid (non-zero, non-NULL) ATM-IV observations backing iv_percentile -- see get_iv_percentile_with_window.';
COMMENT ON COLUMN straddle_scan_history.iv_percentile_window_days IS
    'Days of history spanned by iv_percentile_n_obs observations -- exposes short-history expiries instead of implying a full-year rank.';
