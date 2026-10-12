# System Health Tab (System Validation)

## Overview

Runs the LOCAL health checks and shows the VPS health that was synced down from the collector. The file name and class name still say "system validation"; the tab itself is titled "System Health".

## Controls

- **Run Validation**: runs every LOCAL check in a background thread (`SystemValidator.validate_all()` from `scripts/validate_system.py`).
- **Clear**: clears the results tree, the summary chips and the raw log.
- **Check VPS Health**: shows the contents of the local copy of `logs/vps_health.json`. That file is written by the hourly cron on the VPS and is copied down by "Sync from VPS" (Database tab), so sync first.

## Results

- A tree of check categories; each category shows `passed/total`, worst status first, and non-passing categories are expanded.
- A row of coloured chips, one per category.
- A summary line: all passed, N warnings, or N failed.
- A raw log underneath.

Statuses are PASS, WARN and FAIL (`CheckStatus` in `coding/core/health/models.py`).

## The checks

The checks are registered in `coding/service/health/registry.py`. Each one runs in the LOCAL environment, the VPS environment, or both:

| Category | Environment | What it checks |
|----------|-------------|----------------|
| API Connectivity | both | Deribit API reachable and returning valid data |
| Database - Local | both | Every table in `coding/service/health/table_registry.py`: newest row not too old, required columns filled |
| Database - VPS Internal Continuity | VPS | No missing collection cycles on the VPS |
| Database - VPS Sync | local | Local database compared with the last synced VPS row counts |
| Scanner Activity | both | The straddle scanner writes rows every cycle |
| Daemon Service | VPS | The `option-trading` systemd service is active |
| Telegram | both | Bot token valid (no message sent); alert delivery |
| Forward-Test Harnesses | both | On-chain predictions are recorded; straddle forward tests resolve settlements |
| IV-Percentile Window | local | The IV-percentile history window is fresh enough |
| Morning Note | local | The on-chain analysis and morning-note pipeline runs end to end |

The same registry runs on the VPS through `scripts/check_vps_health.py` (hourly cron), which also stores every run in the `health_check_runs` table.

## Command line

```bash
.venv\Scripts\python -m scripts.validate_system
```

Exit code 1 if any check failed, otherwise 0.

## Tests

`tests/unit/test_health_registry.py`, `test_health_table_registry.py`, `test_health_models.py` and the per-checker tests in `tests/unit/`.
