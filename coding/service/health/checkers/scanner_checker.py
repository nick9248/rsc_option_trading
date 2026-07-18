"""Straddle scanner activity health check."""

from datetime import datetime, timedelta
from typing import List

from coding.core.health.models import CheckEnvironment, CheckResult, CheckStatus
from coding.service.health.base import HealthCheck

_LOOKBACK_HOURS = 6
_CURRENCIES = ["BTC", "ETH"]


class ScannerActivityCheck(HealthCheck):
    """Confirms the straddle scanner is producing scan rows every cycle."""

    category = "Scanner Activity"
    environment = CheckEnvironment.BOTH

    def run(self, repo) -> List[CheckResult]:
        results: List[CheckResult] = []
        window_start = datetime.now() - timedelta(hours=_LOOKBACK_HOURS)

        conn = repo._get_connection()
        try:
            cursor = conn.cursor()
            try:
                for currency in _CURRENCIES:
                    cursor.execute("""
                        SELECT COUNT(*), MAX(scan_time)
                        FROM straddle_scan_history
                        WHERE currency = %s AND scan_time >= %s
                    """, (currency, window_start))
                    count, latest = cursor.fetchone()

                    if count == 0:
                        results.append(CheckResult(
                            name=f"Scanner activity ({currency})", status=CheckStatus.FAIL,
                            message=f"{currency}: no straddle scans recorded in the last {_LOOKBACK_HOURS}h",
                        ))
                        continue

                    now = datetime.now(latest.tzinfo) if latest.tzinfo else datetime.now()
                    hours_ago = (now - latest).total_seconds() / 3600
                    results.append(CheckResult(
                        name=f"Scanner activity ({currency})", status=CheckStatus.PASS,
                        message=f"{currency}: {count} scans in last {_LOOKBACK_HOURS}h, latest {hours_ago * 60:.0f}min ago",
                        details={"count": count, "hours_ago": hours_ago},
                    ))
            finally:
                cursor.close()
        finally:
            repo._return_connection(conn)
        return results
