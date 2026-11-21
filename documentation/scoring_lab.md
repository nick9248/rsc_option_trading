# Candidate generator, metrics and scoring lab

Research only: no orders, no model call, no cost. Code: `coding/core/analytics/candidates/` (generator, metrics, distribution, scoring),
`coding/core/analytics/risk/budget.py`, `coding/service/candidates/scoring_lab_service.py`, `scripts/run_scoring_lab.py`.

## Why
The planned pipeline is: analysis -> every candidate structure of an expiry -> metrics and a deterministic score -> the model chooses among the
best N -> settle and evaluate. The lab answers the cheap part of the question over history: which deterministic rule, if any, picks better
candidates than the average one. No model is involved, so any number of past dates can be scored for free.

## What it does
For every entry day (default 09:00 UTC, the expiry nearest 7 days): the stored chain at that time (fresh, with its index price), every
defined-risk candidate (long call / put, call and put debit and credit spreads, iron condors; strikes by delta range, widths of 2, 4, 6 and 10%
of spot; a buy needs an ask, a sell a bid), and for each candidate:
- exact costs: entry cash at the touch, the spread paid against the mid, trading fees; maximum loss and profit (a long call has unlimited
  profit), breakevens;
- the expected PnL, probability of profit and tail loss (mean of the worst 5% of outcomes) under two lognormal price distributions with the
  expiry's forward as mean: one with the **implied** volatility (ATM mark IV of the expiry) and one with the **realised** volatility (30 daily
  log returns of the perpetual that had finished before the entry day). Fees and delivery fees are inside.
Then each candidate is settled on the estimated delivery price and its realised PnL is divided by its maximum loss ("R"). A rule's pick on a
date is its highest-scoring candidate; the rule is judged by pick R against the average R of all candidates of that date.

Rules: expected PnL per risk under the realised and under the implied distribution, probability of profit (realised), expected PnL per tail
loss, reward to risk, lowest cost.

## Risk budget
`RiskBudget(capital, max drawdown = 20%, losses in a row = 4)`: one trade may lose at most capital x drawdown / 4 (200 USD: 10 USD). A trade is
sized so that its maximum loss equals that. Deribit's minimum order is 0.1 coin for BTC options and 1 coin for ETH options (checked with
get_instruments on 2026-11-21). The lab reports how many candidates fit at the minimum size and what account the cheapest one would need.

## Dated result (BTC, 2026-03-25 to 2026-10-31, 220 dates, 56,879 candidates)
No rule reliably picks winners. The best pick R was +0.10 (expected PnL per risk under the realised volatility) against -0.035 for the average
candidate, but with t = 0.74 over the dates; probability of profit beat the average on 86% of dates with a tiny excess (t = 1.5). Mean R by
family: credit spreads and condors about 0 (they win 76 to 84% of the time and lose it back on the rest), debit spreads -0.24 to -0.26, long
call +0.21 (one August rally), long put -0.16. At 200 USD with the 0.1 BTC minimum **no candidate fits**; the cheapest needs about 265 USD for
one tiny trade, and meaningful choice needs far more.

## Checked
One pick of 2026-06-10 (an iron condor, 19JUN26) recomputed outside the module: entry cash 1,621.86 and fees 73.44 equal raw SQL prices; the
expected PnL under the realised volatility, probability of profit and tail loss equal a 2-million-path Monte Carlo (313.4 against 314.7 +- 1.0;
0.6575 against 0.6579; -2,472.28 both). With a chain priced at one volatility, a structure at the mid has an expected PnL of minus its costs
under that same volatility (unit test).

## Read before trusting
- Dates a week apart share expiries and market moves: t-statistics are optimistic. About 220 dates in one 7-month period is few.
- Iron condors are 62% of the candidates, so "the average candidate" leans towards them.
- Quotes are 30-minute snapshots with fills at the touch and no size limit; the delivery price is an estimate; fees come from third-party
  sources; the lognormal distribution has no skew or fat tails (a flat volatility), so the expected PnL of short-premium structures is flattered.
- The rules are hypotheses chosen before looking; with six of them and no multiple-testing correction, a lucky one is expected.
