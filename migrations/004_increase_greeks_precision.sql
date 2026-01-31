-- Migration: Increase precision for greeks columns to support BTC options
-- Version: 004
-- Description: BTC options have much larger greek values than ETH, need bigger columns

-- Increase precision for greeks to handle BTC options
-- BTC vega can be 200+, theta can be -100+, etc.
ALTER TABLE strategy_signals
    ALTER COLUMN net_delta TYPE DECIMAL(12,6),
    ALTER COLUMN net_gamma TYPE DECIMAL(12,8),
    ALTER COLUMN net_theta TYPE DECIMAL(12,6),
    ALTER COLUMN net_vega TYPE DECIMAL(12,6);

-- Add comment explaining the change
COMMENT ON COLUMN strategy_signals.net_delta IS 'Net delta across all legs (supports BTC options with large values)';
COMMENT ON COLUMN strategy_signals.net_gamma IS 'Net gamma across all legs (supports BTC options with large values)';
COMMENT ON COLUMN strategy_signals.net_theta IS 'Net theta across all legs (supports BTC options with large values)';
COMMENT ON COLUMN strategy_signals.net_vega IS 'Net vega across all legs (supports BTC options with large values)';
