# On Chain Analysis Tab

## Overview

Runs the full on-chain analysis for BTC and/or ETH from live Deribit data, shows the result, and saves the report and the morning-note synthesis to disk. The analysis includes GEX/DEX, volatility surface, buy/sell flow, dealer inventory, exposure profiles and market-wide metrics; none of it is optional.

## Controls

- **Currency**: BTC and ETH checkboxes. If both are ticked the analyses run one after the other.
- **Load Analysis**: runs the analysis (in a background thread) for the ticked currencies.
- **Clear**: clears the results.
- **View Flow Charts**: opens the Flow Charts window (`features/buy_sell_flow_charts.md`).
- **Levels expiry** selector and **Sign convention** radio buttons (Holder, Assumed dealer, Inferred) for the Levels table. "Assumed dealer" is the default; "Inferred" is enabled only when the loaded expiry has an inferred view.

## What it shows

- **Normalized Metrics**: for each metric its value, the 30-day and 90-day percentile (p30, p90), the z-scores (z30, z90) and a regime label, from `HistoricalNormalizer`.
- **Levels** tab: per-strike table with GEX colouring according to the chosen sign convention, with markers for call wall, put support, HVL and max pain.
- **Report** tab: the full text report (all expirations, then the market-wide sections).
- An output log with progress messages.

## What it saves

Each run writes, through `OnChainWorkflowService`:

- `output/data/onchain_analysis/{currency}/report/{YYYYMMDD_HHMMSS}/report.txt` and `synthesis.txt` (the morning note, see `features/morning_note_synthesis.md`);
- one text report per expiration under `output/data/onchain_analysis/{currency}/{expiration}/`.

## Architecture

```
OnChainAnalysisTab (coding/gui/tabs/on_chain_analysis_tab.py)
    -> OnChainWorkflowService.run()        (coding/service/on_chain/on_chain_workflow_service.py)
        -> OnChainAnalysisService.fetch_and_analyze()   (live book summary, Greeks, trades from the database)
        -> MorningNoteService.generate() and save_report_bundle()
```

The tab holds no analysis logic; it renders the typed `OnChainAnalysisResult` it receives. The analysis uses live Deribit data plus the local database (trades, history for the percentiles), so the local database should be synced first.

## Tests

`tests/unit/gui/test_on_chain_analysis_tab.py`, `tests/unit/on_chain/test_on_chain_workflow_service.py`.
