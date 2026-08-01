-- Migration 022: block trade columns on historical_trades (institutional_metrics_spec.md
-- Migration M2 / section 9, Task D1).
--
-- block_trade_id exists in the public Deribit trades API but was never
-- persisted -- historical_trades has no way to group legs of the same
-- block/combo trade, so the market-wide "block trades" report section has
-- been using a $100k notional heuristic instead (which measures large
-- single-leg prints, not blocks -- see market_wide_calculator.py's
-- detect_block_trades docstring for the distinction).
--
-- Live-verified 2026-08-02 against get_last_trades_by_currency_and_time
-- (7-day BTC option scan, 4300 trades, 97 carrying block_trade_id --
-- ~2.3% in this window): observed keys on block-tagged trades are exactly
-- block_trade_id, block_trade_leg_count, combo_id, block_rfq_id, contracts
-- (all present in the DDL below). combo_trade_id was also observed
-- (43 trades) but is NOT part of this migration -- out of scope per the
-- spec's exact DDL; flagged in task-D1-report.md as a follow-up candidate.
-- liquidation was not observed live in this sample (it only appears on
-- forced-liquidation trades) but is included per the spec DDL for when it
-- occurs.
--
-- History is NOT backfillable: block_trade_id was never captured before
-- this migration, so historical_trades rows written before this migration
-- landed will always have block_trade_id IS NULL. The block-trade report
-- section must state this migration's effective date as its start date,
-- not claim "no data".

ALTER TABLE historical_trades
    ADD COLUMN IF NOT EXISTS block_trade_id        VARCHAR(64),
    ADD COLUMN IF NOT EXISTS block_trade_leg_count SMALLINT,
    ADD COLUMN IF NOT EXISTS combo_id              VARCHAR(64),
    ADD COLUMN IF NOT EXISTS block_rfq_id          VARCHAR(64),
    ADD COLUMN IF NOT EXISTS liquidation           VARCHAR(8),
    ADD COLUMN IF NOT EXISTS contracts             NUMERIC(20,8);   -- exists but never written

CREATE INDEX IF NOT EXISTS idx_historical_trades_block
    ON historical_trades (currency, block_trade_id) WHERE block_trade_id IS NOT NULL;
