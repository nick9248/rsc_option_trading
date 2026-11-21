-- Migration 041: a method version on the pipeline's picks.
--
-- The same instant can be picked again by an improved method (a corrected prompt or metric) without paying for or overwriting the old
-- answer: the uniqueness of a pick is now (currency, expiration, as_of, mode, method_version). Existing rows are 'pipeline-1'.
-- Idempotent. No down-migration.

ALTER TABLE strategy_picks ADD COLUMN IF NOT EXISTS method_version varchar(20) NOT NULL DEFAULT 'pipeline-1';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'unique_strategy_pick') THEN
        ALTER TABLE strategy_picks DROP CONSTRAINT unique_strategy_pick;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'unique_strategy_pick_version') THEN
        ALTER TABLE strategy_picks ADD CONSTRAINT unique_strategy_pick_version
            UNIQUE (currency, expiration, as_of, mode, method_version);
    END IF;
END $$;
