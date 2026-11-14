-- Migration 036: stored decision reports (see documentation/decision_maker.md).
--
--   decision_snapshots   one DecisionReport (coding/core/analytics/decision/) per currency and computation time, stored
--                        exactly as it was produced (point in time), so a later evaluator can score each recorded
--                        scenario against what the price did AFTER it and calibrate the uncalibrated priors. A stored
--                        report is never rewritten: a second save with the same key changes nothing.
--
-- computed_at       the report's as_of (UTC); every evidence item of the report is at or before it
-- reference_price   the spot the report's price map is built around
-- report            DecisionReport.to_dict() (evidence, price map, scenarios, decisions, method_version)
--
-- Written only by scripts/decision_maker.py --save (local). Not synced from the VPS; no VPS job writes it yet.
-- Idempotent (IF NOT EXISTS). No down-migration (house convention has none).

CREATE TABLE IF NOT EXISTS decision_snapshots (
    id               serial PRIMARY KEY,
    currency         varchar(10) NOT NULL,
    computed_at      timestamptz NOT NULL,
    reference_price  numeric NOT NULL CHECK (reference_price > 0),
    report           jsonb NOT NULL,
    CONSTRAINT unique_decision_snapshot UNIQUE (currency, computed_at)
);
