-- Migration 039: when the facts of a strategy suggestion were true.
--
--   strategy_suggestions.facts_as_of   the instant of the option chain and the index price the model was shown (UTC): the capture time of
--                                      the stored snapshot when the chain came from one, else the time of the live fetch. A later
--                                      evaluator measures what happened AFTER this instant. computed_at is when the call was made,
--                                      up to 45 minutes later when the chain came from a stored snapshot.
--
-- NULL only on rows written before this migration (one test row of 2026-11-21). Idempotent. No down-migration.

ALTER TABLE strategy_suggestions ADD COLUMN IF NOT EXISTS facts_as_of timestamptz;
