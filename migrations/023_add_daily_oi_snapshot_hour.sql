-- Migration 023: daily_oi_snapshots daily-anchor hour (institutional_metrics_spec.md
-- Migration M8 / section 7(c), Task E4).
--
-- daily_oi_snapshots has exactly the right shape for the fixed-strike-vol-change
-- feature (per-strike, mark_iv populated -- Task C8's FixedStrikeVolCalculator /
-- get_chain_iv_at) but is GUI-triggered, not daemon-written: [verified] only 5 of
-- the last 40 days present (87.5% missing), and its old
-- ON CONFLICT (snapshot_date, currency, expiration, strike, option_type) DO UPDATE
-- meant the stored value for a given day was "whatever the last GUI run of that
-- day happened to capture", not a fixed, reliable daily anchor.
--
-- This migration adds snapshot_hour_utc (default 8, matching Deribit's settlement-
-- hour convention already used throughout this campaign -- MarketWideCalculator.
-- DERIBIT_SETTLEMENT_HOUR_UTC / GexDexCalculator._DERIBIT_SETTLEMENT_HOUR_UTC /
-- repository.py's _FIXED_STRIKE_VOL_ANCHOR_HOUR_UTC) and widens the unique key to
-- include it, so ProspectiveCollector's new 08:00 UTC daemon write (Task E4) can
-- never be silently overwritten by a later GUI run at a different hour of the same
-- day. Existing rows all get snapshot_hour_utc = 8 via the column default -- the
-- new 6-column key is a strict superset of the old 5-column one, so widening it
-- only weakens uniqueness, never violates it; verified against the live schema
-- (132,570 rows as of this migration, zero possible duplicates on the new key since
-- the old 5-column key was already uniquely enforced).
--
-- The original unique constraint's exact name (Postgres-assigned -- migration 010
-- declared it as an inline UNIQUE(...) with no explicit name) was read from the
-- live schema rather than guessed:
-- daily_oi_snapshots_snapshot_date_currency_expiration_strike_key.
--
-- Idempotent DDL, no down-migration (house convention). The DROP/ADD CONSTRAINT
-- pair is safe to run twice: the second run's DROP CONSTRAINT IF EXISTS on the new
-- name is a no-op the first time and removes/recreates it cleanly the second time.

ALTER TABLE daily_oi_snapshots ADD COLUMN IF NOT EXISTS snapshot_hour_utc SMALLINT DEFAULT 8;

ALTER TABLE daily_oi_snapshots
    DROP CONSTRAINT IF EXISTS daily_oi_snapshots_snapshot_date_currency_expiration_strike_key;

ALTER TABLE daily_oi_snapshots
    DROP CONSTRAINT IF EXISTS unique_daily_oi_snapshot;

ALTER TABLE daily_oi_snapshots
    ADD CONSTRAINT unique_daily_oi_snapshot
    UNIQUE (snapshot_date, snapshot_hour_utc, currency, expiration, strike, option_type);

COMMENT ON COLUMN daily_oi_snapshots.snapshot_hour_utc IS
    'UTC hour this row was anchored to. Default 8 matches Deribit settlement (institutional_metrics_spec.md section 7(c) Migration M8). ProspectiveCollector writes this only when the daemon cycle''s current UTC hour == 8; the pre-existing GUI call path (on_chain_analysis_service.py) does not pass this explicitly and gets the column default.';
