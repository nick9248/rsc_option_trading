-- Migration 038: strategy suggestions of the model (see documentation/strategy_suggestions.md).
--
--   strategy_suggestions   one answer of the model per expiry and call, stored exactly as received together with the facts it was shown
--                          (point in time), what the call cost, and the legs priced at the quotes of that moment. A later evaluator
--                          scores each stored suggestion against what happened after it. Rows are only added, never changed.
--
-- computed_at       when the call was made (UTC); the facts are of the analysis at or before it
-- reference_price   spot of the facts
-- status            ok | legs_rejected (a leg is not in the chain or has no quote) | refused | invalid_response
-- facts             the exact text shown to the model
-- response          the model's JSON answer (ok, legs_rejected) or its raw text / the refusal reason
-- net_premium_usd   paid (positive) or received (negative) at ask for buys and bid for sells; NULL when not priceable
-- cost_usd          computed from the usage of the call at the model's list prices
--
-- Written only by scripts/suggest_strategies.py (local). Not synced from the VPS. Idempotent (IF NOT EXISTS). No down-migration.

CREATE TABLE IF NOT EXISTS strategy_suggestions (
    id                    serial PRIMARY KEY,
    currency              varchar(10) NOT NULL,
    expiration            varchar(20) NOT NULL,
    computed_at           timestamptz NOT NULL,
    reference_price       numeric NOT NULL CHECK (reference_price > 0),
    model                 varchar(60) NOT NULL,
    status                varchar(20) NOT NULL,
    facts                 text NOT NULL,
    response              text NOT NULL,
    net_premium_usd       numeric,
    input_tokens          integer NOT NULL,
    output_tokens         integer NOT NULL,
    cache_read_tokens     integer NOT NULL DEFAULT 0,
    cache_write_tokens    integer NOT NULL DEFAULT 0,
    cost_usd              numeric(12,6) NOT NULL CHECK (cost_usd >= 0),
    request_id            varchar(80)
);

CREATE INDEX IF NOT EXISTS idx_strategy_suggestions_lookup ON strategy_suggestions (currency, expiration, computed_at);
