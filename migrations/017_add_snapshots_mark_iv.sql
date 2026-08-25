-- Migration 017: Add mark_iv to snapshots (institutional_metrics_spec.md Migration M1 / Decision D11)
--
-- `snapshots` already captures the full ~900-instrument option chain hourly
-- (40/40 days complete, verified 2026-07-25) but drops `mark_iv` on write even
-- though `get_book_summary` returns it. This is the cheapest, highest-leverage
-- structural fix in the institutional-metrics research: adding this one column
-- unblocks real full-chain IV history for RR/BF term structure (C4), vanna/
-- charm profiles (C5), the fixed-strike vol matrix (C8), and forward vol (C9)
-- -- all of which currently have no trustworthy history source (see F1/F2/F3
-- in institutional_metrics_spec.md section 0.1).
--
-- Column is NULL for all pre-existing rows (mark_iv was never captured
-- historically); populated going forward from the next daemon tick once the
-- write path (coding/core/database/repository.py::save_snapshot) persists it.
--
-- *** VPS DEPLOYMENT ORDER (Wave E) ***
-- This migration MUST be applied on the VPS BEFORE the corresponding code
-- (repository.py's updated save_snapshot, which now includes mark_iv in the
-- INSERT column list) is deployed there. If the code reaches the VPS first,
-- every hourly save_snapshot call raises UndefinedColumn (mark_iv doesn't
-- exist yet on that DB) -- prospective_collector.py's _fetch_book_summary
-- catches this, logs an error, sets rows_saved=0, and the collection cycle
-- continues silently. The full-chain hourly capture (the exact data source
-- this migration exists to protect -- 40/40 days complete, no gaps) would
-- stop accumulating with NO alert. Migrations-before-code is already the
-- house convention (infra_spec.md section 7's deployment checklist); this
-- note exists because that ordering is unusually high-stakes for this
-- specific column, not routine.

ALTER TABLE snapshots
    ADD COLUMN IF NOT EXISTS mark_iv NUMERIC(10,4);

COMMENT ON COLUMN snapshots.mark_iv IS
    'Mark implied volatility, in percent (e.g. 35.70 = 35.70%), as returned by get_book_summary. NULL for rows written before institutional_metrics_spec.md Migration M1 -- no historical backfill (source field was never captured).';
