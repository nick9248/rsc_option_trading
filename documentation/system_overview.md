# System Overview

Automated crypto options trading for BTC and ETH on Deribit. The current focus is collecting data and validating the on-chain analysis. Nothing in this repo places real orders.

This document was rewritten on 2026-10-12 after the table cleanup (migrations 024 to 030); every statement was checked against the code at that date.

## Architecture

```
Core (models, calculators)  ->  Service (orchestration)  ->  GUI / CLI
```

| Layer | Folder | Contains |
|-------|--------|----------|
| Core | `coding/core/` | `analytics/` (calculators, synthesis, charts), `api/`, `database/` (repository), `health/` (result models), `schemas/`, `config.py` |
| Service | `coding/service/` | `data_collection/`, `on_chain/`, `scanner/`, `health/`, `deribit/`, `morning_note/`, `snapshot/`, `analytics/`, `api_testing/`, `database/` (VPS sync) |
| GUI | `coding/gui/` | PySide6 app (`app.py`, `main_window.py`, `tabs/`, `dialogs/`). No business logic: it calls services. |

Other folders: `scripts/` (backfills, sync, health check), `migrations/` (numbered, forward-only SQL), `tests/`, `strategies/` (sample strategies running on the VPS, left alone).

## Data collection

The collector runs on the Hetzner VPS as the systemd service `option-trading` (see `vps/vps_setup_and_operations.md`). The entry point is `coding/service/data_collection/unified_scheduler.py` (`python -m coding.service.data_collection.unified_scheduler`, verified in the unit file on the server): `UnifiedScheduler` runs `ProspectiveCollector.collect_hour()` every 30 minutes (hourly snapshots, on-chain analysis, scanners, DVOL, futures basis, account data) and `TradeCollector` every 60 seconds (trades for the flow data), both with APScheduler.

For each currency (BTC, ETH) one cycle does, in this order:

1. recent trades into `historical_trades`
2. book summary into `snapshots`
3. on-chain analysis into `onchain_analysis_snapshots` (also `volatility_skew_history`, and the forward-test harness records its predictions into `forward_test_predictions`)
4. DVOL (last 48 hourly candles) into `dvol_history`
5. dated-futures basis into `futures_basis`
6. the owner's Deribit account (read-only, `AccountCollector`) into `account_snapshots`, `position_snapshots`, `user_trades`, `orders`, `settlements`; skipped when no credentials are set
7. funding rate into `funding_rate_history`
8. latest daily candle into `ohlcv_history`
9. delta-weighted taker flow into `flow_delta_hourly`
10. the daily open-interest anchor into `daily_oi_snapshots` (only in the 08:00 UTC hour)

After the currencies: hourly aggregation into `hourly_snapshots`, volatility reconstruction into `onchain_volatility_snapshots`, and the scanners (straddle, then iron condor and butterfly) into `straddle_scan_history` and `defined_risk_scan_history`, with Telegram alerts.

`collection_daemon.py` (`CollectionDaemon`) is an older, simpler runner for a PC: it runs `ProspectiveCollector` only, every 30 minutes, and backfills a start-up gap of up to 1.5 hours. The server does not use it.

Buy/sell flow is not stored per capture. It is computed on demand from `historical_trades` with `BuySellFlowAnalyzer` (see `features/buy_sell_flow_charts.md`).

## Live tables

21 tables, created by `migrations/000` to `030` (tables dropped along the way: see migrations `026` to `028`):

| Group | Tables |
|-------|--------|
| Market data | `historical_trades`, `snapshots`, `hourly_snapshots`, `funding_rate_history`, `ohlcv_history`, `dvol_history`, `futures_basis`, `daily_oi_snapshots` |
| On-chain analysis | `onchain_analysis_snapshots`, `onchain_volatility_snapshots`, `volatility_skew_history`, `flow_delta_hourly` |
| Scanners and forward tests | `straddle_scan_history`, `defined_risk_scan_history`, `forward_test_predictions` |
| Owner's Deribit account | `account_snapshots`, `position_snapshots`, `user_trades`, `orders`, `settlements` |
| Operations | `health_check_runs` (stored on the VPS only) |

`scripts/sync_from_vps.py` (`SYNC_TABLES`, 20 tables) copies everything except `health_check_runs` into the local database. `coding/service/health/table_registry.py` lists the same 20 tables with their freshness limits; a test fails when a synced table is missing there.

## Health checks

One registry (`coding/service/health/registry.py`) holds the checks; each check is marked LOCAL, VPS or BOTH:

- API connectivity, local database freshness (per table, from `table_registry.py`), VPS internal continuity (VPS), sync gap between VPS and local (LOCAL), scanner activity, daemon service (VPS), Telegram configuration and delivery, forward-test harnesses (on-chain and straddle), IV-percentile window (LOCAL), morning-note smoke test (LOCAL).
- `python -m scripts.validate_system` runs the LOCAL checks; the GUI "System Health" tab shows them too.
- `scripts/check_vps_health.py` runs the VPS checks every hour (cron) and writes `logs/vps_health.json` and a row in `health_check_runs` (rows older than 90 days are pruned).

## Analysis

`OnChainAnalysisService.fetch_and_analyze()` (`coding/service/on_chain/`) fetches the live book and builds a typed `OnChainAnalysisResult` (GEX/DEX, volatility surface, flow, dealer inventory, exposure profile, market-wide metrics). `MorningNoteService` turns it into the executive summary with `SynthesisMapper` and `SynthesisEngine`. The scoring rules are in `synthesis_logic.md`, the GEX/DEX formulas in `gex_dex_methodology.md`.

## Setup and commands

Run from the repo root (Windows):

```
.venv\Scripts\python -m pytest                               # tests
.venv\Scripts\python -m scripts.validate_system              # local health check
.venv\Scripts\python -m coding.gui.app                       # GUI
.venv\Scripts\python -m scripts.sync_from_vps                # copy VPS data into the local database
python scripts/run_migration.py migrations/NNN_name.sql      # apply one migration
```

The database is PostgreSQL 18 on `localhost:5433` (name `option_trading`); the password is `DB_PASSWORD` in `.env`. Logs go to `output/log/`.

Rules that matter when changing code (full list in the project `CLAUDE.md`): missing data is `None`, never `0.0`; datetimes are UTC-aware; backtests use point-in-time data only; tests that write to a database use a throw-away database.
