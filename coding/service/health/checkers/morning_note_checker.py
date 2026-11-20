"""Morning note synthesis smoke-test health check."""

from typing import List

from coding.core.health.models import CheckEnvironment, CheckResult, CheckStatus
from coding.service.health.base import HealthCheck
from coding.service.deribit.deribit_api_service import DeribitApiService
from coding.service.morning_note.morning_note_service import MorningNoteService
from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService

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
        Analyze ``currency`` and run the synthesis on the typed result. ``repo`` (the framework-injected,
        environment-scoped repository) is used as given. Nothing is saved: this is a read-only smoke test.
        """
        with DeribitApiService(timeout=90) as api_service:
            on_chain_service = OnChainAnalysisService(api_service, repository=repo)
            _, result = on_chain_service.fetch_and_analyze(currency=currency, return_result=True)
            return MorningNoteService(on_chain_service).generate(result)
