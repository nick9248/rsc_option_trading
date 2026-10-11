-- Migration 028: drop four redundant, empty tables.
--
--   max_pain, open_interest, volume  -- legacy per-capture copies of data that
--     onchain_analysis_snapshots already stores for every expiry every hour (max_pain_strike,
--     max_pain_distance_pct, total_call_oi, total_put_oi, put_call_ratio_oi, total_volume,
--     put_call_ratio_volume). Their writers and readers were removed long ago; the only live
--     query that touched open_interest (expiry ordering in the Flow Charts dialog) now reads
--     onchain_analysis_snapshots.
--   buy_sell_flow_metrics            -- a rolling 24 h snapshot of buy/sell volume per strike
--     taken at each analysis run. It would grow by ~25 million rows a year and everything in it
--     is derivable from historical_trades; the Flow Charts now compute it on demand from the
--     stored trades with BuySellFlowAnalyzer.
--
-- Guard: the migration aborts, dropping nothing, if any of the four tables has gained rows.
-- To be applied on the VPS at deployment (a separate, explicit step).
--
-- Idempotent (IF EXISTS). No down-migration (house convention has none).

DO $$
DECLARE
    table_name text;
    row_count bigint;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['max_pain', 'open_interest', 'volume', 'buy_sell_flow_metrics']
    LOOP
        IF to_regclass('public.' || table_name) IS NOT NULL THEN
            EXECUTE format('SELECT count(*) FROM %I', table_name) INTO row_count;
            IF row_count > 0 THEN
                RAISE EXCEPTION 'Migration 028 aborted: % has % rows; refusing to drop it', table_name, row_count;
            END IF;
        END IF;
    END LOOP;
END
$$;

DROP TABLE IF EXISTS max_pain;
DROP TABLE IF EXISTS open_interest;
DROP TABLE IF EXISTS volume;
DROP TABLE IF EXISTS buy_sell_flow_metrics;
