"""API connectivity health check."""

from typing import List

from coding.core.health.models import CheckEnvironment, CheckResult, CheckStatus
from coding.service.deribit.deribit_api_service import DeribitApiService
from coding.service.health.base import HealthCheck


class ApiConnectivityCheck(HealthCheck):
    """Confirms the Deribit API is reachable and returning valid data."""

    category = "API Connectivity"
    environment = CheckEnvironment.BOTH

    def __init__(self, api_service_factory=DeribitApiService):
        self._api_service_factory = api_service_factory

    def run(self, repo) -> List[CheckResult]:
        try:
            with self._api_service_factory() as api:
                response = api.get_ticker("BTC-PERPETUAL")
        except Exception as exc:
            return [CheckResult(
                name="Deribit API", status=CheckStatus.FAIL,
                message=f"Deribit API unreachable: {exc}",
            )]

        if not response or "index_price" not in response:
            return [CheckResult(
                name="Deribit API", status=CheckStatus.FAIL,
                message="Deribit API returned an invalid response",
            )]

        price = response["index_price"]
        return [CheckResult(
            name="Deribit API", status=CheckStatus.PASS,
            message=f"Connected — BTC ${price:,.2f}",
            details={"index_price": price},
        )]
