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
-- purely additive, no rename/drop of the pre-existing indexes (avoids the
-- CLAUDE.md/task-brief STOP condition for touching existing schema
-- objects; a second unique index on an empty table is a no-op until data
-- exists).
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
