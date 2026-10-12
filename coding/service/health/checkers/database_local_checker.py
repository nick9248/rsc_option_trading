"""Local database freshness and completeness health check."""

from datetime import date, datetime, time, timezone
from typing import List, Optional

from coding.core.health.models import CheckEnvironment, CheckResult, CheckStatus
from coding.service.health.base import HealthCheck
from coding.service.health.table_registry import TABLE_SPECS

_COMPLETENESS_SAMPLE_SIZE = 500
_COMPLETENESS_WARN_PCT = 95.0


class DatabaseLocalFreshnessCheck(HealthCheck):
    """
    Checks wall-clock freshness and required-column completeness of the
    local database's pipeline tables. Required columns per table are
    derived from actual consumer code -- see the design spec's
    "Data Completeness" table for the trace.
    """

    category = "Database — Local"
    # Task E3: was CheckEnvironment.LOCAL, which meant this check (and its
    # dvol_history staleness WARN/FAIL, see TABLE_SPECS) only ever
    # ran when a human manually ran scripts/validate_system.py. The only
    # automated/cron-scheduled health run is scripts/check_vps_health.py on
    # the VPS, which only runs CheckEnvironment.VPS-tagged checks -- it
    # never saw this one. BOTH makes registry.run_checks' existing
    # `checker.environment == CheckEnvironment.BOTH` passthrough run this
    # check under every entry point, so it monitors the VPS's own database
    # (where the daemon actually writes) via the VPS cron, not just
    # whatever local DB a human happens to query by hand.
    environment = CheckEnvironment.BOTH

    def run(self, repo) -> List[CheckResult]:
        results: List[CheckResult] = []
        conn = repo._get_connection()
        try:
            cursor = conn.cursor()
            try:
                for spec in TABLE_SPECS:
                    results.extend(self._safe_table_checks(
                        conn, cursor, spec.table, spec.time_column, spec.time_kind,
                        spec.warn_hours, spec.fail_hours, list(spec.required_columns), spec.optional,
                    ))
            finally:
                cursor.close()
        finally:
            repo._return_connection(conn)
        return results

    def _safe_table_checks(
        self, conn, cursor, table: str, ts_col: str, ts_kind: str,
        warn_h: Optional[float], fail_h: Optional[float], required: List[str], optional: bool = False,
    ) -> List[CheckResult]:
        """
        One table's freshness+completeness checks, isolated so a schema
        problem on this table (a renamed/dropped column, for instance)
        doesn't lose visibility into every other table sharing this
        connection. Rolls back on failure so the connection's transaction
        can continue cleanly for the next table.
        """
        try:
            table_results = [self._freshness_result(cursor, table, ts_col, ts_kind, warn_h, fail_h, optional)]
            if required:
                table_results.append(self._completeness_result(cursor, table, ts_col, required))
            return table_results
        except Exception as exc:
            conn.rollback()
            return [CheckResult(
                name=f"{table} check", status=CheckStatus.FAIL,
                message=f"{table}: check failed: {exc}",
            )]

    def _freshness_result(
        self, cursor, table: str, ts_col: str, ts_kind: str,
        warn_hours: Optional[float], fail_hours: Optional[float], optional: bool = False,
    ) -> CheckResult:
        """
        Age of the newest row against the table's limits. Times are compared in UTC:
        the database stores UTC (naive columns are UTC-valued), whatever this host's clock.
        Event tables (no limits) only report their row count (none yet is fine); an empty or
        stale ``optional`` hourly table is a warning, a required one a failure.
        """
        cursor.execute(f"SELECT MAX({ts_col}), COUNT(*) FROM {table}")
        latest, row_count = cursor.fetchone()

        if latest is None and warn_hours is None:
            return CheckResult(
                name=f"{table} freshness", status=CheckStatus.PASS,
                message=f"{table}: no rows (event table: nothing has happened yet)",
            )
        if latest is None:
            status = CheckStatus.WARN if optional else CheckStatus.FAIL
            return CheckResult(name=f"{table} freshness", status=status, message=f"{table}: NO DATA")

        if warn_hours is None or fail_hours is None:
            return CheckResult(
                name=f"{table} freshness", status=CheckStatus.PASS,
                message=f"{table}: {row_count} rows (event table, no freshness limit)",
            )

        hours_ago = (datetime.now(timezone.utc) - self._as_utc(latest, ts_kind)).total_seconds() / 3600

        if hours_ago < warn_hours:
            return CheckResult(
                name=f"{table} freshness", status=CheckStatus.PASS,
                message=f"{table}: fresh ({hours_ago * 60:.0f}min ago)",
                details={"hours_ago": hours_ago},
            )
        if hours_ago < fail_hours:
            return CheckResult(
                name=f"{table} freshness", status=CheckStatus.WARN,
                message=f"{table}: stale ({hours_ago:.1f}h ago)",
                details={"hours_ago": hours_ago},
            )
        age = f"{hours_ago / 24:.1f} days" if hours_ago >= 48 else f"{hours_ago:.1f}h"
        return CheckResult(
            name=f"{table} freshness",
            status=CheckStatus.WARN if optional else CheckStatus.FAIL,  # optional feature may be switched off
            message=f"{table}: very stale ({age} ago)",
            details={"hours_ago": hours_ago},
        )

    @staticmethod
    def _as_utc(value, ts_kind: str) -> datetime:
        """The newest-row value as a UTC-aware datetime."""
        if ts_kind == "ms_epoch":
            return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
        if isinstance(value, date) and not isinstance(value, datetime):
            return datetime.combine(value, time.min, tzinfo=timezone.utc)
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

    def _completeness_result(
        self, cursor, table: str, ts_col: str, required_columns: List[str]
    ) -> CheckResult:
        column_checks = ", ".join(
            f"COUNT(CASE WHEN {col} IS NOT NULL THEN 1 END) AS {col}_ok"
            for col in required_columns
        )
        cursor.execute(f"""
            SELECT COUNT(*) AS total, {column_checks}
            FROM (
                SELECT {", ".join(required_columns)}
                FROM {table}
                ORDER BY {ts_col} DESC
                LIMIT {_COMPLETENESS_SAMPLE_SIZE}
            ) recent
        """)
        row = cursor.fetchone()
        total = row[0]

        if total == 0:
            return CheckResult(
                name=f"{table} completeness", status=CheckStatus.FAIL,
                message=f"{table}: no rows to check completeness",
            )

        worst_pct = 100.0
        worst_col = None
        for i, col in enumerate(required_columns):
            pct = (row[i + 1] / total) * 100.0
            if pct < worst_pct:
                worst_pct = pct
                worst_col = col

        if worst_pct >= _COMPLETENESS_WARN_PCT:
            return CheckResult(
                name=f"{table} completeness", status=CheckStatus.PASS,
                message=f"{table}: {worst_pct:.1f}% complete (worst column: {worst_col})",
                details={"worst_pct": worst_pct, "worst_column": worst_col},
            )
        return CheckResult(
            name=f"{table} completeness", status=CheckStatus.WARN,
            message=f"{table}: only {worst_pct:.1f}% complete on '{worst_col}' (last {total} rows)",
            details={"worst_pct": worst_pct, "worst_column": worst_col},
        )
