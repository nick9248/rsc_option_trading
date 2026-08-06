-- Migration 024: drop confirmed-dead tables (infra_spec.md section 5, Wave E task E5).
--
-- User-approved 2026-08-06 after presenting the audit's verified writer/reader
-- status for each table. Every table below had BOTH its writer and reader code
-- already removed (confirmed via repo-wide grep, no live code path touches any
-- of them) -- this migration only catches up the schema to match code that has
-- already been dead for weeks. CSV backups of every row in every table below
-- were taken before this migration ran (scratch_backups/<table>_backup_20260806.csv,
-- gitignored, local-only) as a safety net given this project's house convention
-- has no down-migrations/rollback tooling.
--
-- Explicitly EXCLUDED from this drop (KEEP, per the same audit):
--   max_pain, open_interest, volume  -- live readers (GUI trend comparison,
--     OnChainAnalysisService._fetch_trend_data)
--   futures_basis                     -- repurpose target (bugfix_spec D4/Item 5),
--     zero rows today but a live consumer (calculate_futures_basis) is expected
--     to persist into this table eventually -- do not drop.
--
-- Dropped here (all zero-reader, zero-writer, confirmed dead):
--   gex_dex (2,662 rows)               -- writer+reader removed 2026-07-14
--   levels (21,052 rows)               -- writer+reader removed 2026-07-14
--   otm_signals (0 rows)               -- writer removed 2026-07-14, zero callers
--   strategy_signals (132 rows)        -- writer+reader removed 2026-07-14
--   displacement_signals (0 rows)      -- never referenced in coding/ or scripts/;
--                                          NOT in any prior migration file (000-015) --
--                                          created outside the migration system,
--                                          no CREATE to point at, flagged for the record.
--   vol_predictions (2 rows)           -- same undocumented-origin caveat as above.
--   external_metrics (109 rows)        -- infra_spec.md section 3 / D13: never had a
--                                          reader, even while it was being written
--                                          (up to 2026-04-22).
--   regime_detections (33 rows)        -- regime system removed 2026-07-14 (user decision),
--                                          discovered during this audit, not in the
--                                          original task list -- user approved separately.
--   technical_indicators (1,547 rows)  -- same as regime_detections.
--
-- Idempotent (IF EXISTS). No down-migration (house convention has none).

DROP TABLE IF EXISTS gex_dex;
DROP TABLE IF EXISTS levels;
DROP TABLE IF EXISTS otm_signals;
DROP TABLE IF EXISTS strategy_signals;
DROP TABLE IF EXISTS displacement_signals;  -- not migration-tracked, see comment above
DROP TABLE IF EXISTS vol_predictions;       -- not migration-tracked, see comment above
DROP TABLE IF EXISTS external_metrics;
DROP TABLE IF EXISTS regime_detections;
DROP TABLE IF EXISTS technical_indicators;
