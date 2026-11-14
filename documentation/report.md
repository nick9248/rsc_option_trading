# Report (one tab, five sections)

The Report tab (module 6) replaced five tabs on 2026-11-14: MenthorQ Charts, Futures Flow, Option Flow, Dealer Levels and Levels. One button builds one HTML file per currency with a tab for each of them.

## What it is
- One file per run, written to `outputs/report/<currency>_market_report_<UTC time>Z.html` (git-ignored; old files are not deleted).
- Self-contained: no network. plotly.js is inlined once (about 4.8 MB), the MenthorQ charts are base64 PNG images, the Plotly charts are embedded as data, and each Levels page sits in an iframe (`srcdoc`, one per dealer expiry). Size: about 8 MB for BTC (7.8 to 7.9 MB on 2026-11-14, latest builds), 7.7 MB for ETH. Build time was about 25 s before the expiry views were added; not measured again.
- Open interest (OI) is the number of open option contracts. The report shows it per expiry, in coins and as notional (OI x index price).
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

Only one sync runs at a time in the application (a second one is refused). The `snapshots` table has a unique key (migration 035, one row per instrument per capture), so a repeated row is skipped; the lock still stops two syncs from doing the same work twice.

Limits: a row updated in place on the VPS is not seen by the check; only these four tables are compared; a table that can never be compared makes every Generate sync; in practice the VPS is almost always ahead, so the check usually decides "sync".

## What each tab shows
| Tab | What it shows | As of | Limits |
|---|---|---|---|
| MenthorQ | Replica charts (IV x OI, net GEX panels, net DEX; monthly or all listed expiries). An open interest table per expiry: days left to settlement, calls, puts, total OI in coins, notional and share. One IV x OI chart per expiry with its levels; the expiry choices are ordered by OI. | Daily close of the last complete UTC day (spot and IV 23:59 UTC, OI from 00:00 UTC) | Dark MenthorQ-style PNGs; formulas fitted on BTC only. If all rows of one side (calls or puts) lack OI, the total is the other side alone; the table does not flag this. |
| Futures flow | Binance perpetual of the report currency. One interactive chart of the rolling 24 h taker buy/sell ratio over the stored 30 days, with 1d, 3d, 7d and all buttons and a range slider. It shows a trailing 7-day mean and a band of mean plus or minus one standard deviation (over all 30 days in the chart, not the zoomed range). Hover shows the hour's ratio and that UTC day's ratio and net flow. A table of the last 7 complete days sits below. | Newest stored hour | Futures, not options. Stale after 3 h. A day with a missing hour has no daily numbers, and the line breaks at a missing hour. No trend lines: the page says why. |
| Option flow | Buy/sell per strike in notional, contracts or trades. All expirations, plus single expirations: the traded ones among the 4 with the most OI always, up to 12 in all, the rest by traded notional. The expiry switcher is ordered by OI, with labels such as "25DEC26, 41 days, 9.65B USD OI, 40%". Also: an OI table (top 8 expiries); "Which expiry carries the largest bars" (the 8 biggest bars of the all-expirations chart, the expiries that make them up with share and days left, blocked share, and buttons that jump to that expiry's view); "Largest blocks of the window" (top 10 blocks by notional: legs in the window and declared, expiries, bought and sold, calls and puts, notional, biggest legs, and a plain label). | Last 24 h up to the report time | Window fixed; other windows: scripts/option_flow_by_strike.py. A bar that the per-expiry flows do not fully explain is marked "not explained". A block whose legs fall partly outside the window shows only the legs in the window. The label describes the block; one leg of a multi-leg block is not a view on its own. |
| Dealer levels | Flow-based vs OI-based dealer GEX, level table, plausibility check; 5 nearest expiries plus the nearest monthly (shown first) | Chain snapshot; trades up to the report time | No ground truth; other expiries: scripts/dealer_flow_levels.py |
| Levels | Every source's levels on one price line with the ledger. An expiry selector: the dealer lanes are computed for the chosen expiry. Pages are precomputed for the 4 expiries with the most OI plus the nearest monthly (5 at most); the default is the nearest monthly. The other lanes (MenthorQ replica, option flow, liquidation) do not depend on the expiry. An OI table (top 8 expiries) sits above the page. | 24 h option flow; newest liquidation snapshot | Band 1 %, 3 sources fixed. Other expiries: scripts/levels_overview.py --expiry. |

## How to read this

Each tab starts with a closed "How to read this tab" panel, and the report has one at the top, "How to read this report". A panel gives a summary, then for each chart or table: what you see, the terms, cases with a worked example, and what it cannot tell. The text is in `coding/core/analytics/report/how_to_read.py` and is drawn by `help_fragment.py`. The examples are recomputed by `tests/unit/report/test_how_to_read.py`.

## When a section fails
The tab says "This section could not be built", what failed and how to fix it; the tab label shows "not built"; the status line names it; the other sections are built. Missing values read "not computed", never 0.

## Look
Printed-sheet style like the Levels page: white paper, ink text, Bahnschrift numerals, one highlighter accent. Plotly figures are recoloured only. Keyboard: Left/Right/Home/End move between tabs; `#option-flow` (or menthorq, futures-flow, dealer-levels, levels) opens a tab.

## Code map
Core `coding/core/analytics/report/` (`section_fragments.py` for the tables, `how_to_read.py` and `help_fragment.py` for the help panels); open interest per expiry `coding/core/analytics/open_interest/expiry_open_interest.py` and `coding/service/on_chain/expiry_open_interest_service.py`; futures chart `coding/core/analytics/futures_flow/taker_flow_figure.py`; option flow blocks and bars `coding/core/analytics/option_flow/block_structure.py`; blocks query `get_block_trade_legs_between` in `coding/core/database/repository.py`; service `coding/service/report/` (`report_sections.py` builds each section); GUI `coding/gui/tabs/report_tab.py`; sync `coding/service/database/vps_sync_service.py`; tests `tests/unit/report/`, `tests/unit/gui/test_report_tab.py`, `tests/unit/test_vps_sync_service.py`.
