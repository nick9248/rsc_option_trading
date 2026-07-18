"""Local database freshness and completeness health check."""

from datetime import datetime
from typing import List, Tuple

from coding.core.health.models import CheckEnvironment, CheckResult, CheckStatus
from coding.service.health.base import HealthCheck

# (table, timestamp_column, timestamp_kind, warn_hours, fail_hours, required_columns)
_HOURLY_TABLES: List[Tuple[str, str, str, float, float, List[str]]] = [
    ("historical_trades", "trade_timestamp", "ms_epoch", 2.0, 24.0, ["direction", "iv"]),
    ("hourly_snapshots", "snapshot_hour", "datetime", 2.0, 24.0, []),
    ("onchain_analysis_snapshots", "snapshot_hour", "datetime", 2.0, 24.0, ["underlying_price"]),
    ("onchain_volatility_snapshots", "snapshot_hour", "datetime", 2.0, 24.0,
     ["atm_iv", "iv_percentile_expiry", "iv_percentile_365d", "realized_vol", "underlying_price"]),
    ("straddle_scan_history", "scan_time", "datetime", 2.0, 24.0,
     ["strike", "cost_usd", "breakeven_down", "breakeven_up",
      "iv_percentile", "iv_percentile_n_obs", "iv_percentile_window_days"]),
    ("forward_test_predictions", "snapshot_hour", "datetime", 2.0, 24.0,
     ["signal_direction", "signal_score", "signal_confidence", "spot_price_at_prediction"]),
]

# (table, timestamp_column, warn_days, fail_days, required_columns)
_DAILY_TABLES: List[Tuple[str, str, float, float, List[str]]] = [
    ("dvol_history", "timestamp", 7.0, 30.0, ["dvol_value"]),
    ("ohlcv_history", "date", 2.0, 5.0, ["close"]),
]

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
    environment = CheckEnvironment.LOCAL

    def run(self, repo) -> List[CheckResult]:
        results: List[CheckResult] = []
        conn = repo._get_connection()
        try:
            cursor = conn.cursor()
            try:
                for table, ts_col, ts_kind, warn_h, fail_h, required in _HOURLY_TABLES:
                    results.append(self._freshness_result(cursor, table, ts_col, ts_kind, warn_h, fail_h))
                    if required:
                        results.append(self._completeness_result(cursor, table, ts_col, required))
                for table, ts_col, warn_d, fail_d, required in _DAILY_TABLES:
                    results.append(self._freshness_result(cursor, table, ts_col, "datetime", warn_d * 24, fail_d * 24))
                    if required:
                        results.append(self._completeness_result(cursor, table, ts_col, required))
            finally:
                cursor.close()
        finally:
            repo._return_connection(conn)
        return results

    def _freshness_result(
        self, cursor, table: str, ts_col: str, ts_kind: str, warn_hours: float, fail_hours: float
    ) -> CheckResult:
        cursor.execute(f"SELECT MAX({ts_col}) FROM {table}")
        latest = cursor.fetchone()[0]

        if latest is None:
            return CheckResult(name=f"{table} freshness", status=CheckStatus.FAIL, message=f"{table}: NO DATA")

        if ts_kind == "ms_epoch":
            latest = datetime.fromtimestamp(latest / 1000)

        now = datetime.now(latest.tzinfo) if getattr(latest, "tzinfo", None) else datetime.now()
        hours_ago = (now - latest).total_seconds() / 3600

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
        return CheckResult(
            name=f"{table} freshness", status=CheckStatus.FAIL,
            message=f"{table}: very stale ({hours_ago / 24:.1f} days ago)",
            details={"hours_ago": hours_ago},
        )

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
