-- Migration 042: a pick must name its method version.
--
-- Migration 041 added strategy_picks.method_version with the default 'pipeline-1' so that the existing rows got a label. Keeping that default would
-- label a row from a writer that omits the column as 'pipeline-1'. The default is dropped: a missing version fails loudly. Idempotent. No down-migration.

ALTER TABLE strategy_picks ALTER COLUMN method_version DROP DEFAULT;
