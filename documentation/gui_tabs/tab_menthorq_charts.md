# MenthorQ Charts Tab

## Overview

One **Generate** button writes every MenthorQ-style chart of the last complete UTC day into a dated folder, and a link opens that folder in the file browser. It reads the database (SELECT only) and never calls Deribit.

The settings are fixed: BTC, MenthorQ daily close of the last complete UTC day (spot and IV at 23:59 UTC, open interest from 00:00 UTC), gamma at the index, and both the monthly and the all-listed expiry universes. Other modes (live, ETH, other dates, comparison with a MenthorQ reference JSON) remain available in `scripts/replicate_menthorq.py` and the MenthorQ Monitor tab.

## What it shows

- A status line: progress while generating (background thread, a few seconds), then "Generated N charts for <date> ...", or "Generation failed: <error class>: <message>".
- Orange warnings, when they apply: IV rebuilt from `mark_price` or missing, expiries whose GEX was not computed, rows without open interest.
- Grey notes for expected gaps: an expiry listed AFTER the open-interest snapshot (for example a new weekly; every one of its rows has no open interest in the snapshot from 00:00 UTC) gets one `Note: Expiry 15NOV26 is not in the open-interest snapshot (listed after it): ...` line instead of the two warnings above. Only SOME rows of an expiry without open interest stays an orange warning, and so does an open-interest snapshot in which NO expiry has open interest (a real fault, not a new listing). The service logs the same case once per run at INFO, not at WARNING.
- **Folder**: a clickable link to the generated folder, and an **Open folder** button.

## What it saves

`outputs/menthorq_replicate/gui/charts/<YYYY-MM-DD>/` (gitignored), the service's "nested" layout:

```
01_iv_times_open_interest/             IV*OI over all expiries (monthly and _all_listed_expiries)
02_iv_times_open_interest_per_expiry/  one IV*OI chart per listed expiry
03_net_gex_panels/                     Net GEX four panels (monthly and _all_listed_expiries)
04_net_dex/                            Net DEX (monthly and _all_listed_expiries)
replication.json                       every number; chart paths relative to this folder
README.txt                             the folders and the run settings
```

Generating the same date again overwrites the files of the same names and deletes nothing. The Monitor tab writes its flat files into the same dated folder.

## Architecture

```
MenthorqChartsTab (coding/gui/tabs/menthorq_charts_tab.py)
    -> MenthorqChartsWorker (QThread)
        -> MenthorqReplicationService.run_menthorq_daily_close(..., chart_layout="nested")
           (coding/service/on_chain/menthorq_replication_service.py)
    -> menthorq_chart_summary.data_warnings and data_notes (coding/core/analytics/)
```

The tab does no calculation and no database access.

## Tests

`tests/unit/gui/test_menthorq_charts_tab.py`, `tests/unit/test_menthorq_replication_service.py`.
