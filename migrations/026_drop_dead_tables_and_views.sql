-- Migration 026: drop tables and views confirmed dead on 2026-10-11.
--
-- Found while working out why 13 tables were not part of the VPS -> local sync
-- (scripts/sync_from_vps.py). Five of them are live and are now synced; the rest
-- are dead. Verified before dropping:
--
--   * zero references in coding/, scripts/ and tests/ (repo-wide grep, whole-word);
--   * zero rows on the VPS and locally (tables); the three views hold only
--     derived data, so no data is lost by dropping them.
--
-- Dropped here:
--   VIEW  collection_completeness   -- built on collection_logs; no consumer.
--   VIEW  hourly_trade_volume       -- built on historical_trades; no consumer.
--   VIEW  latest_hourly_snapshots   -- built on hourly_snapshots; no consumer.
--   TABLE calculated_greeks         -- 0 rows, never referenced (migration 006).
--   TABLE data_quality_checks       -- 0 rows, never referenced (migration 006).
--   TABLE collection_logs           -- 0 rows ever: the only writer,
--                                      repo.save_collection_log(), does not exist,
--                                      so every call from the daemon failed and was
--                                      swallowed. The daemon now logs gaps to the
--                                      journal instead (collection_daemon._log_gap).
--
-- KEPT although empty: futures_basis (a live consumer is expected to persist
-- into it, see 024) and buy_sell_flow_metrics (repository readers still exist).
--
-- To apply on the VPS, run this file there at the next deployment (a separate,
-- explicit step). Until then the VPS keeps these empty objects; nothing breaks.
--
-- Idempotent (IF EXISTS). No down-migration (house convention has none).

-- Safety guard: refuse to drop a table that has gained rows since it was checked.
DO $$
DECLARE
    table_name text;
    row_count bigint;
BEGIN
    FOREACH table_name IN ARRAY ARRAY['calculated_greeks', 'data_quality_checks', 'collection_logs']
    LOOP
        IF to_regclass('public.' || table_name) IS NOT NULL THEN
            EXECUTE format('SELECT count(*) FROM %I', table_name) INTO row_count;
            IF row_count > 0 THEN
                RAISE EXCEPTION 'Migration 026 aborted: % has % rows; refusing to drop it', table_name, row_count;
            END IF;
        END IF;
    END LOOP;
END
$$;

DROP VIEW IF EXISTS collection_completeness;
DROP VIEW IF EXISTS hourly_trade_volume;
DROP VIEW IF EXISTS latest_hourly_snapshots;

DROP TABLE IF EXISTS calculated_greeks;
DROP TABLE IF EXISTS data_quality_checks;
DROP TABLE IF EXISTS collection_logs;
