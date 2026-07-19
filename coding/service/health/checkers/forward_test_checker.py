"""Forward-testing harness health checks (on-chain Phase 3 + straddle scanner)."""

from typing import List

from coding.core.health.models import CheckEnvironment, CheckResult, CheckStatus
from coding.service.health.base import HealthCheck
from coding.service.on_chain.forward_testing_harness import ForwardTestingHarness

_CURRENCIES = ["BTC", "ETH"]
_MIN_RESOLVED_FOR_PASS = 1


class OnChainForwardTestCheck(HealthCheck):
    """Confirms the Phase-3 on-chain forward-testing harness is recording predictions."""

    category = "Forward-Test Harnesses"
    environment = CheckEnvironment.BOTH

    def __init__(self, harness_factory=ForwardTestingHarness):
        self._harness_factory = harness_factory

    def run(self, repo) -> List[CheckResult]:
        harness = self._harness_factory(repository=repo)
        results: List[CheckResult] = []

        for currency in _CURRENCIES:
            try:
                record = harness.get_track_record(currency)
            except Exception as exc:
                results.append(CheckResult(
                    name=f"On-chain forward-test ({currency})", status=CheckStatus.FAIL,
                    message=f"{currency}: check failed: {exc}",
                ))
                continue

            n_total = record.get("n_total", 0)
            n_signals = record.get("n_signals", 0)
            hit_rate = record.get("hit_rate")
            criteria_met = record.get("criteria_met", False)

            if n_total == 0:
                results.append(CheckResult(
                    name=f"On-chain forward-test ({currency})", status=CheckStatus.WARN,
                    message=f"{currency}: no forward-test predictions recorded yet",
                ))
            else:
                hit_rate_str = f"{hit_rate:.1%}" if hit_rate is not None else "n/a"
                results.append(CheckResult(
                    name=f"On-chain forward-test ({currency})", status=CheckStatus.PASS,
                    message=(
                        f"{currency}: {n_total} predictions, {n_signals} directional, "
                        f"hit_rate={hit_rate_str}, criteria_met={criteria_met}"
                    ),
                    details=record,
                ))
        return results


class StraddleForwardTestCheck(HealthCheck):
    """Confirms the straddle scanner's forward-test harness is resolving settlements."""

    category = "Forward-Test Harnesses"
    environment = CheckEnvironment.BOTH

    def run(self, repo) -> List[CheckResult]:
        results: List[CheckResult] = []

        conn = repo._get_connection()
        try:
            cursor = conn.cursor()
            try:
                for currency in _CURRENCIES:
                    try:
                        cursor.execute("""
                            SELECT
                                COUNT(*) AS total,
                                COUNT(resolved_at) AS resolved,
                                AVG(settlement_return_pct) FILTER (WHERE resolved_at IS NOT NULL) AS avg_return
                            FROM straddle_scan_history
                            WHERE currency = %s
                        """, (currency,))
                        total, resolved, avg_return = cursor.fetchone()

                        if total == 0:
                            results.append(CheckResult(
                                name=f"Straddle forward-test ({currency})", status=CheckStatus.WARN,
                                message=f"{currency}: no straddle scan history recorded yet",
                            ))
                        elif resolved < _MIN_RESOLVED_FOR_PASS:
                            results.append(CheckResult(
                                name=f"Straddle forward-test ({currency})", status=CheckStatus.WARN,
                                message=f"{currency}: {total} scans recorded, none settled yet",
                                details={"total": total, "resolved": resolved},
                            ))
                        else:
                            avg_str = f"{avg_return:.1f}%" if avg_return is not None else "n/a"
                            results.append(CheckResult(
                                name=f"Straddle forward-test ({currency})", status=CheckStatus.PASS,
                                message=f"{currency}: {resolved}/{total} scans settled, avg return {avg_str}",
                                details={"total": total, "resolved": resolved, "avg_return": avg_return},
                            ))
                    except Exception as exc:
                        conn.rollback()
                        results.append(CheckResult(
                            name=f"Straddle forward-test ({currency})", status=CheckStatus.FAIL,
                            message=f"{currency}: check failed: {exc}",
                        ))
            finally:
                cursor.close()
        finally:
            repo._return_connection(conn)
        return results
