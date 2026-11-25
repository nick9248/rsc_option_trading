# Strategy pipeline (facts -> candidates -> model pick -> size -> evaluate)

Research only: no orders. One pipeline for a live run and for a replay of any past instant. Code: `coding/core/analytics/strategy_pipeline/`
(facts, shortlist, report), `coding/core/analytics/candidates/` and `risk/`, `coding/service/strategy_pipeline/`, `scripts/run_strategy_pipeline.py`,
migration 040 (`strategy_picks`).

## Steps
1. **Facts** (`FactsAsOfService`): the numbers the collector stores every hour per expiry (max pain, put/call, GEX/DEX totals, walls; ATM IV, skew,
   expected moves, vanna/charm; 24 h delta flow) plus DVOL and realised volatility from daily candles. The at-the-money and 25-delta implied volatility
   come from `volatility_skew_history` (full chain, since 2026-08-25), not from the older columns of `onchain_volatility_snapshots`, which were built from
   a few traded instruments (migration 018). The variance risk premium is computed from that implied volatility and the 30-day realised one. From the older
   table: the IV percentile and expected moves (both from DVOL), and the put/call ratios by moneyness, vanna and charm, which cover only the instruments
   traded in the hour (the facts say so). Everything is read as of the instant:
   a stored row counts once it was written (or its hour had ended; the full-chain implied volatility needs both, with 15 minutes after the hour's end,
   because a later collector run overwrites the row), a row older than 3 hours is missing, and what was not recorded is
   "not recorded", never 0 (the collector zero-fills empty buckets; those are treated as missing). Coverage: levels since 2026-03-15,
   implied volatility and skew since 2026-08-25 (other volatility figures since 2026-07-14), flow since 2026-08-25.
2. **Candidates and metrics**: every defined-risk structure the stored chain offers (see scoring_lab.md), with exact costs and the expected
   result under three price models (see "Robust ranking").
3. **Shortlist**: single long options and costs above 15% of the maximum loss are dropped, the rest ranked by the **score** (the lowest expected PnL
   per unit of maximum loss over the three models; the *baseline rule*), the best two of each family kept (about 10 candidates), numbered by family
   and strikes. A live pick offers only candidates with a positive score; with none, the model is not asked (stored as `no_candidates`, no cost).
   A replay keeps them all. The model is shown the three results and the score of every candidate.
4. **The model** (claude-opus-5-5, effort medium, structured output) sees the facts and the shortlist and returns a choice (an id or no_trade),
   a ranking of every id, reasons, risks and an invalidation. An answer that is not on the shortlist is stored as invalid.
5. **Size**: a live pick is stored per 1 coin per leg (the trader takes his or her own fraction; the tab shows what Derive's smallest order means);
   a replay is sized from the risk budget (capital x drawdown limit / 4 losses in a row; 10,000 USD notional, fractions of a coin allowed).
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
30 days (one pick each, an expiry met twice once; a pick that asked the model is not repeated within the hour for the same expiry; a free "no candidates" result is looked at again), on the newest stored chain (the run has just
synced). The Strategy tab of the unified dashboard shows the newest live pick of each open expiry: the choice, its size, the PnL-at-expiry chart, the reasons,
risks and invalidation, and the shortlist in the model's order with each score and the three model results, the baseline marked. At most two picks are
marked "selected trade": fresh ones (4 h) with the best positive score. The report counts the expiry-days with nothing to offer apart: its results cover only
the looks that had a candidate. Without the tick the tab shows the picks already stored and nothing is
paid. A missing key, an exhausted budget or an API error fails only that step; the dashboard is still built.

## Method versions
pipeline-1: the shortlist showed a flat-volatility "implied view" that ignored skew (the model read it as fair value). pipeline-2: the market view is exact
(minus costs). Replay of 30 instants (2026-08-30 to 10-27): pipeline-1 model -0.27 R, pipeline-2 -0.36 R, average candidate -0.04, baseline top-1 -0.54 to -0.59:
no evidence yet that the model beats the average candidate.

pipeline-3 to pipeline-5: see the sections below. pipeline-6: the facts take the at-the-money and 25-delta implied volatility, the skew and the variance risk
premium from the full-chain table.

## Venue (Derive) and method pipeline-3
Since the scale-free change of 2026-11-22 the live runner passes no venue: the following describes the venue path the service still offers, not the Analysis tab.
Positions are traded on Derive; prices come from the Deribit chain the collector stores (a fair proxy: at matching strikes Derive's spreads were 0.86 to 0.92
times Deribit's on 2026-11-21; Derive quotes fewer strikes). Live picks use the Derive rules (`risk/venue.py`, source docs.derive.xyz and
`public/get_all_instruments`, read 2026-11-21): BTC minimum order 0.01 (step 0.00001), ETH 0.1 (step 0.01); taker fee 0.5 USD per order + min(0.03% x notional,
12.5% x premium), one order per leg. Not modelled: maker orders (no base fee, may not fill), RFQ discounts (fee waived on the cheaper leg of a spread), tiers; the
delivery fee is the Deribit rate (not confirmed for Derive). So costs are an upper bound.
- Candidates: spreads from 0.4% of spot wide are tried (the research default starts at 2%); each is sized to the largest allowed size whose maximum loss is within the
  risk per trade (fees at that size, because the base fee per order is spread over fewer coins at a small size); one whose minimum order risks more is dropped.
- As in every live pick, only candidates with a positive score stay; with none left, the model is not asked (no cost) and the tab says so.
- On the 2026-11-21 chain with a 150 USD account (7.5 USD per trade) only single long calls and puts passed the 15% cost filter and all had a clearly negative
  expectation: the answer of the pipeline for such an account is "no trade". Replay (research, Deribit fees, fractions of a coin) is unchanged.

## Robust ranking (method pipeline-4)
Research of 2026-11-22 on 483 BTC and ETH entries (87,225 candidate outcomes, cluster bootstrap over 70 coin x expiry groups; log in the vault) found that the lognormal
at the 30-day realised volatility was badly calibrated (outcomes 45 to 61% more spread out, 90% intervals covered 86 to 87%), had no predictive value for expected PnL
(slope -0.09, interval -0.81 to +0.54) and cost about -0.55% log growth per trade at 5% risk. Realised volatility exceeded implied in the sample: no variance risk premium.
- Models (all with the forward as mean): realised lognormal, implied Student-t (5 degrees of freedom) and HAR-RV Student-t (`har_volatility.py`, fitted on candles complete at the instant).
- Baseline rule: the LOWEST expected PnL per unit of maximum loss over the three models (a candidate is as good as it looks under the least favourable model); a live pick offers only candidates where it is positive. Method pipeline-5 (2026-11-25) also shows the model all three results and the score (pipeline-4 showed
  only the realised view), so the model's choice is no longer independent of the baseline's: read "model against baseline" with that in mind.
- The three models and the score were chosen on data up to 2026-11-22: a replay of earlier instants is in-sample for that choice. The instructions to the
  model describe the models only and quote no result measured on stored data, so a replay gets no later knowledge through them.
- **No model has skew** (one volatility for all strikes, symmetric distribution), so a positive score can be the skew alone. Measured on the chain of 2026-11-25
  21:01 UTC: for the best offered candidate of 4, 11 and 25 December the value of the legs at one at-the-money volatility exceeded their market mid by +109,
  +206 and +206 USD per coin (costs 41, 111, 96; implied fat-tail expected result +43, +91, +110), and every one of the 12 offered candidates had such a gain:
  without it none would have a positive expected result. The score is therefore a bet that the market's skew is wrong. Not proven either way; a skew-aware
  base distribution was tested on 2026-11-25 and NOT adopted: it forecasts no better and its ranking loses significantly (see
  `documentation/research/skew_model_test_2026-11-25.md`). The market's own probabilities are the best calibrated; no ranking shows an edge.
- Single long calls and puts are never shortlisted (lottery: the mean is dominated by a few outcomes that cannot be validated with about 35 expiries a year).
- `validation/edge_evaluation.py`: expected log growth, cluster bootstrap, calibration table, Kelly fraction and a shrunk fractional Kelly (a tenth of Kelly, halved, capped by the drawdown budget).
  The Kelly sizing is not wired into any position size.
- Honest limit: the best rules were at about +0.16% log growth per trade with a lower bound near -0.3%: no edge is proven. Real value comes from the forward test.
