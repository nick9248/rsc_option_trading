-- Migration 010: Add daily OI snapshots table
-- Stores daily snapshots of per-strike OI and IV for:
-- - Day-over-day OI change detection (large positioning shifts)
-- - IV percentile per expiry (historical ATM IV tracking)

CREATE TABLE IF NOT EXISTS daily_oi_snapshots (
    id SERIAL PRIMARY KEY,
    snapshot_date DATE NOT NULL,
    currency VARCHAR(10) NOT NULL,
    expiration VARCHAR(20) NOT NULL,
    strike DECIMAL(12,2) NOT NULL,
    option_type CHAR(1) NOT NULL,
    open_interest DECIMAL(16,2) NOT NULL,
    mark_iv DECIMAL(10,4),
    underlying_price DECIMAL(16,4),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(snapshot_date, currency, expiration, strike, option_type)
);

-- Index for querying previous day's OI for change detection
CREATE INDEX IF NOT EXISTS idx_daily_oi_date_currency_exp
    ON daily_oi_snapshots(snapshot_date DESC, currency, expiration);

-- Index for ATM IV history queries
CREATE INDEX IF NOT EXISTS idx_daily_oi_iv_history
    ON daily_oi_snapshots(currency, expiration, strike, snapshot_date DESC);

COMMENT ON TABLE daily_oi_snapshots IS 'Daily snapshots of per-strike OI and IV for change detection and percentile analysis';
