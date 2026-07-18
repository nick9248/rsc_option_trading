"""VPS-side internal continuity gap check (missing collection cycles)."""

from datetime import datetime, timedelta
from typing import List, Tuple

from coding.core.health.models import CheckEnvironment, CheckResult, CheckStatus
from coding.service.health.base import HealthCheck

_WINDOW_HOURS = 48
_EXPECTED_HOURS = 48

# (table, timestamp_column) -- checked per currency, one row per hour expected
_CADENCE_TABLES: List[Tuple[str, str]] = [
    ("onchain_analysis_snapshots", "snapshot_hour"),
    ("hourly_snapshots", "snapshot_hour"),
    ("straddle_scan_history", "scan_time"),
]

_WARN_COVERAGE_PCT = 90.0
_FAIL_COVERAGE_PCT = 60.0


class DatabaseVpsGapCheck(HealthCheck):
    """
    Checks the VPS database's own tables for missing collection cycles
    over a rolling window -- answers "is the daemon actually producing
    complete data," independent of local sync status.
    """

    category = "Database — VPS Internal Continuity"
    environment = CheckEnvironment.VPS

    def run(self, repo) -> List[CheckResult]:
        results: List[CheckResult] = []
        window_start = datetime.now() - timedelta(hours=_WINDOW_HOURS)

        conn = repo._get_connection()
        try:
            cursor = conn.cursor()
            try:
                for table, ts_col in _CADENCE_TABLES:
                    results.extend(self._gap_results(cursor, table, ts_col, window_start))
            finally:
                cursor.close()
        finally:
            repo._return_connection(conn)
        return results

    def _gap_results(self, cursor, table: str, ts_col: str, window_start) -> List[CheckResult]:
        cursor.execute(f"""
            SELECT currency, COUNT(DISTINCT {ts_col})
            FROM {table}
            WHERE {ts_col} >= %s
            GROUP BY currency
            ORDER BY currency
        """, (window_start,))
        rows = cursor.fetchall()

        if not rows:
            return [CheckResult(
                name=f"{table} continuity", status=CheckStatus.FAIL,
                message=f"{table}: no rows in the last {_WINDOW_HOURS}h",
            )]

        results = []
        for currency, hours in rows:
            coverage_pct = (hours / _EXPECTED_HOURS) * 100.0
            if coverage_pct >= _WARN_COVERAGE_PCT:
                status = CheckStatus.PASS
            elif coverage_pct >= _FAIL_COVERAGE_PCT:
                status = CheckStatus.WARN
            else:
                status = CheckStatus.FAIL
            results.append(CheckResult(
                name=f"{table} continuity ({currency})", status=status,
                message=f"{table} {currency}: {hours}/{_EXPECTED_HOURS}h ({coverage_pct:.0f}%)",
                details={"coverage_pct": coverage_pct, "currency": currency},
            ))
        return results
