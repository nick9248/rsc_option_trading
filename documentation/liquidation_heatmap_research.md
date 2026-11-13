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

## First result (24h window ending 2026-11-13 14:20 UTC, tiers 25/50/100 equal weights)

- The timing of band ends follows the price path and matches Coinglass (for example the bands near 70,900 end when
  price drops through them, and the 68,000 band ends at the 05:55 UTC low).
- The intensity is far more concentrated than Coinglass (strongest bin about 800M USD against 77M): three tiers
  put every step's change into three bins per side. More tiers or a spread of leverage is the first thing to try.
- Which levels are strongest does not match yet (the big Coinglass band near 72,100 is missing in ours).

## Open

- Calibrate the leverage distribution and the bin size on the 12h and 24h screenshots, then check the 48h, 3d, 1w
  and 2w screenshots without tuning (hold-out).
- Check against real liquidations: CryptoQuant daily long/short liquidations (works on the current plan) and a
  recording of Binance's live liquidation feed.
- Use the archive's long/short ratios to see whether they improve the model.
- Known simplifications: isolated margin, no maintenance tiers or fees, one symbol, open interest only.
