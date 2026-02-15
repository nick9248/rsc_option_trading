-- Migration 009: Add buy_sell_flow_metrics table
-- Purpose: Store pre-aggregated per-strike flow data for fast chart queries

CREATE TABLE IF NOT EXISTS buy_sell_flow_metrics (
    id SERIAL PRIMARY KEY,
    captured_at TIMESTAMP NOT NULL DEFAULT NOW(),
    window_hours INTEGER NOT NULL DEFAULT 24,

    -- Grouping keys
    currency VARCHAR(10) NOT NULL,
    expiration VARCHAR(20) NOT NULL,
    strike DECIMAL(12,2) NOT NULL,
    option_type CHAR(1) NOT NULL,  -- 'C' or 'P'

    -- Buy metrics
    buy_count INTEGER NOT NULL DEFAULT 0,
    buy_volume DECIMAL(18,8) NOT NULL DEFAULT 0,
    buy_notional DECIMAL(20,4) NOT NULL DEFAULT 0,

    -- Sell metrics
    sell_count INTEGER NOT NULL DEFAULT 0,
    sell_volume DECIMAL(18,8) NOT NULL DEFAULT 0,
    sell_notional DECIMAL(20,4) NOT NULL DEFAULT 0,

    -- Derived
    net_flow DECIMAL(18,8) NOT NULL,
    buy_sell_ratio DECIMAL(10,4),
    underlying_price DECIMAL(16,4) NOT NULL,

    UNIQUE(captured_at, currency, expiration, strike, option_type)
);

-- Index for efficient queries by expiration and time
CREATE INDEX idx_flow_metrics_expiration_time ON buy_sell_flow_metrics(currency, expiration, captured_at DESC);

-- Index for strike-based queries
CREATE INDEX idx_flow_metrics_strike ON buy_sell_flow_metrics(strike, option_type);

-- Comments
COMMENT ON TABLE buy_sell_flow_metrics IS 'Per-strike buy/sell flow metrics for chart generation';
COMMENT ON COLUMN buy_sell_flow_metrics.window_hours IS 'Lookback window in hours for aggregation';
COMMENT ON COLUMN buy_sell_flow_metrics.net_flow IS 'Buy volume - sell volume';
COMMENT ON COLUMN buy_sell_flow_metrics.buy_sell_ratio IS 'Buy volume / sell volume (NULL if no sells)';
