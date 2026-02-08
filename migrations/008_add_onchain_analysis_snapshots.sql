-- Migration: Add on-chain analysis snapshots table
-- Stores GEX/DEX, max pain, support/resistance levels computed every 30 min

CREATE TABLE IF NOT EXISTS onchain_analysis_snapshots (
    id SERIAL PRIMARY KEY,
    snapshot_hour TIMESTAMP NOT NULL,
    currency VARCHAR(10) NOT NULL,
    expiration VARCHAR(20) NOT NULL,

    -- Max Pain
    max_pain_strike DECIMAL(12,2),
    max_pain_distance_pct DECIMAL(8,4),  -- Distance from current price (%)

    -- Put/Call Ratios
    put_call_ratio_oi DECIMAL(8,4),      -- Put OI / Call OI
    put_call_ratio_volume DECIMAL(8,4),  -- Put Volume / Call Volume
    total_call_oi DECIMAL(16,2),
    total_put_oi DECIMAL(16,2),

    -- GEX/DEX (Gamma/Delta Exposure)
    total_net_gex DECIMAL(16,4),         -- Total net gamma exposure
    total_net_dex DECIMAL(16,4),         -- Total net delta exposure
    call_resistance_strike DECIMAL(12,2), -- Strike with max positive GEX
    put_support_strike DECIMAL(12,2),     -- Strike with max negative GEX
    hvl_level DECIMAL(12,2),              -- High Vol Level (zero gamma crossing)

    -- Support/Resistance (OI-based)
    resistance_1_strike DECIMAL(12,2),   -- Top resistance by call OI
    resistance_1_call_oi DECIMAL(16,2),
    support_1_strike DECIMAL(12,2),      -- Top support by put OI
    support_1_put_oi DECIMAL(16,2),

    -- Volume
    total_volume DECIMAL(16,4),

    -- Moneyness (ITM/OTM breakdown)
    itm_call_oi_pct DECIMAL(6,2),        -- % of call OI that's ITM
    otm_call_oi_pct DECIMAL(6,2),        -- % of call OI that's OTM
    itm_put_oi_pct DECIMAL(6,2),
    otm_put_oi_pct DECIMAL(6,2),

    -- Underlying
    underlying_price DECIMAL(16,4) NOT NULL,

    -- Metadata
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(snapshot_hour, currency, expiration)
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_onchain_snapshots_currency_time
    ON onchain_analysis_snapshots(currency, snapshot_hour DESC);

CREATE INDEX IF NOT EXISTS idx_onchain_snapshots_expiration
    ON onchain_analysis_snapshots(expiration, snapshot_hour DESC);

COMMENT ON TABLE onchain_analysis_snapshots IS 'On-chain analysis metrics computed from option Greeks and open interest';
COMMENT ON COLUMN onchain_analysis_snapshots.max_pain_strike IS 'Strike where option sellers minimize payout';
COMMENT ON COLUMN onchain_analysis_snapshots.hvl_level IS 'High Volatility Level - price where gamma exposure crosses zero';
COMMENT ON COLUMN onchain_analysis_snapshots.total_net_gex IS 'Net gamma exposure: (Call Gamma - Put Gamma) × Spot';
