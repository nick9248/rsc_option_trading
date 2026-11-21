# Strategy pipeline (facts -> candidates -> model pick -> size -> evaluate)

Research only: no orders. One pipeline for a live run and for a replay of any past instant. Code: `coding/core/analytics/strategy_pipeline/`
(facts, shortlist, report), `coding/core/analytics/candidates/` and `risk/`, `coding/service/strategy_pipeline/`, `scripts/run_strategy_pipeline.py`,
migration 040 (`strategy_picks`).

## Steps
1. **Facts** (`FactsAsOfService`): the numbers the collector stores every hour per expiry (max pain, put/call, GEX/DEX totals, walls; ATM IV, skew,
   expected moves, vanna/charm; 24 h delta flow) plus DVOL and realised volatility from daily candles. Everything is read as of the instant:
   a stored row counts once it was written (or its hour had ended), a row older than 3 hours is missing, and what was not recorded is
   "not recorded", never 0 (the collector zero-fills empty buckets; those are treated as missing). Coverage: levels since 2026-03-15,
   volatility since 2026-07-14, flow since 2026-08-25.
2. **Candidates and metrics**: every defined-risk structure the stored chain offers (see scoring_lab.md), with exact costs and the expected
   result under a flat-volatility view with the implied and one with the realised volatility.
3. **Shortlist**: costs above 15% of the maximum loss are dropped, the rest ranked by expected PnL per risk under the realised view
   (the *baseline rule*), the best two of each family kept (about 14 candidates), numbered by family and strikes so the order does not reveal the ranking.
4. **The model** (claude-opus-5-5, effort medium, structured output) sees the facts and the shortlist and returns a choice (an id or no_trade),
   a ranking of every id, reasons, risks and an invalidation. An answer that is not on the shortlist is stored as invalid.
5. **Size**: from the risk budget (capital x drawdown limit / 4 losses in a row; research default 10,000 USD notional, fractions of a coin allowed,
   as on Derive).
6. **Stored** in `strategy_picks` with the facts, the shortlist, the cost and the request id. An instant is never paid for twice.
7. **Evaluate** after the expiry: every shortlisted candidate is settled on the estimated delivery price; the chain of that instant is regenerated to
   average ALL candidates. Compared in units of the maximum loss (R): model (no_trade counts 0), baseline top-1, shortlist average, all-candidate average.

## Cost control
Total cost of all stored model calls (suggestions and picks) must stay within 15 USD, checked before every call (about 0.04 to 0.08 USD per call).
`replay` needs `--yes` and a cap on calls. Run one process at a time (the check is not atomic).

## Replay is only as honest as its inputs
The chain, facts and volatilities are those of the instant. The delivery price is used only to evaluate. Dates without a fresh snapshot, an index price
or a delivery price are skipped. The model knows the market up to its training date and may recognise a famous date; the facts carry no date of
the event, but the instants are labelled. Take the replay as a test of consistency, not as proof; the forward test is the proof.

## In the Analysis tab
"Ask Claude for strategies" (off by default, about 0.2 USD per currency): after the report, a strategy step asks the model about the expiries nearest 3, 7, 14 and
30 days (one pick each, an expiry met twice once; a pick of the same expiry within the hour is not repeated), on the newest stored chain (the run has just
synced). The Strategy tab of the unified dashboard shows the newest live pick of each open expiry: the choice, its size, the PnL-at-expiry chart, the reasons,
risks and invalidation, and the shortlist in the model's order with the baseline marked. Without the tick the tab shows the picks already stored and nothing is
paid. A missing key, an exhausted budget or an API error fails only that step; the dashboard is still built.

## Method versions
pipeline-1: the shortlist showed a flat-volatility "implied view" that ignored skew (the model read it as fair value). pipeline-2: the market view is exact
(minus costs). Replay of 30 instants (2026-08-30 to 10-27): pipeline-1 model -0.27 R, pipeline-2 -0.36 R, average candidate -0.04, baseline top-1 -0.54 to -0.59:
no evidence yet that the model beats the average candidate.
