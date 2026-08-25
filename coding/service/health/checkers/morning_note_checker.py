"""Morning note synthesis smoke-test health check."""

from typing import List

from coding.core.health.models import CheckEnvironment, CheckResult, CheckStatus
from coding.service.health.base import HealthCheck
from coding.service.on_chain.on_chain_workflow_service import OnChainWorkflowService

_CURRENCIES = ["BTC", "ETH"]


class MorningNoteSmokeTestCheck(HealthCheck):
    """
    Confirms the morning note synthesis pipeline still runs end to end.
    Morning Note has no DB table or cron -- it's an on-demand report
    generator -- so there's no "freshness" to check; this invokes it for
    real against live data and confirms it doesn't raise and doesn't
    return empty text.
    """

    category = "Morning Note"
    environment = CheckEnvironment.LOCAL

    def __init__(self, synthesis_runner=None):
        self._synthesis_runner = synthesis_runner or self._default_synthesis_runner

    def run(self, repo) -> List[CheckResult]:
        results: List[CheckResult] = []

        for currency in _CURRENCIES:
            try:
                synthesis = self._synthesis_runner(currency, repo)
            except Exception as exc:
                results.append(CheckResult(
                    name=f"Morning note ({currency})", status=CheckStatus.FAIL,
                    message=f"{currency}: synthesis raised: {exc}",
                ))
                continue

            if not synthesis or not synthesis.strip():
                results.append(CheckResult(
                    name=f"Morning note ({currency})", status=CheckStatus.FAIL,
                    message=f"{currency}: synthesis returned empty text",
                ))
            else:
                results.append(CheckResult(
                    name=f"Morning note ({currency})", status=CheckStatus.PASS,
                    message=f"{currency}: synthesis OK ({len(synthesis)} chars)",
                ))
        return results

    @staticmethod
    def _default_synthesis_runner(currency: str, repo) -> str:
        """
        T9 (refactor_design_spec.md): one call into ``OnChainWorkflowService``
        replaces the manual fetch_and_analyze + MorningNoteService
        orchestration. ``repo`` (the framework-injected, environment-scoped
        repository) is passed through explicitly so this check keeps
        querying the exact repository instance the health-check framework
        gave it -- the workflow service otherwise defaults to constructing
        its own fresh ``DatabaseRepository()``, which is correct for the
        GUI's fire-and-forget usage but would be wrong here.

        Carried finding #3 (A6 review): ``save_bundle=False`` -- this is a
        read-only synthesis smoke test, not a report-generation request.
        Without it, every health-check invocation wrote a timestamped
        report bundle to output/data/onchain_analysis/, an unflagged side
        effect and failure surface for a check that only needs the
        synthesis text.
        """
        output = OnChainWorkflowService(currency, repository=repo).run(save_bundle=False)
        return output.synthesis_text
