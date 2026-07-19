"""IV-percentile window freshness health check."""

from datetime import datetime, timedelta
from typing import List

from coding.core.health.models import CheckEnvironment, CheckResult, CheckStatus
from coding.service.health.base import HealthCheck

_ACTIVE_WINDOW_HOURS = 48
_STALE_WARN_DAYS = 7.0


class IvPercentileWindowCheck(HealthCheck):
    """
    For every currently-active expiry, surfaces how stale its IV-percentile
    reconstruction is -- catches the class of bug where an expiry's
    onchain_volatility_snapshots rows silently stop updating (e.g. 26MAR27
    stuck since 2026-07-13) while still being presented as if ranked
    against a full year of history.

    Candidate expiries are sourced from onchain_analysis_snapshots (written
    reliably every cycle by the daemon) rather than from
    onchain_volatility_snapshots itself -- an expiry whose reconstruction
    has completely stopped has zero recent rows in the latter table, so
    filtering candidates from that same table would make the checker blind
    to the exact failure mode it exists to catch.
    """

    category = "IV-Percentile Window"
    environment = CheckEnvironment.LOCAL

    def run(self, repo) -> List[CheckResult]:
        window_start = datetime.now() - timedelta(hours=_ACTIVE_WINDOW_HOURS)

        conn = repo._get_connection()
        try:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    SELECT DISTINCT currency, expiration
                    FROM onchain_analysis_snapshots
                    WHERE snapshot_hour >= %s
                    ORDER BY currency, expiration
                """, (window_start,))
                candidates = cursor.fetchall()

                if not candidates:
                    return [CheckResult(
                        name="IV-percentile window", status=CheckStatus.WARN,
                        message=f"No active expiries found in onchain_analysis_snapshots in the last {_ACTIVE_WINDOW_HOURS}h",
                    )]

                results: List[CheckResult] = []
                for currency, expiration in candidates:
                    try:
                        cursor.execute("""
                            SELECT MAX(reconstructed_at)
                            FROM onchain_volatility_snapshots
                            WHERE currency = %s AND expiration = %s
                        """, (currency, expiration))
                        latest_reconstructed = cursor.fetchone()[0]
                        results.append(self._evaluate(repo, currency, expiration, latest_reconstructed))
                    except Exception as exc:
                        conn.rollback()
                        results.append(CheckResult(
                            name=f"IV-percentile window ({currency} {expiration})", status=CheckStatus.FAIL,
                            message=f"{currency} {expiration}: check failed: {exc}",
                        ))
            finally:
                cursor.close()
        finally:
            repo._return_connection(conn)
        return results

    def _evaluate(self, repo, currency: str, expiration: str, latest_reconstructed) -> CheckResult:
        if latest_reconstructed is None:
            return CheckResult(
                name=f"IV-percentile window ({currency} {expiration})", status=CheckStatus.FAIL,
                message=f"{currency} {expiration}: volatility reconstruction has never run for this expiry",
            )

        now = datetime.now(latest_reconstructed.tzinfo) if latest_reconstructed.tzinfo else datetime.now()
        days_stale = (now - latest_reconstructed).total_seconds() / 86400.0

        window_info = repo.get_iv_percentile_with_window(currency, expiration)
        n_obs = window_info.get("n_obs", 0)
        window_days = window_info.get("window_days", 0.0)

        if days_stale >= _STALE_WARN_DAYS:
            return CheckResult(
                name=f"IV-percentile window ({currency} {expiration})", status=CheckStatus.WARN,
                message=(
                    f"{currency} {expiration}: reconstruction stale ({days_stale:.1f} days) — "
                    f"n_obs={n_obs}, window={window_days:.0f}d"
                ),
                details={"days_stale": days_stale, "n_obs": n_obs, "window_days": window_days},
            )
        return CheckResult(
            name=f"IV-percentile window ({currency} {expiration})", status=CheckStatus.PASS,
            message=f"{currency} {expiration}: fresh, n_obs={n_obs}, window={window_days:.0f}d",
            details={"days_stale": days_stale, "n_obs": n_obs, "window_days": window_days},
        )
