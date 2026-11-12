# MenthorQ Monitor Tab

## Overview

Shows, on the PC, what the MenthorQ-style monitor in the collector would produce: the charts, the Telegram brief, and which urgent alerts would fire. It **never sends Telegram messages and never writes to the database** (the tab says so at the top). It reads the local database (SELECT only) and writes chart PNG/JSON files.

## Controls

**Currency**, **Mode** (MenthorQ daily close for a **Date (UTC)**, default the last complete UTC day, or Live), **Expiry universe**, **Gamma at**, and an optional **Compare with reference JSON**. **Run** does everything below in a background thread (about 5 seconds on the local database).

## What a Run does

1. **Charts**: `MenthorqReplicationService` (as in the Charts tab) writes the PNGs and the `*_replication.json` to a dated folder: `outputs/menthorq_replicate/gui/monitor/<YYYY-MM-DD>/` for a daily close, `outputs/menthorq_replicate/gui/monitor/live_<YYYYMMDDTHHMM>Z/` (run time, UTC) for Live.
2. **Brief**: `MenthorqBriefPreviewService` builds the brief with the same formatter Telegram gets (`format_brief`, HTML with `<b>`, `<i>`, `<pre>`). As in `scripts/menthorq_brief.py --dry-run`, the earlier records (previous run, 24 h, 7 d) and the previous date's daily close are recomputed from the snapshots at their own instants, not read from the monitor table. The brief instant is 23:59 UTC of the date (daily close) or now (Live). The brief record is always a live record (newest snapshot and its own open interest), as in the collector. The charts of a daily close use open interest from 00:00 UTC instead.
3. **Alert check**: every urgent-alert rule with the collector's thresholds (`AlertThresholds` defaults: HVL 3%, walls 5% of spot vs 24 h; HVL crossing and net-GEX sign change vs the previous run), before the 12 h cooldown.

## What it shows

- **Output folder**: a clickable link and an **Open folder** button.
- **Generated charts**: the PNG files of the Run. Click one to show it, scaled to the width of the pane. **Save PNG as...** copies it.
- **Brief**: the brief as rich text, its length (of Telegram's 4,096), and **Copy as plain text** (the text as Telegram displays it).
- **Alerts**: rule, enabled (yes only when `MENTHORQ_MONITOR_ENABLED` and `MENTHORQ_ALERTS_ENABLED` are both 1 in the local .env and the rule is on; with the defaults every row says no), threshold (shows whether the rule itself is on), current value, would fire (computed either way). Below it, the urgent message that would be sent.
- **Info**: brief data time, snapshot capture time and age, index price and its trade time, open-interest instant, IV source counts, universe, expiries, the daily-close column, and the charts' own inputs. Then the warnings and data caveats; an expiry listed after the open-interest snapshot is an expected gap and shows as a `Note: ...` bullet, not as a warning (see the MenthorQ Charts tab).
- **Compare with MenthorQ** (needs a reference JSON) and **Output** (the services' log).

Missing values show as "not computed", never 0.

## Architecture

```
MenthorqMonitorTab (coding/gui/tabs/menthorq_monitor_tab.py)
    -> MenthorqMonitorWorker (QThread)
        -> MenthorqReplicationService.run_menthorq_daily_close() / run_live()
        -> build_brief_preview_service(...).build_preview(as_of)
           (coding/service/on_chain/menthorq_monitor/brief_preview_service.py)
              -> MenthorqMonitorService.compute_* (never store), format_brief,
                 evaluate_urgent_alerts, format_urgent_alert
```

The tab has no calculation and no database access.

## Limitations

- The brief title says "Preview (references replayed, not stored)" instead of "Weekly brief" / "On-demand brief", and its references are replayed. The collector's brief compares with stored records, which can differ when a stored run is missing or was computed from an older sync.
- Cooldowns are not applied (the preview does not read the delivery log).

## Tests

`tests/unit/gui/test_menthorq_monitor_tab.py`, `tests/unit/on_chain/test_menthorq_brief_preview_service.py`.
