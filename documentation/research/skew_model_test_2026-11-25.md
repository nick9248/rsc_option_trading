# Pre-registered test: does a skew-aware price model rank option structures better? (written 2026-11-25, before any result was computed)

## Models
Current (pipeline-6) ranking: minimum expected PnL per risk over three symmetric one-volatility models: lognormal at 30-day realised volatility,
Student-t(5) at at-the-money implied volatility, Student-t(5) at the HAR forecast.

Candidate: the SHAPE of the market's own distribution (fitted smile of the expiry: skew and tails), with its WIDTH scaled to a volatility view:
log return x' = a + k * (x - E[x]), k = view volatility / at-the-money implied volatility, a chosen so that the mean price is the forward.
- smile_rv: view = 30-day realised volatility. - smile_har: view = HAR forecast. - smile (k = 1): the market itself.
Candidate ranking: minimum expected PnL per risk over smile_rv and smile_har.

## Data
BTC and ETH, one entry per day at 09:00 UTC from 2026-03-15 to the last settled expiry; expiries nearest 3, 7, 14 and 30 days (each once per entry).
Point in time: chain, volatilities and candles of the instant; only the delivery price is later. Overlapping entries: every interval is a block
bootstrap over (currency, expiry) clusters.

## Criteria (all fixed now)
C1 Correctness (the artefact is gone): under smile (k = 1), the value of an option at the fitted smile differs from its market mid by a median of at
   most the median half-spread of those options; and the median over candidates of (expected PnL under smile k=1) minus (market view, i.e. minus costs)
   is within 10% of the candidates' median costs.
C2 Forecast: the continuous ranked probability score (CRPS, lower is better) of the settlement log return for smile_rv is not worse than for the
   lognormal at the same volatility, and smile_har not worse than the Student-t at HAR: "worse" means the 95% interval of the mean difference lies
   entirely above 0. Interval coverage (50/80/90%) is reported.
C3 Ranking: on entries where a rule takes its top candidate only if its score is positive (else no trade, result 0), the expected log growth per
   entry at 5% risk of the candidate rule is not significantly below that of the current rule (95% interval of the difference not entirely below 0).
Reported, not criteria: win-probability calibration; slope of realised R on predicted EV per risk; share of entries traded; results per horizon,
per currency and per half of the period; comparison with taking the average candidate.

## Decision
Adopt in the app if C1, C2 and C3 all hold. Adoption means "the ranking no longer has the known artefact and is not worse"; it does not mean an edge is
proven. An edge would need the candidate rule's own log growth to have a 95% interval above 0.
No model or criterion is changed after seeing results; anything tried afterwards is reported as exploratory.
