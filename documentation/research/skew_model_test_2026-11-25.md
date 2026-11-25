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

---

## Result (run 2026-11-25, after the criteria above were committed)
Entries 2026-03-15 to the last settled expiry, BTC and ETH, four horizons: 1,873 entries, 450,792 eligible candidates, 503 currency x expiry clusters.
Command: `scripts.compare_price_models collect` (per currency) then `report`.

**Decision: DO NOT ADOPT.** C1 passed, C2 passed, C3 failed. No edge shown for either ranking.

```
BTC: 934 entries; looks without an entry: {'no snapshot': 4, 'no index price at the snapshot': 4, 'no smile': 1, 'expiry not settled yet': 64}
ETH: 939 entries; looks without an entry: {'no snapshot': 4, 'expiry not settled yet': 64}
Entries 1873, candidates 450,792, clusters (currency x expiry) 503

C1 correctness (market smile at its own width)
  option value minus mid: median 2.606 USD against a median half-spread of 12.12 USD
  candidate expected PnL minus (minus costs): median -0.2873 USD (costs 74.61); current implied one-volatility model: +6.029 USD
  C1 PASSED

C2 forecast of the settlement (CRPS of the log return, lower is better; coverage of the central 50/80/90%)
  realised   CRPS 0.05256  coverage 0.52 / 0.77 / 0.83
  implied_t  CRPS 0.05120  coverage 0.44 / 0.75 / 0.83
  har_t      CRPS 0.05016  coverage 0.54 / 0.81 / 0.88
  smile      CRPS 0.05133  coverage 0.47 / 0.77 / 0.85
  smile_rv   CRPS 0.05273  coverage 0.48 / 0.75 / 0.83
  smile_har  CRPS 0.05064  coverage 0.57 / 0.83 / 0.91
  smile_rv minus realised: +0.00016  95% [-0.00043, +0.00088]
  smile_har minus har_t: +0.00048  95% [-0.00015, +0.00115]
  C2 PASSED

C3 ranking (take the top candidate only if its score is positive; expected log growth per entry at 5% risk)
  all              current   traded   27%  mean R of trades -0.103  log growth -0.1606% [-0.4774, +0.1395]  (n=1873)
  all              candidate traded   30%  mean R of trades -0.482  log growth -0.8209% [-1.1417, -0.4992]  (n=1873)
  3-day expiries   current   traded   17%  mean R of trades +0.039  log growth +0.0266% [-0.0743, +0.1329]  (n=503)
  3-day expiries   candidate traded   48%  mean R of trades -0.511  log growth -1.3283% [-1.6875, -0.9266]  (n=503)
  7-day expiries   current   traded   23%  mean R of trades +0.083  log growth +0.0797% [-0.1234, +0.2889]  (n=491)
  7-day expiries   candidate traded   29%  mean R of trades -0.209  log growth -0.4686% [-1.1423, +0.3737]  (n=491)
  14-day expiries  current   traded   29%  mean R of trades -0.130  log growth -0.2163% [-0.6351, +0.1906]  (n=477)
  14-day expiries  candidate traded   21%  mean R of trades -0.494  log growth -0.6279% [-1.1621, -0.0939]  (n=477)
  30-day expiries  current   traded   42%  mean R of trades -0.279  log growth -0.6221% [-1.6273, +0.3492]  (n=402)
  30-day expiries  candidate traded   18%  mean R of trades -0.907  log growth -0.8455% [-1.4616, -0.3317]  (n=402)
  BTC              current   traded   34%  mean R of trades -0.101  log growth -0.1952% [-0.7081, +0.2969]  (n=934)
  BTC              candidate traded   30%  mean R of trades -0.405  log growth -0.7068% [-1.0697, -0.3328]  (n=934)
  ETH              current   traded   20%  mean R of trades -0.107  log growth -0.1261% [-0.4795, +0.2197]  (n=939)
  ETH              candidate traded   29%  mean R of trades -0.561  log growth -0.9345% [-1.4240, -0.3711]  (n=939)
  first half       current   traded   46%  mean R of trades -0.150  log growth -0.3815% [-0.9707, +0.1826]  (n=930)
  first half       candidate traded   24%  mean R of trades -0.509  log growth -0.6632% [-0.9000, -0.4335]  (n=930)
  second half      current   traded    8%  mean R of trades +0.162  log growth +0.0573% [-0.1185, +0.2295]  (n=943)
  second half      candidate traded   35%  mean R of trades -0.463  log growth -0.9765% [-1.5184, -0.3815]  (n=943)
  benchmark: the average candidate of each entry, always traded: log growth -0.3833%
  candidate minus current: -0.6603%  95% [-1.1007, -0.2094]
  C3 FAILED

Reported, not criteria
  slope of realised R on the current score: +0.37 [-0.09, +0.77] (1 = means what it says)
  slope of realised R on the candidate score: +0.17 [-0.08, +0.37] (1 = means what it says)
  win probability, mean absolute calibration gap: realised 0.045, implied_t 0.029, har_t 0.031, smile 0.010, smile_rv 0.046, smile_har 0.053

DECISION: DO NOT ADOPT the market-smile models (C1 passed, C2 passed, C3 failed)
EDGE: not shown (the interval of the candidate rule includes zero or lies below it)
```

## Reading it
- The market's own distribution (smile at its own width) predicts win probabilities best: mean calibration gap 0.010, against 0.029 to 0.031 for the two
  Student-t models and 0.045 to 0.053 for the realised-volatility based ones.
- Scaling the market's width to the realised or HAR volatility does not forecast better (C2 only shows it is not significantly worse) and the ranking
  built on it loses significantly: -0.82% log growth per entry at 5% risk, 95% interval [-1.14, -0.50]; -0.66% against the current ranking [-1.10, -0.21].
- Exploratory (not a criterion): the candidate rule mostly bought out-of-the-money debit spreads it scored at +0.29 to +0.33 of the risk. Its put debit
  spreads won 5% of the time where the market said 17% (260 picks, mean R -0.90). The width view overstated large moves.
- The current ranking (three one-volatility models, a known skew artefact) trades 27% of entries at -0.16% [-0.48, +0.14]: not distinguishable from zero,
  and not different from a gain or a small loss. Its picks won about as often as the market's probabilities said.
- Limits: eight months, one market regime, overlapping entries (handled by the cluster bootstrap), Deribit quotes at one hour of the day, fees as modelled.

---

## Exploratory second run (2026-11-26, after the result above was seen: it decides nothing)
A code review of the first version found three weaknesses. The first run above used the code of commit 737ff17; this one uses the corrected code.
1. The width-scaled smiles were about 13% wider than the volatility they are named for (the factor divided by the at-the-money volatility, but the
   smile's own deviation is larger). Now the standard deviation of the log return equals the view.
2. Fitted curves implying negative probabilities were silently repaired. A repair above 0.01 now gives no smile (23 looks), and a quote without a
   volatility spread is left out instead of getting an assumed weight.
3. C1's candidate test uses a signed median, which the flawed implied model would also pass (+6.0 USD against a threshold of 7.5). The median absolute
   gap, now reported, separates them: 22.3 USD for the implied Student-t, 2.9 USD for the market smile.

```
BTC: 918 entries; looks without an entry: {'no snapshot': 4, 'no smile': 17, 'no index price at the snapshot': 4, 'expiry not settled yet': 64}
ETH: 933 entries; looks without an entry: {'no snapshot': 4, 'no smile': 6, 'expiry not settled yet': 64}
Entries 1851, candidates 446,182, clusters (currency x expiry) 491

C1 correctness (market smile at its own width)
  option value minus mid: median 2.53 USD against a median half-spread of 12.08 USD
  candidate expected PnL minus (minus costs): median -0.2831 USD (costs 74.61); current implied one-volatility model: +5.993 USD
  C1 PASSED

C2 forecast of the settlement (CRPS of the log return, lower is better; coverage of the central 50/80/90%)
  realised   CRPS 0.05273  coverage 0.52 / 0.77 / 0.83
  implied_t  CRPS 0.05138  coverage 0.44 / 0.75 / 0.83
  har_t      CRPS 0.05033  coverage 0.54 / 0.81 / 0.88
  smile      CRPS 0.05116  coverage 0.47 / 0.77 / 0.85
  smile_rv   CRPS 0.05279  coverage 0.43 / 0.71 / 0.81
  smile_har  CRPS 0.05038  coverage 0.52 / 0.80 / 0.88
  smile_rv minus realised: +0.00006  95% [-0.00070, +0.00082]
  smile_har minus har_t: +0.00004  95% [-0.00067, +0.00079]
  C2 PASSED

C3 ranking (take the top candidate only if its score is positive; expected log growth per entry at 5% risk)
  all              current   traded   27%  mean R of trades -0.101  log growth -0.1591% [-0.4660, +0.1394]  (n=1851)
  all              candidate traded   27%  mean R of trades -0.193  log growth -0.2975% [-0.5259, -0.0964]  (n=1851)
  3-day expiries   current   traded   17%  mean R of trades +0.047  log growth +0.0333% [-0.0632, +0.1379]  (n=490)
  3-day expiries   candidate traded   34%  mean R of trades -0.233  log growth -0.4643% [-0.7578, -0.1517]  (n=490)
  7-day expiries   current   traded   23%  mean R of trades +0.080  log growth +0.0771% [-0.1293, +0.2877]  (n=485)
  7-day expiries   candidate traded   23%  mean R of trades -0.156  log growth -0.1997% [-0.4862, +0.0574]  (n=485)
  14-day expiries  current   traded   29%  mean R of trades -0.123  log growth -0.2064% [-0.6216, +0.2026]  (n=475)
  14-day expiries  candidate traded   23%  mean R of trades -0.138  log growth -0.1940% [-0.5428, +0.1175]  (n=475)
  30-day expiries  current   traded   42%  mean R of trades -0.279  log growth -0.6237% [-1.6341, +0.3492]  (n=401)
  30-day expiries  candidate traded   27%  mean R of trades -0.227  log growth -0.3344% [-0.9310, +0.1730]  (n=401)
  BTC              current   traded   34%  mean R of trades -0.100  log growth -0.1954% [-0.7202, +0.2938]  (n=918)
  BTC              candidate traded   30%  mean R of trades -0.122  log growth -0.2080% [-0.4376, -0.0037]  (n=918)
  ETH              current   traded   20%  mean R of trades -0.104  log growth -0.1233% [-0.4845, +0.2252]  (n=933)
  ETH              candidate traded   24%  mean R of trades -0.279  log growth -0.3855% [-0.7964, -0.0299]  (n=933)
  first half       current   traded   46%  mean R of trades -0.150  log growth -0.3824% [-0.9732, +0.1826]  (n=920)
  first half       candidate traded   33%  mean R of trades +0.090  log growth +0.1200% [-0.1038, +0.3452]  (n=920)
  second half      current   traded    8%  mean R of trades +0.175  log growth +0.0616% [-0.1116, +0.2424]  (n=931)
  second half      candidate traded   21%  mean R of trades -0.618  log growth -0.7100% [-1.0636, -0.3963]  (n=931)
  benchmark: the average candidate of each entry, always traded: log growth -0.3861%
  candidate minus current: -0.1384%  95% [-0.5091, +0.2204]
  C3 PASSED

Reported, not criteria
  median ABSOLUTE gap between the expected PnL under implied_t and minus costs: 22.27 USD
  median ABSOLUTE gap between the expected PnL under smile and minus costs: 2.916 USD
  slope of realised R on the current score: +0.38 [-0.08, +0.79] (1 = means what it says)
  slope of realised R on the candidate score: +0.20 [-0.26, +0.64] (1 = means what it says)
  win probability, mean absolute calibration gap: realised 0.044, implied_t 0.030, har_t 0.031, smile 0.010, smile_rv 0.064, smile_har 0.016

DECISION by C1 to C3: ADOPT the market-smile models (C1 passed, C2 passed, C3 passed). It binds only for the run the criteria were written for.
EDGE: none: the candidate rule LOSES against not trading with 95% confidence (C3 only compares it with the current rule)
```

### Why the app is still unchanged
- This run was made after seeing results, so by the rule written above it is exploratory. All three criteria passing here is not an adoption.
- **C3 was too weak a criterion**: it asks only "not significantly worse than the current rule". The corrected candidate rule still loses against not
  trading with 95% confidence (-0.30% log growth per entry, [-0.53, -0.10]); it gained in the first half (+0.12%, includes zero) and lost in the
  second (-0.71%). A next test must also require the rule not to lose against not trading, on data after 2026-11-26.
- What holds in both runs: the market's own distribution predicts win probabilities best (calibration gap 0.010); no ranking shows an edge.
- Worth a new, pre-registered test: the corrected HAR-width smile was better calibrated than the HAR Student-t (0.016 against 0.031).
