# MenthorQ Charts Tab

## Overview

Generates MenthorQ-style charts from the local database and shows them with their levels: IV*OI for all expiries, IV*OI for one expiry, the Net GEX four-panel chart and Net DEX for all expiries. It is the GUI for what `scripts/replicate_menthorq.py` does on the command line. It reads the database (SELECT only) and never calls Deribit.

## Controls

- **Currency**: BTC or ETH. The formulas were fitted on MenthorQ's BTC charts only. ETH uses them unchanged, and the tab shows a warning that they are not validated for ETH.
- **Mode**:
  - **MenthorQ daily close** for the **Date (UTC)**: spot and IV at 23:59 UTC, open interest from 00:00 UTC of that date. This is MenthorQ's convention as found on three dates, and the open-interest instant is a consistent choice, not a proven one. The date defaults to, and cannot go past, the last complete UTC day.
  - **Live (newest stored snapshot)**: the newest snapshot in the local database. Spot is the newest index price at or before that snapshot, and open interest comes from the same snapshot. Locally this is only as fresh as the last VPS sync.
- **Expiry universe**: Monthly, All listed, or Quarterly + front month. MenthorQ does not publish its expiry set, and no single set fits every date checked.
- **Gamma at**: Index or Forward.
- **Reference JSON** (optional): MenthorQ numbers read off its charts, in the format of `outputs/menthorq_replicate/menthorq_reference_*.json`.
- **Generate**: renders every chart in a background thread (a few seconds).
- **Chart**: which generated chart to show. "IV*OI one expiry" uses the **Expiry** list, which is filled after Generate. "All charts" shows every chart.
- **Save PNG as...**: copies the shown chart (only when a single chart is shown).

## What it shows

- The chart, scaled to the width of its pane. It scrolls, and it is never enlarged past its real size.
- An info line: mode and instant, the time the snapshot was captured, the index price and the time of the trade it comes from, the time of the open-interest snapshot, the IV source (`mark_iv`, or the number of rows whose IV was rebuilt from `mark_price` or is missing), the universe and the gamma underlying.
- Warnings, when they apply: IV rebuilt or missing, expiries whose GEX was not computed, rows without open interest, a currency other than BTC, and a reference JSON for a different date.
- **Levels**: Call Resistance, Put Support, HVL and spot for the shown chart. For the Net GEX panels there is one row per panel, with its expiry and GEX Expiring % (net GEX of the expiry divided by the sum of |net GEX| over the universe; approximate). A value that could not be computed shows as "not computed", never 0.
- **Compare with MenthorQ** (needs a reference): our drawn values next to MenthorQ's. Rows: spot, the all-expiry levels, and per panel the expiry, its levels and GEX Expiring %, with the difference (ours minus MenthorQ).
- **Output**: the replication service's log.

## What it saves

Each Generate writes to `outputs/menthorq_replicate/gui/` (gitignored). It writes the PNGs (the three all-expiry charts, an "_all_listed_expiries" variant when the universe is not "all", and one IV*OI chart per expiry) and a `*_replication.json` with every number. Live runs have `_live` in their file names.

## Architecture

```
MenthorqChartsTab (coding/gui/tabs/menthorq_charts_tab.py)
    -> MenthorqChartsWorker (QThread)
        -> MenthorqReplicationService.run_menthorq_daily_close() / run_live()
           (coding/service/on_chain/menthorq_replication_service.py)
    -> menthorq_chart_summary (coding/core/analytics/): levels rows, comparison rows,
       info line, warnings; pure functions over the service output
```

The tab does no calculation and no database access. It only formats the rows it gets.

## Tests

`tests/unit/gui/test_menthorq_charts_tab.py`, `tests/unit/analytics/test_menthorq_chart_summary.py`, `tests/unit/test_menthorq_replication_service.py`.
