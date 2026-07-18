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
    """Row counts for the tables the local sync-gap check compares against."""
    snapshot = {}
    conn = repo._get_connection()
    try:
        cursor = conn.cursor()
        try:
            for table in _SYNC_TRACKED_TABLES:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                snapshot[table] = {"rows": cursor.fetchone()[0]}
        finally:
            cursor.close()
    finally:
        repo._return_connection(conn)
    return snapshot


def run(log_dir: Optional[Path] = None) -> int:
    """Run every VPS-environment health check and write logs/vps_health.json."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*60}")
    print(f"VPS HEALTH CHECK — {now}")
    print(f"{'='*60}")

    repo = DatabaseRepository()
    grouped = run_checks(CheckEnvironment.VPS, repo)
    tables = _table_snapshot(repo)

    all_results = [r for results in grouped.values() for r in results]
    passed = sum(1 for r in all_results if r.status == CheckStatus.PASS)
    problems = [r.message for r in all_results if r.status != CheckStatus.PASS]

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

    log_dir = log_dir or (Path(__file__).parents[1] / "logs")
    log_dir.mkdir(exist_ok=True, parents=True)
    (log_dir / "vps_health.json").write_text(json.dumps(data, indent=2, default=str))

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
