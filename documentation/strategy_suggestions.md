# Strategy suggestions (model per expiry)

Code prepares, the model proposes, code checks. Research only: nothing here places an order.

## Flow
1. `scripts/suggest_strategies.py` runs the analysis (option chain from the stored snapshot, live when it is older than 45 min).
2. `coding/core/analytics/strategy_suggestion/expiry_facts.py` turns one expiry of the result into text: market state, key levels, the
   statistics of the expiry, the skew row and a chain table with USD quotes (bid / ask / IV / open interest, the 30 strikes closest to spot
   within 25%). It uses the dashboard's own row functions, so every number is on the dashboard. About 3,000 tokens.
3. `StrategySuggestionService` sends it with a fixed system prompt (cached) and a JSON schema (view, strategy name, legs, reasons, risks,
   invalidation). Model `claude-opus-5-5`, effort medium, key `claude_api_key` from `.env`.
4. The answer is parsed and **checked in code**: every leg must be a strike of the chain with the quote its fill needs. Legs are priced by
   code: a buy at the ask, a sell at the bid (net premium; positive = paid).
5. Everything is stored in `strategy_suggestions` (migration 038): the facts shown, the answer as received, status
   (`ok`, `legs_rejected`, `refused`, `invalid_response`), token usage, cost and the request id. A later evaluator scores each row
   against what happened after it.

## Cost control
- Cost of a call is computed from its usage at list prices (Opus 5.5: 4 / 20 USD per million tokens in / out) and stored.
- Before each call the stored total plus a 0.60 USD reserve must stay within the budget (default 15 USD). Otherwise the call is not made.
- More than one expiry in one run needs `--yes`. `--dry-run` shows the facts and their token count (count_tokens, free).
- First real call (27NOV26): 0.06 USD.

## Limits to keep in mind
- Several facts are conventions (assumed dealer positioning, GEX signs), not measurements. The prompt says so.
- Whether the suggestions are any good is unknown until they are scored after expiry. Do not trade on them before that.
- The model is not told about the bid/ask history or fees; the backtester will price those.
