-- Migration 040: the strategy pipeline's picks (see documentation/strategy_pipeline.md).
--
--   strategy_picks   one answer of the model per expiry and instant: the facts and the shortlist of candidates it was shown, its choice
--                    (a candidate id or no_trade) and ranking, the position size the risk budget allows, what the call cost, and - once the
--                    expiry has settled - what the choice, the deterministic top candidate and the average candidate really earned.
--                    The same row type serves a live run (mode 'live', as_of = now) and a replay of a past instant (mode 'replay').
--
-- as_of          the instant of the chain, the spot and the facts (UTC); nothing newer was used to build the row
-- candidates     the shortlist as shown: id, family, legs with fill prices, entry cash, max loss and profit, breakevens, costs, metrics
-- outcomes       after evaluation: per candidate id the realised PnL per coin and in units of the maximum loss (R), plus the mean of the
--                shortlist and of all candidates of that instant
-- Unique per (currency, expiration, as_of, mode): a replay never pays twice for the same instant.
--
-- Written only by scripts/run_strategy_pipeline.py (local). Not synced from the VPS. Idempotent (IF NOT EXISTS). No down-migration.

CREATE TABLE IF NOT EXISTS strategy_picks (
    id                    serial PRIMARY KEY,
    currency              varchar(10) NOT NULL,
    expiration            varchar(20) NOT NULL,
    mode                  varchar(10) NOT NULL CHECK (mode IN ('live', 'replay')),
    as_of                 timestamptz NOT NULL,
    computed_at           timestamptz NOT NULL,
    spot                  numeric NOT NULL CHECK (spot > 0),
    model                 varchar(60) NOT NULL,
    status                varchar(20) NOT NULL,
    facts                 text NOT NULL,
    candidates            jsonb NOT NULL,
    choice                varchar(20),
    ranking               jsonb,
    response              text NOT NULL,
    quantity              numeric,
    max_loss_usd          numeric,
    risk_per_trade_usd    numeric,
    input_tokens          integer NOT NULL,
    output_tokens         integer NOT NULL,
    cache_read_tokens     integer NOT NULL DEFAULT 0,
    cache_write_tokens    integer NOT NULL DEFAULT 0,
    cost_usd              numeric(12,6) NOT NULL CHECK (cost_usd >= 0),
    request_id            varchar(80),
    baseline_id           varchar(20),
    evaluated_at          timestamptz,
    settlement_price      numeric,
    outcomes              jsonb,
    CONSTRAINT unique_strategy_pick UNIQUE (currency, expiration, as_of, mode)
);

CREATE INDEX IF NOT EXISTS idx_strategy_picks_lookup ON strategy_picks (currency, mode, as_of);
