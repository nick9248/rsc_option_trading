-- Migration 030: keep the history of the automatic health checks.
--
-- scripts/check_vps_health.py (hourly cron on the VPS) used to overwrite
-- logs/vps_health.json, so only the latest result existed. Every run is now also stored
-- here: when it ran, how many checks passed, the problems found, every check result and
-- the row count of every watched table. Rows older than 90 days are pruned by the script.
--
-- Idempotent (IF NOT EXISTS). No down-migration (house convention has none).

CREATE TABLE IF NOT EXISTS health_check_runs (
    id          serial PRIMARY KEY,
    run_at      timestamptz NOT NULL,
    total       integer NOT NULL,
    passed      integer NOT NULL,
    problems    jsonb NOT NULL,
    results     jsonb NOT NULL,
    tables      jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_health_check_runs_run_at ON health_check_runs (run_at);
