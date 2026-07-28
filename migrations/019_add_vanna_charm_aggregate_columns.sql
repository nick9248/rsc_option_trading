-- Migration 019: Vanna/charm aggregate history columns (institutional_metrics_spec.md
-- Migration M7 / section 4, per-strike VEX/CEX profiles).
--
-- Per-strike vanna/charm history is deliberately NOT persisted (~900
-- rows/hour/currency = ~15.8M rows/year -- see spec section 4(c) and the
-- judgment call in section 11 item 6). Only per-expiry aggregates and peak
-- strikes are stored, folded into the existing onchain_volatility_snapshots
-- table (same grain: snapshot_hour, currency, expiration).
--
-- Legacy net_vanna/net_charm columns (migration 012) are NOT touched --
-- they stay frozen so historical rows keep their old (OI-weighted
-- gamma*vega / -gamma*theta approximation, not true BS vanna/charm)
-- meaning. New code reads only the new columns below.
--
-- Schema-only in this migration; population (ExposureProfileCalculator +
-- the daemon writer) is Wave C's C5 task, not this one.

ALTER TABLE onchain_volatility_snapshots
    ADD COLUMN IF NOT EXISTS vex_holder          NUMERIC(24,4),
    ADD COLUMN IF NOT EXISTS cex_holder          NUMERIC(24,4),
    ADD COLUMN IF NOT EXISTS vex_assumed_dealer  NUMERIC(24,4),
    ADD COLUMN IF NOT EXISTS cex_assumed_dealer  NUMERIC(24,4),
    ADD COLUMN IF NOT EXISTS vex_peak_strike     NUMERIC(20,2),
    ADD COLUMN IF NOT EXISTS cex_peak_strike     NUMERIC(20,2);

COMMENT ON COLUMN onchain_volatility_snapshots.vex_holder IS
    'Total VEX (USD notional delta per +1 vol point), holder-side raw convention (every open contract counted +1). See institutional_metrics_spec.md section 4(b).';
COMMENT ON COLUMN onchain_volatility_snapshots.cex_holder IS
    'Total CEX (USD notional delta per day of decay), holder-side raw convention.';
COMMENT ON COLUMN onchain_volatility_snapshots.vex_assumed_dealer IS
    'Total VEX, assumed-dealer convention (long calls / short puts, SqueezeMetrics sign).';
COMMENT ON COLUMN onchain_volatility_snapshots.cex_assumed_dealer IS
    'Total CEX, assumed-dealer convention.';
COMMENT ON COLUMN onchain_volatility_snapshots.vex_peak_strike IS
    'Strike with the largest |VEX| contribution that hour (holder-side convention).';
COMMENT ON COLUMN onchain_volatility_snapshots.cex_peak_strike IS
    'Strike with the largest |CEX| contribution that hour (holder-side convention).';
