-- Migration: separate index price from per-expiry forward price on
-- onchain_analysis_snapshots (bugfix_spec.md Item 7 / F7.3.3).
--
-- `underlying_price` historically stored the highest-24h-volume option's
-- underlying_price across the WHOLE book (a future, of an arbitrary
-- expiry) applied to every expiration's row -- up to 3.9% off the correct
-- per-expiry basis. New rows populate all three columns going forward:
--   index_price   -- spot index (USD); the correct anchor for GEX/DEX and
--                    any USD conversion.
--   forward_price -- THIS expiration's own future price; the correct
--                    anchor for moneyness/max-pain-distance/breakevens
--                    (settlement-space math).
--   underlying_price -- kept for continuity (deprecated name); populated
--                    with the same value as index_price going forward.
--
-- Backfill is NOT possible: the historical per-expiry forwards were never
-- stored, and faking one would misrepresent history. Existing rows keep
-- their old (global-future) underlying_price value; index_price/
-- forward_price are NULL for rows written before this migration.

ALTER TABLE onchain_analysis_snapshots
    ADD COLUMN IF NOT EXISTS index_price   NUMERIC(16,4),
    ADD COLUMN IF NOT EXISTS forward_price NUMERIC(16,4);

COMMENT ON COLUMN onchain_analysis_snapshots.index_price IS
    'Spot index (USD) -- anchor for GEX/DEX and USD conversion. NULL for rows written before bugfix_spec.md Item 7.';
COMMENT ON COLUMN onchain_analysis_snapshots.forward_price IS
    'This expiry''s own future price -- anchor for moneyness/max-pain distance. NULL for rows written before bugfix_spec.md Item 7.';
COMMENT ON COLUMN onchain_analysis_snapshots.underlying_price IS
    'DEPRECATED: historically the highest-volume option''s underlying_price (a future, of an arbitrary expiry) applied to every expiration. Rows written after bugfix_spec.md Item 7 populate this with the same value as index_price for continuity; use index_price/forward_price instead.';
