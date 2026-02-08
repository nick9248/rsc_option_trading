-- Migration 006: Add tables for prospective data collection
-- Date: 2026-02-02
-- Purpose: Support hourly automated data capture for ML training

-- ============================================================
-- 1. HISTORICAL_TRADES: Raw trade data from Deribit API
-- ============================================================
CREATE TABLE IF NOT EXISTS historical_trades (
    id SERIAL PRIMARY KEY,

    -- Trade identification
    trade_id VARCHAR(50) NOT NULL UNIQUE,
    trade_seq BIGINT,

    -- Timing
    trade_timestamp BIGINT NOT NULL,  -- Unix timestamp in milliseconds (from Deribit)
    captured_at TIMESTAMP NOT NULL DEFAULT NOW(),

    -- Instrument details
    instrument_name VARCHAR(50) NOT NULL,
    currency VARCHAR(10) NOT NULL,
    expiration VARCHAR(20),  -- Extracted from instrument_name (e.g., "3FEB26")
    strike DECIMAL(12,2),    -- Extracted from instrument_name
    option_type CHAR(1),     -- 'C' or 'P', extracted from instrument_name

    -- Trade data
    price DECIMAL(18,8) NOT NULL,
    amount DECIMAL(18,8) NOT NULL,
    contracts DECIMAL(18,8),
    direction VARCHAR(10) NOT NULL,  -- 'buy' or 'sell'

    -- Market data at trade time
    iv DECIMAL(8,4),  -- IMPLIED VOLATILITY - CRITICAL for Greeks calculation
    mark_price DECIMAL(18,8),
    index_price DECIMAL(18,8),  -- Underlying price at trade time

    -- Metadata
    tick_direction INTEGER,

    -- Indexes for fast queries
    CONSTRAINT unique_trade UNIQUE (trade_id, trade_timestamp)
);

CREATE INDEX idx_historical_trades_instrument ON historical_trades(instrument_name);
CREATE INDEX idx_historical_trades_currency ON historical_trades(currency);
CREATE INDEX idx_historical_trades_timestamp ON historical_trades(trade_timestamp);
CREATE INDEX idx_historical_trades_captured ON historical_trades(captured_at);
CREATE INDEX idx_historical_trades_expiration ON historical_trades(expiration);

COMMENT ON TABLE historical_trades IS 'Raw trade data from Deribit API, captured prospectively';
COMMENT ON COLUMN historical_trades.iv IS 'Implied Volatility (%) - used to calculate Greeks';
COMMENT ON COLUMN historical_trades.index_price IS 'Underlying asset price at trade time (spot/index)';


-- ============================================================
-- 2. CALCULATED_GREEKS: Greeks calculated from IV using Black-Scholes
-- ============================================================
CREATE TABLE IF NOT EXISTS calculated_greeks (
    id SERIAL PRIMARY KEY,

    -- Link to trade or snapshot
    trade_id VARCHAR(50),  -- If linked to specific trade
    instrument_name VARCHAR(50) NOT NULL,
    calculated_at TIMESTAMP NOT NULL DEFAULT NOW(),

    -- Timing
    timestamp BIGINT NOT NULL,  -- Unix timestamp in milliseconds

    -- Instrument details
    currency VARCHAR(10) NOT NULL,
    strike DECIMAL(12,2) NOT NULL,
    expiration VARCHAR(20) NOT NULL,
    option_type CHAR(1) NOT NULL,  -- 'C' or 'P'

    -- Inputs for Greeks calculation
    underlying_price DECIMAL(18,8) NOT NULL,  -- Spot or futures price
    iv DECIMAL(8,4) NOT NULL,  -- Implied Volatility (%)
    time_to_expiry DECIMAL(10,6) NOT NULL,  -- Years (e.g., 0.083 = 30 days)
    risk_free_rate DECIMAL(6,4) DEFAULT 0.05,  -- Risk-free rate (default 5%)

    -- Greeks (Black-Scholes output)
    delta DECIMAL(10,8),      -- Price sensitivity to underlying
    gamma DECIMAL(12,10),     -- Delta sensitivity to underlying
    theta DECIMAL(10,8),      -- Time decay (per day)
    vega DECIMAL(10,8),       -- IV sensitivity
    rho DECIMAL(10,8),        -- Interest rate sensitivity

    -- Model metadata
    calculation_method VARCHAR(50) DEFAULT 'black_scholes',
    is_futures_based BOOLEAN DEFAULT FALSE,  -- TRUE for BTC (futures options)

    -- Indexes
    CONSTRAINT unique_greeks_calc UNIQUE (instrument_name, timestamp)
);

CREATE INDEX idx_calculated_greeks_instrument ON calculated_greeks(instrument_name);
CREATE INDEX idx_calculated_greeks_currency ON calculated_greeks(currency);
CREATE INDEX idx_calculated_greeks_timestamp ON calculated_greeks(timestamp);
CREATE INDEX idx_calculated_greeks_expiration ON calculated_greeks(expiration);

COMMENT ON TABLE calculated_greeks IS 'Greeks calculated from IV using Black-Scholes formula';
COMMENT ON COLUMN calculated_greeks.is_futures_based IS 'TRUE for BTC/ETH options (reference futures, not spot)';


-- ============================================================
-- 3. HOURLY_SNAPSHOTS: Aggregated hourly market state
-- ============================================================
CREATE TABLE IF NOT EXISTS hourly_snapshots (
    id SERIAL PRIMARY KEY,

    -- Timing
    snapshot_hour TIMESTAMP NOT NULL,  -- Hour bucket (e.g., '2026-02-02 14:00:00')
    captured_at TIMESTAMP NOT NULL DEFAULT NOW(),

    -- Instrument details
    instrument_name VARCHAR(50) NOT NULL,
    currency VARCHAR(10) NOT NULL,
    strike DECIMAL(12,2),
    expiration VARCHAR(20),
    option_type CHAR(1),

    -- Aggregated trade data (from historical_trades)
    trade_count INTEGER DEFAULT 0,
    total_volume DECIMAL(18,8),
    vwap DECIMAL(18,8),  -- Volume-weighted average price

    -- Market state (from book_summary API)
    bid_price DECIMAL(18,8),
    ask_price DECIMAL(18,8),
    mark_price DECIMAL(18,8),
    mark_iv DECIMAL(8,4),
    open_interest DECIMAL(18,2),  -- PULLED DIRECTLY FROM API (not inferred!)

    -- Underlying prices
    index_price DECIMAL(18,8),
    futures_price DECIMAL(18,8),  -- For basis calculation
    basis DECIMAL(18,8),  -- futures_price - index_price (BTC/ETH)

    -- Greeks (hourly average or snapshot)
    avg_delta DECIMAL(10,8),
    avg_gamma DECIMAL(12,10),
    avg_theta DECIMAL(10,8),
    avg_vega DECIMAL(10,8),

    -- Unique constraint
    CONSTRAINT unique_hourly_snapshot UNIQUE (instrument_name, snapshot_hour)
);

CREATE INDEX idx_hourly_snapshots_instrument ON hourly_snapshots(instrument_name);
CREATE INDEX idx_hourly_snapshots_currency ON hourly_snapshots(currency);
CREATE INDEX idx_hourly_snapshots_hour ON hourly_snapshots(snapshot_hour);
CREATE INDEX idx_hourly_snapshots_expiration ON hourly_snapshots(expiration);

COMMENT ON TABLE hourly_snapshots IS 'Aggregated hourly market state (trades + book summary + Greeks)';
COMMENT ON COLUMN hourly_snapshots.open_interest IS 'Pulled directly from book_summary API (NOT inferred from volume)';
COMMENT ON COLUMN hourly_snapshots.basis IS 'Futures - Spot spread (for BTC/ETH basis risk tracking)';


-- ============================================================
-- 4. FUTURES_BASIS: Track BTC/ETH futures-spot basis
-- ============================================================
CREATE TABLE IF NOT EXISTS futures_basis (
    id SERIAL PRIMARY KEY,

    -- Timing
    timestamp TIMESTAMP NOT NULL,

    -- Instrument
    currency VARCHAR(10) NOT NULL,
    futures_instrument VARCHAR(50) NOT NULL,  -- e.g., 'BTC-28FEB26'

    -- Prices
    futures_price DECIMAL(18,8) NOT NULL,
    spot_price DECIMAL(18,8) NOT NULL,  -- Index price (Deribit's spot proxy)

    -- Basis calculation
    basis_absolute DECIMAL(18,8),  -- futures - spot ($)
    basis_percentage DECIMAL(8,4),  -- (futures - spot) / spot * 100
    implied_repo_rate DECIMAL(8,4),  -- Annualized carry rate

    -- Time to expiry
    days_to_expiry DECIMAL(10,2),

    -- Unique constraint
    CONSTRAINT unique_futures_basis UNIQUE (futures_instrument, timestamp)
);

CREATE INDEX idx_futures_basis_currency ON futures_basis(currency);
CREATE INDEX idx_futures_basis_timestamp ON futures_basis(timestamp);

COMMENT ON TABLE futures_basis IS 'BTC/ETH futures-spot basis tracking (critical for BTC options Greeks)';
COMMENT ON COLUMN futures_basis.implied_repo_rate IS 'Annualized carry rate implied by basis';


-- ============================================================
-- 5. COLLECTION_LOGS: Track automated collection runs
-- ============================================================
CREATE TABLE IF NOT EXISTS collection_logs (
    id SERIAL PRIMARY KEY,

    -- Timing
    run_timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
    collection_hour TIMESTAMP,  -- The hour being collected

    -- Status
    status VARCHAR(20) NOT NULL,  -- 'success', 'partial', 'failed'

    -- Metrics
    currencies_collected TEXT[],  -- ['BTC', 'ETH']
    trades_collected INTEGER DEFAULT 0,
    instruments_collected INTEGER DEFAULT 0,
    greeks_calculated INTEGER DEFAULT 0,

    -- Duration
    duration_seconds DECIMAL(8,2),

    -- Errors
    error_message TEXT,
    error_count INTEGER DEFAULT 0,

    -- Metadata
    collection_type VARCHAR(50) DEFAULT 'prospective_hourly'
);

CREATE INDEX idx_collection_logs_timestamp ON collection_logs(run_timestamp);
CREATE INDEX idx_collection_logs_status ON collection_logs(status);

COMMENT ON TABLE collection_logs IS 'Audit log for automated data collection runs';


-- ============================================================
-- 6. DATA_QUALITY_CHECKS: Monitor data completeness
-- ============================================================
CREATE TABLE IF NOT EXISTS data_quality_checks (
    id SERIAL PRIMARY KEY,

    -- Timing
    check_timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
    period_start TIMESTAMP NOT NULL,
    period_end TIMESTAMP NOT NULL,

    -- Completeness metrics
    expected_hours INTEGER,
    actual_hours INTEGER,
    completeness_pct DECIMAL(5,2),

    -- Data quality
    missing_iv_pct DECIMAL(5,2),  -- % of trades missing IV
    missing_oi_pct DECIMAL(5,2),  -- % of snapshots missing OI
    greeks_completeness_pct DECIMAL(5,2),

    -- Outliers
    outlier_count INTEGER DEFAULT 0,
    outlier_details JSONB,

    -- Status
    quality_status VARCHAR(20),  -- 'excellent', 'good', 'acceptable', 'poor'

    -- Alerts
    alerts_triggered TEXT[]
);

CREATE INDEX idx_data_quality_timestamp ON data_quality_checks(check_timestamp);

COMMENT ON TABLE data_quality_checks IS 'Automated data quality monitoring and alerts';


-- ============================================================
-- VIEWS for convenient querying
-- ============================================================

-- View: Latest hourly snapshot per instrument
CREATE OR REPLACE VIEW latest_hourly_snapshots AS
SELECT DISTINCT ON (instrument_name) *
FROM hourly_snapshots
ORDER BY instrument_name, snapshot_hour DESC;

COMMENT ON VIEW latest_hourly_snapshots IS 'Most recent hourly snapshot for each instrument';


-- View: Trade volume by hour
CREATE OR REPLACE VIEW hourly_trade_volume AS
SELECT
    DATE_TRUNC('hour', TO_TIMESTAMP(trade_timestamp / 1000.0)) AS hour,
    currency,
    COUNT(*) AS trade_count,
    SUM(amount) AS total_volume,
    COUNT(DISTINCT instrument_name) AS unique_instruments
FROM historical_trades
GROUP BY 1, 2
ORDER BY 1 DESC, 2;

COMMENT ON VIEW hourly_trade_volume IS 'Aggregated trade volume by hour and currency';


-- View: Data collection completeness
CREATE OR REPLACE VIEW collection_completeness AS
SELECT
    DATE_TRUNC('day', run_timestamp) AS collection_date,
    COUNT(*) AS total_runs,
    SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successful_runs,
    SUM(trades_collected) AS total_trades,
    SUM(greeks_calculated) AS total_greeks,
    ROUND(AVG(duration_seconds), 2) AS avg_duration_sec
FROM collection_logs
GROUP BY 1
ORDER BY 1 DESC;

COMMENT ON VIEW collection_completeness IS 'Daily summary of collection pipeline performance';
