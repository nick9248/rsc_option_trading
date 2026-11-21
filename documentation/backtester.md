# Backtester for simple option structures

Research only: no orders. Code: `coding/core/analytics/backtest/` (structures, fees, engine), `coding/service/backtest/backtest_service.py`,
`scripts/run_backtest.py`.

## What it does
One trade per entry day (default 09:00 UTC) held to expiry. Point in time: the newest stored chain snapshot at or before the entry
(at most 45 min old), the index price of that instant (at most 15 min older than the snapshot), the expiry nearest the target days to
expiry (default 7, at least 2), strikes chosen by Black-Scholes delta from that snapshot only (snapshots without mark IV get it rebuilt
from the mark price and the forward, as in the MenthorQ replay). Buys fill at the ask, sells at the bid. Settlement: the mean index price
of the option trades in the 30 minutes before 08:00 UTC of the expiry (an estimate of Deribit's delivery price; at least 5 trades).

Structures (one coin per leg): `short_strangle` (sell the 16-delta put and call), `put_credit_spread` (sell the 16-delta put, buy the put
about 5% of spot below), `iron_condor` (both spreads), `long_call` and `long_put` (buy the 30-delta option).

Fees: 0.03% of the index per fill (at most 12.5% of the option price); 0.015% of the settlement price per leg that settles in the money
(at most 12.5% of its value). From third-party summaries of Deribit's schedule; settlement fees are waived for some accounts.

## Read before trusting a number
- Trades are independent and overlap (a new one every day, each lives about a week). No margin, no position limit, no compounding. The
  "max drawdown" is of the sum of independent one-coin trades, not of an account.
- USD view: premiums at the entry index, payoffs at the settlement price. Real options are margined and settled in the coin.
- Quotes are snapshots every 30 minutes, taken at the capture time (not at 09:00 sharp): the bid and ask may have moved. Fills are assumed
  at the touch, with no size limit and no slippage beyond the spread. This favours the strategies.
- The delivery price is an estimate (trade-weighted index), not the exchange figure.
- One asset, about seven months (2026-03-20 to 2026-10-31), a few market regimes. A positive total here is not evidence of an edge:
  short-premium structures win often and lose rarely and large (the strangle's worst trade is -12,256 USD for one coin, its best +1,023).
- Hold to expiry only: no stops, no early exit, no adjustments.

## First run (BTC, 2026-03-20 to 2026-10-31, 09:00 UTC, about 7 days to expiry, defaults)
| structure | trades | win rate | total USD | mean | median | worst | max drawdown |
|---|---|---|---|---|---|---|---|
| short_strangle | 225 | 84% | -64,375 | -286 | 508 | -12,256 | 128,199 |
| put_credit_spread | 225 | 91% | 2,989 | 13 | 179 | -3,856 | 29,291 |
| iron_condor | 225 | 81% | -9,437 | -42 | 336 | -3,695 | 41,095 |
| long_call | 225 | 18% | 24,791 | 110 | -650 | -1,203 | 60,661 |
| long_put | 225 | 20% | -33,029 | -147 | -718 | -1,391 | 70,535 |

Month by month the results swing hard (the strangle: +13,938 in April, -35,406 in May, -89,414 in August): the sign of the total depends
on a few large moves. Fees average 46 to 74 USD per trade.

## Checked against raw data
Two trades (the worst put credit spread, 2026-05-30 5JUN26, and a typical winner, 2026-05-16 22MAY26) were recomputed from raw SQL
(snapshot bid/ask, index price, settlement mean, fees): premium, payoff, fees and PnL equal the backtester's to the cent.
