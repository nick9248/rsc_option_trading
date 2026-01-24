-- Migration 003: Add Market Regime Detection Tables
-- Created: 2026-01-19

-- Table: ohlcv_history
-- Stores historical OHLCV data for technical indicator calculations
CREATE TABLE IF NOT EXISTS ohlcv_history (
    id SERIAL PRIMARY KEY,
    currency VARCHAR(10) NOT NULL,
    instrument_name VARCHAR(50) NOT NULL,
    timestamp BIGINT NOT NULL,
    date TIMESTAMP NOT NULL,
    open DECIMAL(20, 8) NOT NULL,
    high DECIMAL(20, 8) NOT NULL,
    low DECIMAL(20, 8) NOT NULL,
    close DECIMAL(20, 8) NOT NULL,
    volume DECIMAL(20, 8),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(instrument_name, timestamp)
);

CREATE INDEX idx_ohlcv_currency_date ON ohlcv_history(currency, date DESC);
CREATE INDEX idx_ohlcv_instrument_date ON ohlcv_history(instrument_name, date DESC);

-- Table: technical_indicators
-- Caches calculated technical indicators
CREATE TABLE IF NOT EXISTS technical_indicators (
    id SERIAL PRIMARY KEY,
    currency VARCHAR(10) NOT NULL,
    date TIMESTAMP NOT NULL,
    sma_50 DECIMAL(20, 8),
    sma_200 DECIMAL(20, 8),
    ema_50 DECIMAL(20, 8),
    ema_200 DECIMAL(20, 8),
    adx DECIMAL(10, 4),
    plus_di DECIMAL(10, 4),
    minus_di DECIMAL(10, 4),
    atr DECIMAL(20, 8),
    atr_percentile DECIMAL(5, 2),
    rsi DECIMAL(10, 4),
    macd DECIMAL(20, 8),
    macd_signal DECIMAL(20, 8),
    macd_histogram DECIMAL(20, 8),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(currency, date)
);

CREATE INDEX idx_technical_currency_date ON technical_indicators(currency, date DESC);

-- Table: funding_rate_history
-- Stores funding rate data for perpetual contracts
CREATE TABLE IF NOT EXISTS funding_rate_history (
    id SERIAL PRIMARY KEY,
    currency VARCHAR(10) NOT NULL,
    instrument_name VARCHAR(50) NOT NULL,
    timestamp BIGINT NOT NULL,
    date TIMESTAMP NOT NULL,
    funding_rate DECIMAL(10, 8) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(instrument_name, timestamp)
);

CREATE INDEX idx_funding_currency_date ON funding_rate_history(currency, date DESC);
CREATE INDEX idx_funding_instrument_date ON funding_rate_history(instrument_name, date DESC);

-- Table: volatility_index_history
-- Stores DVOL (Deribit Volatility Index) history
CREATE TABLE IF NOT EXISTS volatility_index_history (
    id SERIAL PRIMARY KEY,
    currency VARCHAR(10) NOT NULL,
    index_name VARCHAR(50) NOT NULL,
    timestamp BIGINT NOT NULL,
    date TIMESTAMP NOT NULL,
    dvol DECIMAL(10, 4) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(index_name, timestamp)
);

CREATE INDEX idx_dvol_currency_date ON volatility_index_history(currency, date DESC);

-- Table: external_metrics
-- Stores metrics from external free APIs (Fear & Greed, BTC Dominance)
CREATE TABLE IF NOT EXISTS external_metrics (
    id SERIAL PRIMARY KEY,
    date TIMESTAMP NOT NULL,
    fear_greed_value INTEGER,
    fear_greed_classification VARCHAR(20),
    btc_dominance DECIMAL(5, 2),
    eth_dominance DECIMAL(5, 2),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(date)
);

CREATE INDEX idx_external_metrics_date ON external_metrics(date DESC);

-- Table: regime_detections
-- Stores market regime detection results
CREATE TABLE IF NOT EXISTS regime_detections (
    id SERIAL PRIMARY KEY,
    currency VARCHAR(10) NOT NULL,
    detected_at TIMESTAMP NOT NULL,
    regime VARCHAR(30) NOT NULL,
    confidence_score DECIMAL(5, 2) NOT NULL,
    trend_score DECIMAL(5, 2),
    volatility_score DECIMAL(5, 2),
    momentum_score DECIMAL(5, 2),
    onchain_score DECIMAL(5, 2),
    sentiment_score DECIMAL(5, 2),
    current_price DECIMAL(20, 8),
    sma_50 DECIMAL(20, 8),
    sma_200 DECIMAL(20, 8),
    adx DECIMAL(10, 4),
    atr_percentile DECIMAL(5, 2),
    rsi DECIMAL(10, 4),
    funding_rate DECIMAL(10, 8),
    put_call_ratio DECIMAL(10, 4),
    fear_greed INTEGER,
    reasoning TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(currency, detected_at)
);

CREATE INDEX idx_regime_currency_date ON regime_detections(currency, detected_at DESC);
CREATE INDEX idx_regime_regime_date ON regime_detections(regime, detected_at DESC);

-- Comments for documentation
COMMENT ON TABLE ohlcv_history IS 'Historical OHLCV data from Deribit perpetual contracts';
COMMENT ON TABLE technical_indicators IS 'Calculated technical indicators for regime detection';
COMMENT ON TABLE funding_rate_history IS 'Funding rate history for perpetual contracts';
COMMENT ON TABLE volatility_index_history IS 'Deribit Volatility Index (DVOL) historical data';
COMMENT ON TABLE external_metrics IS 'External API data (Fear & Greed Index, BTC Dominance)';
COMMENT ON TABLE regime_detections IS 'Market regime detection results with component scores';
