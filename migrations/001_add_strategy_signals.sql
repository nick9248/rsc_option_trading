-- Migration: Add strategy_signals table for storing strategy evaluation results
-- Version: 001
-- Description: Creates table to store scored strategy signals with all metrics

-- Create strategy_signals table
CREATE TABLE IF NOT EXISTS strategy_signals (
    id SERIAL PRIMARY KEY,
    generated_at TIMESTAMP NOT NULL,
    strategy_name VARCHAR(50) NOT NULL,
    currency VARCHAR(10) NOT NULL,
    expiration VARCHAR(20) NOT NULL,

    -- Scores (0-10 scale)
    intrinsic_score DECIMAL(4,2) NOT NULL,
    on_chain_score DECIMAL(4,2) NOT NULL,
    composite_score DECIMAL(4,2) NOT NULL,

    -- Ranking
    rank INTEGER,

    -- Structure (JSON)
    legs JSONB NOT NULL,

    -- Score breakdowns (JSON)
    intrinsic_breakdown JSONB,
    on_chain_breakdown JSONB,

    -- Market context
    underlying_price DECIMAL(12,2) NOT NULL,
    implied_volatility DECIMAL(6,4),
    max_pain_strike DECIMAL(12,2),

    -- Risk metrics
    max_risk DECIMAL(12,2) NOT NULL,
    max_profit DECIMAL(12,2),
    total_cost DECIMAL(12,2) NOT NULL,
    breakeven_points DECIMAL(12,2)[],
    max_loss_percentage DECIMAL(6,2) NOT NULL,

    -- Exit management
    take_profit_percentage DECIMAL(6,2),

    -- Market regime
    market_regime VARCHAR(20),

    -- Greeks
    net_delta DECIMAL(8,6),
    net_gamma DECIMAL(10,8),
    net_theta DECIMAL(8,6),
    net_vega DECIMAL(8,6),

    -- Constraints
    CONSTRAINT strategy_signals_unique UNIQUE (generated_at, strategy_name, currency, expiration),
    CONSTRAINT valid_intrinsic_score CHECK (intrinsic_score >= 0 AND intrinsic_score <= 10),
    CONSTRAINT valid_on_chain_score CHECK (on_chain_score >= 0 AND on_chain_score <= 10),
    CONSTRAINT valid_composite_score CHECK (composite_score >= 0 AND composite_score <= 10),
    CONSTRAINT valid_market_regime CHECK (market_regime IS NULL OR market_regime IN ('bullish', 'bearish', 'neutral'))
);

-- Create indexes for common query patterns
CREATE INDEX idx_strategy_signals_currency_exp ON strategy_signals(currency, expiration);
CREATE INDEX idx_strategy_signals_generated_at ON strategy_signals(generated_at DESC);
CREATE INDEX idx_strategy_signals_composite_score ON strategy_signals(composite_score DESC);
CREATE INDEX idx_strategy_signals_strategy_name ON strategy_signals(strategy_name);
CREATE INDEX idx_strategy_signals_market_regime ON strategy_signals(market_regime) WHERE market_regime IS NOT NULL;

-- Create composite index for filtering by currency, expiration, and score
CREATE INDEX idx_strategy_signals_filter ON strategy_signals(currency, expiration, composite_score DESC);

-- Add comment
COMMENT ON TABLE strategy_signals IS 'Stores scored strategy signals from evaluation system';
COMMENT ON COLUMN strategy_signals.legs IS 'JSON array of strategy legs with strikes, costs, and greeks';
COMMENT ON COLUMN strategy_signals.intrinsic_breakdown IS 'JSON object with intrinsic score component breakdown';
COMMENT ON COLUMN strategy_signals.on_chain_breakdown IS 'JSON object with on-chain score component breakdown';
COMMENT ON COLUMN strategy_signals.max_loss_percentage IS 'Maximum loss as percentage of underlying price';
COMMENT ON COLUMN strategy_signals.take_profit_percentage IS 'Optional take profit target as percentage gain';
