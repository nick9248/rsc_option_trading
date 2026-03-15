-- Migration 000: Base schema
-- All tables not created by migrations 001-011
-- Run this first on a fresh database before running other migrations.

-- ============================================================
-- Legacy on-chain GUI tables
-- ============================================================

CREATE TABLE IF NOT EXISTS snapshots (
    id          SERIAL PRIMARY KEY,
    captured_at TIMESTAMP NOT NULL DEFAULT NOW(),
    currency    VARCHAR(10) NOT NULL,
    instrument_name VARCHAR(50) NOT NULL,
    expiration  VARCHAR(20),
    strike      DECIMAL(12,2),
    option_type CHAR(1),
    open_interest DECIMAL(18,8),
    volume      DECIMAL(18,8),
    volume_usd  DECIMAL(18,8),
    underlying_price DECIMAL(18,8),
    mark_price  DECIMAL(18,8),
    bid_price   DECIMAL(18,8),
    ask_price   DECIMAL(18,8)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_currency ON snapshots(currency);
CREATE INDEX IF NOT EXISTS idx_snapshots_captured ON snapshots(captured_at);

CREATE TABLE IF NOT EXISTS max_pain (
    id                  SERIAL PRIMARY KEY,
    captured_at         TIMESTAMP NOT NULL DEFAULT NOW(),
    currency            VARCHAR(10) NOT NULL,
    expiration          VARCHAR(20) NOT NULL,
    max_pain_strike     DECIMAL(12,2) NOT NULL,
    underlying_price    DECIMAL(18,8),
    distance_from_price DECIMAL(18,8),
    distance_percent    DECIMAL(8,4)
);

CREATE INDEX IF NOT EXISTS idx_max_pain_currency ON max_pain(currency);

CREATE TABLE IF NOT EXISTS open_interest (
    id              SERIAL PRIMARY KEY,
    captured_at     TIMESTAMP NOT NULL DEFAULT NOW(),
    currency        VARCHAR(10) NOT NULL,
    expiration      VARCHAR(20) NOT NULL,
    total_call_oi   DECIMAL(18,8),
    total_put_oi    DECIMAL(18,8),
    total_oi        DECIMAL(18,8),
    put_call_ratio  DECIMAL(10,6),
    underlying_price DECIMAL(18,8)
);

CREATE INDEX IF NOT EXISTS idx_oi_currency ON open_interest(currency);

CREATE TABLE IF NOT EXISTS volume (
    id                    SERIAL PRIMARY KEY,
    captured_at           TIMESTAMP NOT NULL DEFAULT NOW(),
    currency              VARCHAR(10) NOT NULL,
    expiration            VARCHAR(20) NOT NULL,
    total_call_volume     DECIMAL(18,8),
    total_put_volume      DECIMAL(18,8),
    total_volume          DECIMAL(18,8),
    volume_put_call_ratio DECIMAL(10,6),
    underlying_price      DECIMAL(18,8)
);

CREATE INDEX IF NOT EXISTS idx_volume_currency ON volume(currency);

CREATE TABLE IF NOT EXISTS levels (
    id              SERIAL PRIMARY KEY,
    captured_at     TIMESTAMP NOT NULL DEFAULT NOW(),
    currency        VARCHAR(10) NOT NULL,
    expiration      VARCHAR(20) NOT NULL,
    level_type      VARCHAR(30) NOT NULL,
    strike          DECIMAL(12,2),
    oi_or_gex_value DECIMAL(18,8),
    underlying_price DECIMAL(18,8)
);

CREATE INDEX IF NOT EXISTS idx_levels_currency ON levels(currency);

CREATE TABLE IF NOT EXISTS gex_dex (
    id                      SERIAL PRIMARY KEY,
    captured_at             TIMESTAMP NOT NULL DEFAULT NOW(),
    currency                VARCHAR(10) NOT NULL,
    expiration              VARCHAR(20) NOT NULL,
    total_net_gex           DECIMAL(18,8),
    total_net_dex           DECIMAL(18,8),
    call_resistance_strike  DECIMAL(12,2),
    call_resistance_gex     DECIMAL(18,8),
    put_support_strike      DECIMAL(12,2),
    put_support_gex         DECIMAL(18,8),
    hvl_strike              DECIMAL(12,2),
    underlying_price        DECIMAL(18,8)
);

CREATE INDEX IF NOT EXISTS idx_gex_dex_currency ON gex_dex(currency);

-- ============================================================
-- Prospective collector tables (time-series data)
-- ============================================================

CREATE TABLE IF NOT EXISTS technical_indicators (
    id              SERIAL PRIMARY KEY,
    currency        VARCHAR(10) NOT NULL,
    date            TIMESTAMP NOT NULL,
    sma_50          DECIMAL(18,8),
    sma_200         DECIMAL(18,8),
    ema_50          DECIMAL(18,8),
    ema_200         DECIMAL(18,8),
    adx             DECIMAL(10,4),
    plus_di         DECIMAL(10,4),
    minus_di        DECIMAL(10,4),
    atr             DECIMAL(18,8),
    atr_percentile  DECIMAL(6,2),
    rsi             DECIMAL(6,2),
    macd            DECIMAL(18,8),
    macd_signal     DECIMAL(18,8),
    macd_histogram  DECIMAL(18,8),
    UNIQUE (currency, date)
);

CREATE TABLE IF NOT EXISTS funding_rate_history (
    id              SERIAL PRIMARY KEY,
    currency        VARCHAR(10) NOT NULL,
    instrument_name VARCHAR(50) NOT NULL,
    timestamp       BIGINT NOT NULL,
    date            TIMESTAMP NOT NULL,
    funding_rate    DECIMAL(18,10) NOT NULL,
    UNIQUE (instrument_name, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_funding_rate_currency ON funding_rate_history(currency);
CREATE INDEX IF NOT EXISTS idx_funding_rate_date ON funding_rate_history(date);

CREATE TABLE IF NOT EXISTS volatility_index_history (
    id          SERIAL PRIMARY KEY,
    currency    VARCHAR(10) NOT NULL,
    index_name  VARCHAR(20) NOT NULL,
    timestamp   BIGINT NOT NULL,
    date        TIMESTAMP NOT NULL,
    dvol        DECIMAL(10,4) NOT NULL,
    UNIQUE (index_name, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_vol_index_currency ON volatility_index_history(currency);
CREATE INDEX IF NOT EXISTS idx_vol_index_date ON volatility_index_history(date);

CREATE TABLE IF NOT EXISTS ohlcv_history (
    id              SERIAL PRIMARY KEY,
    currency        VARCHAR(10) NOT NULL,
    instrument_name VARCHAR(50) NOT NULL,
    timestamp       BIGINT NOT NULL,
    date            TIMESTAMP NOT NULL,
    open            DECIMAL(18,8),
    high            DECIMAL(18,8),
    low             DECIMAL(18,8),
    close           DECIMAL(18,8),
    volume          DECIMAL(18,8),
    UNIQUE (instrument_name, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_currency ON ohlcv_history(currency);
CREATE INDEX IF NOT EXISTS idx_ohlcv_date ON ohlcv_history(date);

CREATE TABLE IF NOT EXISTS external_metrics (
    id                          SERIAL PRIMARY KEY,
    date                        TIMESTAMP NOT NULL,
    fear_greed_value            INTEGER,
    fear_greed_classification   VARCHAR(30),
    btc_dominance               DECIMAL(6,2),
    eth_dominance               DECIMAL(6,2),
    UNIQUE (date)
);

-- ============================================================
-- OTM strategy table
-- ============================================================

CREATE TABLE IF NOT EXISTS dvol_history (
    id          SERIAL PRIMARY KEY,
    asset       VARCHAR(10)   NOT NULL,
    timestamp   TIMESTAMPTZ   NOT NULL,
    dvol_value  DECIMAL(8,4)  NOT NULL,
    UNIQUE (asset, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_dvol_history_asset_ts ON dvol_history(asset, timestamp DESC);
