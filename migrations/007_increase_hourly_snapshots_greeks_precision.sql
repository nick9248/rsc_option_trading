-- Migration: Increase precision for Greeks in hourly_snapshots to support BTC options
-- Version: 007
-- Date: 2026-02-03
-- Description: BTC options have much larger Greek values than ETH
--              Theta can be -200+, Vega can be 200+

-- Step 1: Drop view that depends on the columns we're altering
DROP VIEW IF EXISTS latest_hourly_snapshots;

-- Step 2: Increase precision for avg_theta and avg_vega
-- DECIMAL(10,8) allows -99.99 to 99.99 (TOO SMALL for BTC)
-- DECIMAL(12,8) allows -9999.99 to 9999.99 (sufficient for BTC at $100k)
ALTER TABLE hourly_snapshots
    ALTER COLUMN avg_theta TYPE DECIMAL(12,8),
    ALTER COLUMN avg_vega TYPE DECIMAL(12,8);

-- Step 3: Recreate the view
CREATE VIEW latest_hourly_snapshots AS
SELECT DISTINCT ON (instrument_name) *
FROM hourly_snapshots
ORDER BY instrument_name, snapshot_hour DESC;

-- Add comments explaining the change
COMMENT ON COLUMN hourly_snapshots.avg_theta IS 'Average theta (per day) - DECIMAL(12,8) to support BTC options with large values';
COMMENT ON COLUMN hourly_snapshots.avg_vega IS 'Average vega (IV sensitivity) - DECIMAL(12,8) to support BTC options with large values';
COMMENT ON VIEW latest_hourly_snapshots IS 'Most recent hourly snapshot for each instrument';

-- Note: avg_delta and avg_gamma are fine at current precision
-- Delta is always -1 to 1, so DECIMAL(10,8) is sufficient
-- Gamma is always small (typically < 1), so DECIMAL(12,10) is sufficient
