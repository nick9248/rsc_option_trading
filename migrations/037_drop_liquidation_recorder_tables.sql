-- Migration 037: drop the liquidation recorder tables created by migration 034.
--
-- The liquidation heatmap research failed its forward test and the feature was removed completely (recorder, snapshot
-- job, models, scripts, consumers). Nothing reads or writes liquidation_events or liquidation_heatmap_snapshots any more.
-- Their indexes and unique constraints are dropped with the tables; no view or foreign key depends on them.
--
-- The migration must be applied on the VPS at deployment, after the collector without the recorder is deployed.
--
-- Idempotent (IF EXISTS). No down-migration (house convention has none).

DROP TABLE IF EXISTS liquidation_events;
DROP TABLE IF EXISTS liquidation_heatmap_snapshots;
