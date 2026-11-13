# Futures taker flow (Binance BTC and ETH perpetuals)

The taker buy/sell ratio of the Binance USD-M perpetual futures. This is the **futures
market**, not the options market: it shows how aggressive buyers and sellers were in the
instrument most traders use to take a directional view. It is shown separately from the
MenthorQ-style option levels.

**Taker buy/sell ratio** = taker buy volume / taker sell volume. A taker is the side that crossed
the spread. Above 1 buyers were more aggressive, below 1 sellers were. Over several hours or days
it is total buy volume over total sell volume (volume-weighted), never an average of ratios.
Volumes are in USD (USDT).

## What it does

| Part | Where | What |
|---|---|---|
| Collector job | `coding/service/futures_flow/taker_flow_job.py`, registered in `unified_scheduler` | Every hour at :05 UTC (and once at start-up) stores the closed hours of the last 30 days that are missing from `taker_flow_hourly`. The first run is the 30-day backfill; a missed run is repaired by the next. |
| Table | migration `033_add_taker_flow_hourly.sql` | One row per symbol (`BTCUSDT`, `ETHUSDT`) and complete UTC hour: `taker_buy_usd`, `taker_sell_usd`, `trade_count`. A missing hour is a missing row, never zeros. |
| Brief section | `taker_flow_brief_section.py`, appended by `MenthorqBriefDeliveryService` | A separate "Futures flow (not options)" section in the weekly and on-demand brief: ratio over the last 24 h, the last complete UTC day, the last 7 complete days, and the lowest and highest of those days, for BTC and ETH. |
| GUI tab | `coding/gui/tabs/taker_flow_tab.py` ("Futures Flow") | One Generate button writes a PNG per asset (daily ratio, daily net flow, rolling 24 h ratio) and a JSON of the daily numbers into `outputs/taker_flow/gui/<date>/`, with a link to the folder. It reads the local copy of the table, so sync first (`scripts/sync_from_vps.py`). |
| Sync | `scripts/sync_from_vps.py` | `taker_flow_hourly` is an optional table (skipped with a log line until migration 033 is on the VPS). |

Nothing is computed from missing data: a ratio whose hours are not all present is "n/a" with the
reason in the brief; a day with a missing hour is left out and named in the GUI warning.

## Data source and why there is a relay

Binance futures klines (`/fapi/v1/klines`, interval `1h`): the candle's quote volume (index 7) is
the total volume and index 10 the volume bought by takers; taker sell = total - taker buy. It is public
(no key). Only closed candles are stored.

**Binance blocks US IP addresses** (HTTP 451). The production VPS is in the USA (Hetzner, Ashburn),
so the collector reads through a read-only relay on the Spanish server (Vultr), set with
`BINANCE_FUTURES_BASE_URL`. The relay only forwards `/fapi/v1/klines` and
`/futures/data/takerlongshortRatio`, only for the Hetzner IP (nginx `allow`/`deny` plus a UFW rule),
on port 8082. The link between the servers is plain HTTP: the data is public, but it is not
authenticated in transit. Details and rollback: the server notes (`ServerNotes\projects.md`,
section binance-relay). Your own PC reaches Binance directly (leave the variable unset).

## Environment (.env)

| Variable | Default | Meaning |
|---|---|---|
| `TAKER_FLOW_ENABLED` | `0` | `1` = collect hourly and add the brief section. Only the production collector sets it. A local debug run of the collector writes nothing. |
| `BINANCE_FUTURES_BASE_URL` | `https://fapi.binance.com` | Source of the klines. On the VPS: `http://65.20.101.75:8082` (the relay). |

A bad value is refused (logged), never replaced by a default. A failure of this job never stops the
collector or the MenthorQ monitor.

## Verification against CryptoQuant (2026-11-13)

CryptoQuant's `taker-buy-sell-stats` (`exchange=binance`, daily; the plan allows daily only) was
compared with our daily ratios built from the hourly klines, 29 UTC days 2026-10-15 to 2026-11-12:

- mean absolute difference of the ratio: **0.0028**; largest 0.0127 (2026-10-27); the direction
  (above or below 1) is the same on all 29 days;
- our total volume is about 1% higher than CryptoQuant's on every day (cause not known: a
  different trade filter or cut-off on their side).

Binance USD-M `BTCUSDT` alone is the match; adding spot, COIN-M or `BTCUSDC` made it worse.
CryptoQuant daily would be the fallback source if the relay is lost (not built).
Hand check of the brief: BTC 7-day ratio from the daily volumes and ratios = 0.9672; the brief showed 0.97.

## Deployment (only when asked)

1. `scripts/deploy_to_vps.py` (code), then on the VPS: apply migration 033.
2. Add `TAKER_FLOW_ENABLED=1` and `BINANCE_FUTURES_BASE_URL=http://65.20.101.75:8082` to the VPS `.env`.
3. Restart the collector (owner, sudo). The first run backfills 30 days; check the log for
   `Taker flow BTCUSDT: stored 720 hour(s)`.
4. The next weekly brief (or `/brief`) has the section.

## Limits

- Binance USD-M only (not Bybit, OKX, spot or COIN-M); BTC and ETH only.
- History starts when the collector first runs (30-day backfill); older data is not stored.
- No alert rule on the ratio yet; it is information in the brief and the chart.
