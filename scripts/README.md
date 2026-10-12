# Scripts

Run every script from the repo root, for example `.venv\Scripts\python -m scripts.validate_system`. Each script's docstring has its full usage and options.

## Daily operations

| Script | What it does |
|--------|--------------|
| `sync_from_vps.py` | Copies the VPS data into the local database through an SSH tunnel (the tables in `SYNC_TABLES`) and pulls `logs/vps_health.json`. The GUI "Sync from VPS" button runs it. |
| `validate_system.py` | Runs every LOCAL health check (shared registry in `coding/service/health/`) and prints a grouped summary. |
| `check_vps_health.py` | Runs the VPS health checks. Runs on the VPS (hourly cron). Writes `logs/vps_health.json` and stores each run in the `health_check_runs` table. |

## Database

| Script | What it does |
|--------|--------------|
| `run_migration.py` | Applies one numbered SQL file from `migrations/`. Migrations are forward-only. |

## Backfills

| Script | What it does |
|--------|--------------|
| `backfill_historical_trades.py` | Downloads historical option trades from Deribit into `historical_trades`. |
| `aggregate_hourly_snapshots.py` | Aggregates stored trades into `hourly_snapshots`. |
| `run_full_backfill.py` | Runs the trade download, Greeks calculation and hourly aggregation as one pipeline. |
| `backfill_dvol_history.py` | Refreshes the whole DVOL history into `dvol_history` (final close of every completed hourly candle). |
| `backfill_funding_rate.py` | Fills `funding_rate_history`. |
| `backfill_ohlcv.py` | Fills `ohlcv_history` with daily candles. |
| `backfill_gex_dex_history.py` | Recomputes GEX/DEX columns in `onchain_analysis_snapshots` for rows written before the collection bug fix. |
| `backfill_volatility_reconstruction.py` | Rebuilds `onchain_volatility_snapshots` from stored data. |
| `backfill_external_metrics.py` | Obsolete: writes to `external_metrics`, which migration 024 dropped. It fails if run. |

## Research and maintenance

| Script | What it does |
|--------|--------------|
| `phase2_backtest.py` | Phase 2 metric backtest (FDR-corrected, walk-forward). Its results are `phase2_blueprint.txt` and `phase2_single_metric_results.csv`. |
| `backtest_straddle_metrics.py` | Backtest of straddle deal-quality metrics. |
| `record_onchain_fixture.py` | Records live API and DB responses as the fixture for the on-chain golden-master test. Not a routine script. |
| `cleanup_expired_charts.py` | Removes chart folders in `output/charts/` for expirations Deribit no longer lists. |
