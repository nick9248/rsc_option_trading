-- Migration 020: futures_basis persistence check (institutional_metrics_spec.md
-- Migration M4 / section 9, resurrecting the empty futures_basis table).
--
-- Column check (verified against the live local DB before writing this
-- migration): futures_basis already has exactly the 9 columns spec M4
-- calls for -- timestamp, currency, futures_instrument, futures_price,
-- spot_price, basis_absolute, basis_percentage, implied_repo_rate,
-- days_to_expiry (0 rows, confirmed empty). No ALTER needed.
--
-- Index check: the table already carries a unique index
-- (futures_instrument, timestamp) plus separate single-column indexes on
-- currency and timestamp (added outside the numbered migration system,
-- pre-dating this task). Spec M4 asks for a composite unique key that
-- also covers currency, and a composite (currency, timestamp DESC) index
-- for the query pattern institutional_metrics_spec.md section 1's basis
-- percentile actually uses. Both are added here under new names --
-- additive, no rename/drop of the pre-existing indexes in THIS migration
-- (avoids the CLAUDE.md/task-brief STOP condition for touching existing
-- schema objects from this schema-only task).
--
-- *** WAVE E ACTION ITEM (not "decide whether to" -- do this) ***
-- The two unique keys now coexisting are NOT independent constraints that
-- both need to survive: (futures_instrument, timestamp) is strictly
-- STRONGER than (timestamp, currency, futures_instrument), because
-- futures_instrument functionally determines currency (e.g.
-- "BTC-30SEP26" only ever maps to currency='BTC') -- so uq_futures_basis
-- can never reject a row that unique_futures_basis would accept. That is
-- also *why* adding uq_futures_basis alongside it was safe on this
-- 0-row table -- not because "0 rows = no-op" (that only means CREATE
-- didn't fail on existing duplicates; it says nothing about behavior once
-- rows exist). When Wave E wires the daemon writer: DROP the now-
-- redundant unique_futures_basis, and normalize currency casing at write
-- time (the functional dependency above only holds if futures_instrument's
-- currency prefix and the currency column are written in matching case).
--
-- Schema-only in this migration; population (the daemon writer for
-- MarketWideCalculator.calculate_futures_basis, plus BUG 5's fractional-
-- DTE-to-08:00-UTC fix) is Wave E's daemon-wiring work, not this task.

CREATE UNIQUE INDEX IF NOT EXISTS uq_futures_basis
    ON futures_basis (timestamp, currency, futures_instrument);
CREATE INDEX IF NOT EXISTS idx_futures_basis_ccy_time
    ON futures_basis (currency, timestamp DESC);

COMMENT ON TABLE futures_basis IS
    'Futures basis term structure (currency, futures_instrument, timestamp). Columns confirmed to already match institutional_metrics_spec.md Migration M4 (0 rows as of this migration). Populated by MarketWideCalculator.calculate_futures_basis via a daemon writer -- Wave E scope, not yet wired as of this migration.';
