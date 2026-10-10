-- Migration 027: merge volatility_index_history into dvol_history (the single DVOL table).
--
-- Both tables held the same Deribit hourly DVOL series. volatility_index_history
-- (daemon, from 2026-03-15) stored whatever the latest candle showed at collection
-- time, i.e. a provisional close, and was the only source for the hours missing from
-- dvol_history (2026-07-16 14:00 to 2026-08-25 13:00 UTC, when its feeder had stopped).
-- Before this migration, scripts/backfill_dvol_history.py was re-run: it refetches the
-- full history from Deribit, fills that hole and corrects provisional values, so
-- dvol_history now holds the final close of every completed hourly candle.
--
-- Guard: the migration aborts, dropping nothing, if any COMPLETED hour that exists in
-- volatility_index_history is still missing from dvol_history. The hour still forming
-- is ignored: dvol_history only stores completed candles.
--
-- Backups: scratch_backups/volatility_index_history_backup_<date>.csv (gitignored).
-- The migration must be applied on the VPS at deployment, after running the refresh
-- script there.
--
-- Idempotent (IF EXISTS). No down-migration (house convention has none).

DO $$
DECLARE
    missing_hours bigint;
BEGIN
    IF to_regclass('public.volatility_index_history') IS NOT NULL THEN
        SELECT count(*) INTO missing_hours
        FROM volatility_index_history v
        WHERE v.date < date_trunc('hour', now() AT TIME ZONE 'UTC')
          AND NOT EXISTS (
            SELECT 1 FROM dvol_history d
            WHERE d.asset = v.currency AND d.timestamp = v.date AT TIME ZONE 'UTC'
        );
        IF missing_hours > 0 THEN
            RAISE EXCEPTION 'Migration 027 aborted: % hours of volatility_index_history are missing from dvol_history; run scripts/backfill_dvol_history.py first', missing_hours;
        END IF;
    END IF;
END
$$;

DROP TABLE IF EXISTS volatility_index_history;
