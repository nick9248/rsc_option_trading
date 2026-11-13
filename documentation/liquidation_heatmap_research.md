# Liquidation heatmap (research)

Status: **research only**. Nothing runs in the collector, nothing is stored, no GUI tab. The goal is to find out
whether a heatmap we can compute ourselves from free Binance data is close to the Coinglass one and whether it
predicts anything real. Only if it does, it becomes a collector job, a brief section and a tab (like the taker flow).

## Why our own estimate

The Coinglass API that serves the heatmap needs the Professional plan (about $571 a month billed yearly) and its
intensity is a relative, model-based value. No exchange publishes where positions get liquidated, so every
heatmap of Binance, OKX or Bybit is a model. No published formula was found; the Coinglass docs only say it is
built from market data and a range of leverage amounts.

## The model (v0, `liquidation_heatmap_model.py`)

Every step is an assumption to be tested; see the module docstring for the exact rules.

1. Every 5 minutes, an increase of open interest (BTC) creates new contracts at the closing price, the same
   amount on the long and the short side.
2. They are spread over leverage tiers; isolated-margin liquidation price (simplified, no fees or tiers):
   long = entry x (1 - 1/L + mmr), short = entry x (1 + 1/L - mmr).
3. A level disappears when the price range of a step reaches it, and the liquidated contracts also close their
   other side.
4. What is left of an OI change after those liquidations is voluntary: a rise opens contracts, a fall closes
   contracts proportionally on both sides.
5. The state is warmed up over several days before the window (positions older than that are unknown).

Hand-checked by `tests/unit/liquidation_heatmap/test_liquidation_heatmap_model.py`.

## Data (`binance_history_loader.py`, from the PC only)

- 5-minute klines: Binance futures REST.
- 5-minute open interest: the public archive `data.binance.vision/.../metrics/` for complete UTC days (checked for
  2021, 2022, 2024 and 2026; it also carries long/short account ratios and taker volume ratios), REST for the
  newest days (REST serves about 30 days). The stamp of an OI sample is taken as the OI at the close of the
  candle that opened 5 minutes earlier: an unverified assumption.
- Binance blocks the USA VPS, so this is not a collector job as it stands.

## How to run

```
.venv\Scripts\python -m scripts.research_liquidation_heatmap --end 2026-11-13T14:20Z --hours 24
```

Writes a PNG and a JSON into `outputs/liquidation_heatmap/` (git-ignored).

## What was read off the Coinglass screenshots (Model 1, Binance BTC/USDT, threshold 0.85)

- Bands run to the right until the price touches them; then they stop.
- A cloud of bands follows the price path above (shorts) and below (longs).
- The colour maximum grows with the window (12h and 24h 77.37M, 48h and 3d 91.82M, 1w 106.13M, 2w 133.79M):
  relative, and probably a few preset datasets.
- The price axis spans about 3 % under the lowest and 4 % over the highest price of the window.

## Comparison with the Coinglass screenshots (2026-11-13, six Model 1 screenshots)

Method: the right-edge column of each screenshot is read as a price profile (price axis calibrated from the
top and bottom price labels, colour scale from the colour bar, value in USD per bin; candle pixels ignored).
The same levels appear at the same prices in the 24h, 12h, 48h and 3d screenshots (for example 67,500, 70,240,
72,170 and 72,400, each 40 to 75M USD), so the reading is consistent. Our model is run to the same end time
and its last column is compared. Tuned on 24h and 12h only; 48h, 3d, 1w and 2w kept as hold-out.

Result of v0 (open-interest rise creates contracts, leverage ladder, level removed when touched):

- The timing of band ends follows the price path and looks like Coinglass.
- Which levels are strongest does **not** match. Tuned screenshots: rank correlation +0.1 to +0.4 and a top-10
  overlap of 0.7 to 1.0, but pure chance gives about 0.45 to 0.6 for this overlap, so the gain is small and
  comes mostly from coarser bins. Hold-out screenshots: correlation -0.2 to -0.33, overlap 0.0 to 0.4 (chance
  level or worse). The tuned settings do not generalise.
- Our profile is too flat (many levels of 120 to 170M USD) while Coinglass is peaky (a few levels of 20 to 70M).
- The 1w and 2w screenshots show a very strong block near 75,700 to 77,300 (about 10 % above price) that the
  model does not produce. It looks like short positions opened around 74,500 to 75,000 at 25x to 100x, which a
  rise-of-open-interest rule misses when open interest fell at that time. That points to creation from traded
  volume (as the open-source `py-liquidation-map` does with large trades) instead of from open-interest changes.
- Added setting `position_half_life_hours` (turnover of old positions): it did not help on the hold-out.

## Experiment 1 (2026-11-13): positions created from traded volume

Setting `creation_source="volume"`: taker buy volume opens longs, taker sell volume opens shorts (times a scale
`volume_open_share`, default 0.1), same leverage tiers and touch rule, a touched level removes only its own side,
open interest is not used. Rules fixed before running: choose settings on the 24h and 12h screenshots (72
combinations of tier spread, bin size and half-life), run the four other screenshots once, and add a benchmark
that only peaks near the current price. Same metrics as above (rank correlation on the relevant bins, top-10
overlap with chance about 0.45 to 0.6 on 24h/12h and about 0.2 to 0.3 on the wider windows, scale = our maximum
over theirs).

| Held-out screenshot | Near-price benchmark | Open-interest model (best) | Volume, tiers 25-100/5, bin 100, half-life 48 h | Volume, tiers 10-100/10, bin 100, half-life 48 h |
|---|---|---|---|---|
| 48h | rho -0.40, overlap 0.1 | -0.18, 0.3 | +0.32, 0.5 | +0.25, 0.5 |
| 3d | -0.42, 0.1 | -0.21, 0.4 | +0.41, 0.4 | +0.35, 0.4 |
| 1w | -0.50, 0.0 | -0.31, 0.0 | +0.50, 0.4 | +0.50, 0.4 |
| 2w | -0.41, 0.0 | -0.31, 0.0 | +0.28, 0.2 | +0.40, 0.2 |

(The third configuration of the top 3, tiers 5-100/5 with half-life 12 h, did worse: +0.21, +0.12, -0.10, -0.18.)

Reading: creating positions from volume is clearly better than from open-interest changes. The held-out rank
correlation is positive for two of the three configurations on all four screenshots, where the open-interest
model was negative on all four and the near-price benchmark was negative too. The scale is within the right order
of magnitude (our maximum is 0.2x to 0.8x of Coinglass with the untuned share of 0.1, lower for longer windows, as
Coinglass's own maximum grows with the window). But the top-10 overlap is only modestly above chance (0.4 to 0.5
held out, 0.2 on the 2-week screenshot, which is chance), and by eye our picture is smooth with thick bands where
Coinglass is thin and distinct. So: the right direction, not a match. Three configurations were run on the held-out
set after being chosen on the tuned one, so treat the numbers as indicative, not as a final score.

## Backtest against real liquidations (2026-11-13, 30 days only)

`scripts/backtest_liquidation_heatmap.py`. For each UTC day the volume-model state known at the start of the day is
swept by the day's real low (long levels) and high (short levels); the resulting USD is compared, by rank
correlation, with CryptoQuant's real Binance BTC/USDT daily long and short liquidations. This is a conditional
test (given where the price went, are the levels right?), not a price prediction. A naive predictor (size of the
move times the day's volume) is the control: a heatmap only counts if it adds information beyond it (partial
rank correlation). The three configurations were fixed before the run.

**Data limit: the CryptoQuant key is on the Basic plan: daily window, 30 days of history only** (older dates are
refused with "Out of allowed request range"; Advanced adds hourly and 1 year, Premium full history). So the
sample is 30 days (2026-10-14 to 2026-11-12), one regime (a falling market), and consecutive days are not
independent, so the p-values are optimistic. Treat everything below as a first indication, not a result.
Binance's own liquidation archive no longer exists (404 for every date), and the daily amounts CryptoQuant
records are a sample of the real totals (Binance publishes at most one liquidation per second), so only ranks
are used.

| Config 1 (volume, tiers 25-100x/5, bin 100, half-life 48 h) | Heatmap | Naive, same-day volume | Naive, previous-day volume | Heatmap after removing naive (p) |
|---|---|---|---|---|
| Long liquidations | +0.93 | +0.88 | +0.72 | +0.65 (p < 0.001) |
| Short liquidations | +0.55 | +0.81 | +0.65 | +0.04 (p = 0.84) |

The other two configurations give the same picture (long +0.93/+0.94, partial +0.67/+0.69; short +0.55/+0.56, partial +0.04/+0.05).

Robustness checks (config 1):

- Placebo, heatmap state taken N days earlier than the start of the day (long side, correlation / partial):
  0 days +0.93 / +0.65; 1 day +0.74 / +0.25; 3 days +0.64 / +0.18; 7 days +0.32 / +0.09; 14 days +0.25 / -0.06.
  The fit falls steadily with a staler state, so the current structure of the levels matters.
- Leaving out the single largest day changes nothing (long +0.93, partial +0.64; short +0.50, partial +0.02).
- Level amounts are about 5 to 10 times the recorded liquidations on the big long days (untuned scale share 0.1
  and CryptoQuant undercounting) but not proportional on short days.

Reading: for LONG liquidations the heatmap's levels agree with real liquidations beyond what the size of the move and
the volume explain. For SHORT liquidations it adds nothing (the naive predictor is better), and the model
probably over-creates shorts in a falling market (taker sell volume opens "shorts" even when they are covering).
Not yet shown: that it holds in other market regimes, for ETH, or out of sample in time.

## The recorder and the 7-day forward test (from 2026-11-13)

Why: with 30 days of CryptoQuant data the level accuracy cannot be measured (no real liquidations with a time and a
price). The recorder on the VPS collects exactly what is needed, so that the levels can be judged on data that did not
exist when they were published.

What the collector records (opt-in, `LIQUIDATION_RECORDER_ENABLED=1`, tables from migration 034, synced as optional tables):

1. `liquidation_events`: real liquidations of OKX BTC-USDT-SWAP (public endpoint, no key, works from the VPS), polled
   every 2 minutes: time, liquidated side, price (OKX's bankruptcy price), size (contracts x 0.01 BTC). Binance's own
   feed is not reachable from the USA VPS, so this is a cross-venue check (liquidations happen at the same prices on every
   venue). Side mapping checked on 1000 real events: long liquidations come in falling minutes (mean -0.11 %, rising in
   18 %), short liquidations in rising minutes (+0.14 %, rising in 94 %). About 300 events per hour.
2. `liquidation_heatmap_snapshots`: every hour at :12 UTC the model (the volume configuration 25-100x/5, bin 100,
   half-life 48 h) is computed ON THE VPS from 12 days of Binance 5-minute candles read through the relay, and the levels
   within 10 % of the price are stored with the time they were computed (point in time, never rewritten). A missing or
   too-thin data hour gives no snapshot (a gap, not an estimate).

How it is judged (`scripts/evaluate_liquidation_levels.py`, after `scripts/sync_from_vps.py`): each real liquidation is
scored against the latest snapshot computed BEFORE it. Score = percentile of the snapshot's intensity at the event's
price among the prices within 5 % of the reference price (0.5 = a random price, 1.0 = the strongest level), plus the
share of events in the top 10 % of intensity (random = 10 %). The same events are scored against a snapshot that was
already 24 hours old (control), and the two are compared only on the events that have BOTH scores. A level that is
accurate must beat both chance and the stale control.

Price convention: OKX reports the BANKRUPTCY price of a liquidated position, while the model's levels are TRIGGER prices
(they include the maintenance margin). At 70,000 that is about 280 USD, more than the 100 USD matching window, so the
event price is moved 0.4 % toward the trigger (up for a long, down for a short) before scoring
(`--price-shift-percent`, default 0.4, the model's maintenance margin rate). The OKX maintenance margin rate is not
verified; run the evaluation also with 0 and with 0.8 to see how sensitive the verdict is.

Pass criteria, fixed BEFORE any data (a judgement, adjustable): after 7 days, with at least 500 scored events per side,
a side passes when the mean percentile is at least 0.60 and the top-decile share at least 0.20 (twice chance), and
both are clearly above the stale control (mean percentile at least 0.05 higher). Sides are judged separately (the
earlier backtest suggested long is better than short). A pass means "the levels carry real information"; it does not
mean "exact": the percentile does not tell how far an event is from the level.

Deployment (only on request): deploy the code, apply migration 034 on the VPS, add `LIQUIDATION_RECORDER_ENABLED=1` to the
VPS `.env` (`BINANCE_FUTURES_BASE_URL` is already set for the taker flow), restart the collector. Rollback: remove the
line and restart; the tables can stay.

Known limits: OKX, not Binance, liquidations; OKX publishes filled liquidation orders, which may be a sample; a page of
the feed is cut at a millisecond boundary (events with the same millisecond as the cursor can be missed, rare);
if more than 3000 events arrive between two runs a gap is possible and is logged.

## Next steps

1. Read the 7-day forward test of the recorder (above). The key for CryptoQuant is not on the VPS; the daily Binance
   liquidations can still be pulled from the PC for the same 7 days as a second check (30-day window).
2. Fix the short side (opening versus closing volume, for example by using the open-interest change as a filter).
3. Repeat on other regimes and out of sample in time once there is more history; then ETH.
4. Only after that: a collector job, a brief section and a GUI tab (like the taker flow).

## Where this goes (three sources of levels)

The plan is to compare level sources and look for overlap: (1) these estimated liquidation levels, (2) the options levels
(GEX/DEX call resistance, put support, HVL; later call/put buy-sell levels and block trades). A snapshot is therefore
stored as plain `[price, long_usd, short_usd]` rows with a time, so that a common "levels" view can be built later without
changing the recorder. Only a level source that passed its own accuracy check should enter the comparison.
