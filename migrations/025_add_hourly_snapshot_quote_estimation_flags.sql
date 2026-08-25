-- Migration 025: bid_is_estimated / ask_is_estimated on hourly_snapshots
-- (Task Wave-J-E Fix 2).
--
-- hourly_snapshots.bid_price/ask_price have NEVER held a real order-book
-- quote -- despite migration 006's column comment ("Market state (from
-- book_summary API)"), HourlyAggregationService is this table's only
-- writer, and it estimates both from trade prices
-- (coding/service/data_collection/hourly_aggregation_service.py
-- _aggregate_instrument): ask_price = max(buy-trade prices) (or
-- vwap*1.005 if no buy trade occurred that hour), bid_price =
-- min(sell-trade prices) (or vwap*0.995 if no sell trade occurred). The
-- column names assert an observed quote; nothing previously disclosed
-- that the value can be a synthetic +/-0.5% guess around vwap.
--
-- This was latent/harmless while DatabaseRepository.get_hourly_snapshots_
-- for_hour never selected bid_price/ask_price at all (Task Wave-J-E
-- Fix 1's bug): VolatilitySurfaceCalculator._build_delta_points's "quoted"
-- filter (bid_price > 0 or ask_price > 0) always saw both keys absent and
-- always failed, so no synthetic value was ever read back for the
-- historical-reconstruction path. Fixing Fix 1 alone would have made that
-- filter start reading these columns and pass every fallback-derived
-- value as if it were a genuine two-sided quote (both fallbacks are always
-- positive, so the OR-filter would trivially pass 100% of rows regardless
-- of real market presence). These two flags let the filter tell a
-- genuinely trade-derived side (this hour had a real buy/sell execution)
-- from a pure vwap+/-0.5% guess (no trade occurred on that side at all),
-- and refuse to treat a row where BOTH sides are pure guesses as quoted.
--
-- DEFAULT TRUE / backfilled TRUE on existing rows: every row written
-- before this migration came from the same estimate-always logic and its
-- true per-side provenance was never recorded, so TRUE (estimated) is the
-- only honest label for pre-migration rows -- it is not a claim that no
-- historical row ever had genuine trade evidence, only that we cannot
-- reconstruct which did without re-deriving from historical_trades (out
-- of scope here). New rows from HourlyAggregationService populate the
-- accurate per-side value going forward.

ALTER TABLE hourly_snapshots
    ADD COLUMN IF NOT EXISTS bid_is_estimated BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS ask_is_estimated BOOLEAN NOT NULL DEFAULT TRUE;

COMMENT ON COLUMN hourly_snapshots.bid_is_estimated IS
    'TRUE when bid_price is the vwap*0.995 fallback (no sell-side trade this hour to derive a real value from), FALSE when bid_price is min(sell-trade prices) for a genuine trade that hour. Rows written before migration 025 are backfilled TRUE (true provenance not recorded pre-migration).';
COMMENT ON COLUMN hourly_snapshots.ask_is_estimated IS
    'TRUE when ask_price is the vwap*1.005 fallback (no buy-side trade this hour to derive a real value from), FALSE when ask_price is max(buy-trade prices) for a genuine trade that hour. Rows written before migration 025 are backfilled TRUE (true provenance not recorded pre-migration).';
