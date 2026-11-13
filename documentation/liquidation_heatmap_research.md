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

## Next experiments

1. Creation from traded volume (taker buy volume to longs, taker sell volume to shorts, only the part that
   opens positions) instead of open-interest changes; judge on the hold-out screenshots without further tuning.
2. Whatever the result, validate against real liquidations (CryptoQuant daily long/short liquidations, and a
   recording of Binance's live liquidation feed): the aim is a metric that is right, not a copy of Coinglass.
3. Add a trivial benchmark (profile that only peaks near the current price) to every comparison.
