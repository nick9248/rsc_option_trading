# Report (one tab, five sections)

The Report tab (module 6) replaced five tabs on 2026-11-14: MenthorQ Charts, Futures Flow, Option Flow, Dealer Levels and Levels. One button builds one HTML file per currency with a tab for each of them.

## What it is
- One file per run, written to `outputs/report/<currency>_market_report_<UTC time>Z.html` (git-ignored; old files are not deleted).
- Self-contained: no network. plotly.js is inlined once (about 4.8 MB), PNG charts are base64 images, the Levels page sits in an iframe (`srcdoc`). Measured on 2026-11-14: 7.8 MB for BTC, 7.7 MB for ETH, about 25 s to build.
- Every number comes from the existing services; the report computes nothing a second way.

## How to use
1. Open the Report tab, pick the currency, press Generate report.
2. The status line shows each step, then how many sections were built, the file size, the sections not built and what the sync step did.
3. The report opens in the tab. Open in browser opens it in the default browser; the File line links to the folder.

A Generate pressed during a run waits and runs next. On exit the run is asked to stop (between sync tables or sections) and the application waits for the current step.

## The sync rule
1. For historical_trades, snapshots, taker_flow_hourly and liquidation_heatmap_snapshots, MAX(watermark column) (the column the sync uses) is read on the VPS (read-only, through the SSH tunnel) and locally.
2. If the VPS is ahead on any of them, or one cannot be compared, the normal incremental sync of all tables runs through the same tunnel.
3. An optional table missing on the VPS does not trigger a sync.
4. If the check fails (no SSH), the report is built from the local data; the header's Database line says so.

Only one sync runs at a time in the application (a second one is refused), because two syncs would insert the same snapshots rows twice (the `snapshots` table has no unique key).

Limits: a row updated in place on the VPS is not seen by the check; only these four tables are compared; a table that can never be compared makes every Generate sync; in practice the VPS is almost always ahead, so the check usually decides "sync".

## What each tab shows
| Tab | What it shows | As of | Limits |
|---|---|---|---|
| MenthorQ | Replica charts (IV x OI, net GEX panels, net DEX; monthly or all listed expiries) and IV x OI per expiry with levels | Daily close of the last complete UTC day (spot and IV 23:59 UTC, OI from 00:00 UTC) | Dark MenthorQ-style PNGs; formulas fitted on BTC only |
| Futures flow | Binance perpetual of the report currency: 30-day chart and the last 7 complete days | Newest stored hour | Futures, not options; stale after 3 h; a day with a missing hour is not drawn |
| Option flow | Buy/sell per strike: all expirations plus up to 12 single expirations (most traded by notional), in notional, contracts or trades | Last 24 h up to the report time | Window fixed; other windows: scripts/option_flow_by_strike.py |
| Dealer levels | Flow-based vs OI-based dealer GEX, level table, plausibility check; 5 nearest expiries plus the nearest monthly (shown first) | Chain snapshot; trades up to the report time | No ground truth; other expiries: scripts/dealer_flow_levels.py |
| Levels | Every source's levels on one price line with the ledger | Nearest monthly, 24 h flow, newest liquidation snapshot | Band 1 %, 3 sources fixed; other settings: scripts/levels_overview.py |

## When a section fails
The tab says "This section could not be built", what failed and how to fix it; the tab label shows "not built"; the status line names it; the other sections are built. Missing values read "not computed", never 0.

## Look
Printed-sheet style like the Levels page: white paper, ink text, Bahnschrift numerals, one highlighter accent. Plotly figures are recoloured only. Keyboard: Left/Right/Home/End move between tabs; `#option-flow` (or menthorq, futures-flow, dealer-levels, levels) opens a tab.

## Code map
Core `coding/core/analytics/report/`; service `coding/service/report/`; GUI `coding/gui/tabs/report_tab.py`; sync `coding/service/database/vps_sync_service.py`; tests `tests/unit/report/`, `tests/unit/gui/test_report_tab.py`, `tests/unit/test_vps_sync_service.py`.
