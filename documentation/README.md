# Documentation

The code is the source of truth. If a document here disagrees with the code, the document is wrong: fix it or tell the owner.

Other specifications live outside this folder: `synthesis_logic.md` (morning-note scoring, repo root) and `migrations/` (the schema, as numbered SQL files).

## System

- `system_overview.md`: architecture, data flow, live tables, health checks, how to run things.
- `vps/vps_setup_and_operations.md`: the Hetzner VPS, the systemd daemon, syncing, daily operations.

## Methodology

- `gex_dex_methodology.md`: GEX/DEX formulas, sign conventions and key levels.
- `gex_dex_validation_results.md`: dated validation of the GEX/DEX formula (2026-02-16), kept as a record.

## Features

- `features/buy_sell_flow_charts.md`: the Flow Charts window (flow computed on demand from stored trades).
- `features/morning_note_synthesis.md`: how the morning note is built from the on-chain analysis.

## GUI tabs

One file per tab that exists in `coding/gui/tabs/`:

- `gui_tabs/tab_api_connection.md`
- `gui_tabs/tab_snapshot.md`
- `gui_tabs/tab_on_chain_analysis.md`
- `gui_tabs/tab_database.md`
- `gui_tabs/tab_system_validation.md`

The Automation tab (`automation_tab.py`) has no document yet.

## Obsolete documents (proposed for deletion, awaiting the owner's decision)

These describe code that has been removed (strategy system, regime detection, OTM contract finder, ML pipeline, Windows Task Scheduler setup). Do not rely on them:

- `features/feature_strategy_system.md`
- `features/feature_market_regime_detection.md`
- `features/feature_otm_contract_finder.md`
- `strategy_scoring_calculation_example.md`
- `gui_tabs/tab_strategies.md`
- `gui_tabs/tab_market_regime.md`
- `ml/ml_data_collection_system.md`
- `ml/ml_for_decision_making_complete.md` (the owner may want to keep its ML model plan)

## Keeping documentation current

- When code changes a table, command, class name or path that a document mentions, update the document in the same pull request.
- Put new documents in a subfolder by type (`features/`, `gui_tabs/`, `vps/`) and list them here.
