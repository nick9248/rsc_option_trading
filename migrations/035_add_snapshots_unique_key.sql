-- Migration 035: a unique key on the raw option-chain snapshots, one row per instrument per capture.
--
-- ``snapshots`` had only a serial id, so inserting the same chain twice (two overlapping VPS syncs, a collector
-- retry) silently doubled it. The natural key is (captured_at, instrument_name): a capture is one instant and an
-- instrument appears once in it. Checked before writing this migration: the local copy (19.7 million rows, 23,426
-- captures) holds no duplicate of this key, so nothing is deleted. Apply on a database that has duplicates ONLY after
-- removing them (CREATE UNIQUE INDEX fails if any exist, and changes nothing).
--
-- Writers use ``ON CONFLICT DO NOTHING`` (DatabaseRepository.save_snapshot has no conflict target on purpose, so
-- it works with or without this index; the VPS sync names the key). Building the index on the big table takes a
-- while and blocks writes to ``snapshots`` meanwhile: on the VPS apply it at a quiet moment (a separate deployment
-- step). Idempotent (IF NOT EXISTS). No down-migration (house convention has none).

CREATE UNIQUE INDEX IF NOT EXISTS unique_snapshots_capture_instrument
    ON snapshots (captured_at, instrument_name);
