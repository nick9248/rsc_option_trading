"""
VPS Health Check — thin wrapper over the shared health-check registry
(VPS environment). Runs on the VPS via cron (hourly). Writes
logs/vps_health.json, including a "tables" section (row counts) that the
local DatabaseSyncGapCheck reads once this file is synced down.

Usage:
    python -m scripts.check_vps_health
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from coding.core.database.repository import DatabaseRepository
from coding.core.health.models import CheckEnvironment, CheckStatus
from coding.core.logging.logging_setup import init_logging
from coding.service.health.registry import run_checks

init_logging(level="WARNING")

# Tables surfaced in the "tables" section for the local sync-gap check.
_SYNC_TRACKED_TABLES = [
    "historical_trades", "hourly_snapshots", "onchain_analysis_snapshots",
    "onchain_volatility_snapshots", "straddle_scan_history",
    "forward_test_predictions", "dvol_history", "ohlcv_history",
]


def _table_snapshot(repo: DatabaseRepository) -> dict:
    """
    Row counts for the tables the local sync-gap check compares against.
    Each table is isolated: a failure counting one table doesn't discard
    the row counts already collected for the others -- the connection is
    rolled back so the transaction can continue for the next table.
    """
    snapshot = {}
    conn = repo._get_connection()
    try:
        cursor = conn.cursor()
        try:
            for table in _SYNC_TRACKED_TABLES:
                try:
                    cursor.execute(f"SELECT COUNT(*) FROM {table}")
                    snapshot[table] = {"rows": cursor.fetchone()[0]}
                except Exception as exc:
                    conn.rollback()
                    snapshot[table] = {"rows": None, "error": str(exc)}
        finally:
            cursor.close()
    finally:
        repo._return_connection(conn)
    return snapshot


def _write_health_json(log_dir: Path, data: dict) -> None:
    log_dir.mkdir(exist_ok=True, parents=True)
    (log_dir / "vps_health.json").write_text(json.dumps(data, indent=2, default=str))


def run(log_dir: Optional[Path] = None) -> int:
    """Run every VPS-environment health check and write logs/vps_health.json."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*60}")
    print(f"VPS HEALTH CHECK — {now}")
    print(f"{'='*60}")

    log_dir = log_dir or (Path(__file__).parents[1] / "logs")

    try:
        repo = DatabaseRepository()
        grouped = run_checks(CheckEnvironment.VPS, repo)
    except Exception as exc:
        print(f"  [ERR ] Failed to run health checks: {exc}")
        _write_health_json(log_dir, {
            "timestamp": now,
            "total": 0,
            "passed": 0,
            "results": [],
            "problems": [f"Health check run failed entirely: {exc}"],
            "tables": {},
        })
        print(f"\nRESULT: could not run health checks — {exc}")
        print(f"{'='*60}\n")
        return 1

    try:
        tables = _table_snapshot(repo)
        table_snapshot_error = None
    except Exception as exc:
        print(f"  [ERR ] Failed to collect table row counts: {exc}")
        tables = {}
        table_snapshot_error = str(exc)

    all_results = [r for results in grouped.values() for r in results]
    passed = sum(1 for r in all_results if r.status == CheckStatus.PASS)
    problems = [r.message for r in all_results if r.status != CheckStatus.PASS]
    if table_snapshot_error is not None:
        problems.append(f"Table row-count snapshot failed: {table_snapshot_error}")

    data = {
        "timestamp": now,
        "total": len(all_results),
        "passed": passed,
        "results": [
            {"ok": r.status == CheckStatus.PASS, "label": r.name, "message": r.message}
            for r in all_results
        ],
        "problems": problems,
        "tables": tables,
    }
    _write_health_json(log_dir, data)

    print()
    for r in all_results:
        icon = "OK  " if r.status == CheckStatus.PASS else ("WARN" if r.status == CheckStatus.WARN else "ERR ")
        print(f"  [{icon}] {r.message}")
    print(f"\nRESULT: {passed}/{len(all_results)} checks OK")
    if problems:
        print(f"\nPROBLEMS ({len(problems)}):")
        for msg in problems:
            print(f"  - {msg}")
    else:
        print("All checks passed.")
    print(f"{'='*60}\n")

    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(run())
