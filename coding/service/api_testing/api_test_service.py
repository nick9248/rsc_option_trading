"""
Service facade for the API Connection tab's "run this endpoint" action.

Task Wave-J-F Fix 1: coding/gui/tabs/api_connection_tab.py used to own an
endpoint-name -> service-method dispatch table, per-endpoint parameter
coercion (e.g. casting Get Last Trades By Time's timestamps to int), and
constructed DeribitApiService/ExternalMetricsFetcher directly inside its
QThread workers -- exactly the "business logic in the GUI worker" pattern
CLAUDE.md's Code Quality Checklist calls out as wrong. That knowledge moves
here, mirroring coding/service/on_chain/on_chain_workflow_service.py's
one-call pattern for on_chain_analysis_tab.py: the GUI worker makes a single
``ApiTestService(...).invoke(endpoint_name, parameters)`` (or
``.load_instruments(...)``) call and renders whatever comes back. No
endpoint knowledge, parameter coercion, or API client construction remains
in the GUI file.
"""

import logging
from typing import Any, Callable, Dict, Optional

from coding.core.api.external_apis import ExternalMetricsFetcher
from coding.service.deribit.deribit_api_service import DeribitApiService

logger = logging.getLogger(__name__)


# Endpoints served by ExternalMetricsFetcher rather than DeribitApiService.
EXTERNAL_ENDPOINTS = frozenset({"Fear & Greed Index", "CoinGecko Market Data"})


class UnknownEndpointError(ValueError):
    """Raised by ``invoke`` when given an endpoint name it doesn't recognize."""


class ApiTestService:
    """
    Single entry point for "call this API Connection tab endpoint with these
    parameters and give me the result".

    Owns the endpoint-name -> method dispatch table (both the Deribit
    endpoints and the external ones), the per-endpoint parameter coercion
    the timestamp-based endpoint needs, and construction of the underlying
    API clients (``DeribitApiService``, ``ExternalMetricsFetcher``).

    API client factories are injectable (constructor injection, matching
    this codebase's other Service-layer classes) so tests can substitute
    fakes/mocks without hitting the network.
    """

    def __init__(
        self,
        api_service_factory: Callable[[], DeribitApiService] = DeribitApiService,
        external_fetcher_factory: Callable[[], ExternalMetricsFetcher] = ExternalMetricsFetcher,
    ) -> None:
        self._api_service_factory = api_service_factory
        self._external_fetcher_factory = external_fetcher_factory

    def is_external_endpoint(self, endpoint_name: str) -> bool:
        """True if ``endpoint_name`` is served by ExternalMetricsFetcher, not Deribit."""
        return endpoint_name in EXTERNAL_ENDPOINTS

    def invoke(self, endpoint_name: str, parameters: Optional[Dict[str, Any]] = None) -> Any:
        """
        Run ``endpoint_name`` with ``parameters`` and return its result.

        Args:
            endpoint_name: One of ApiConnectionTab.ENDPOINTS' keys.
            parameters: User-entered parameter values (ignored for endpoints
                that take none, e.g. "Test Connection" and the external
                endpoints).

        Returns:
            Whatever the underlying service method returns.

        Raises:
            UnknownEndpointError: ``endpoint_name`` isn't recognized.
            Whatever the underlying API client raises otherwise -- this
            facade does not swallow errors; the GUI worker's except clause
            decides how to surface them.
        """
        if endpoint_name in EXTERNAL_ENDPOINTS:
            return self._invoke_external(endpoint_name)
        return self._invoke_deribit(endpoint_name, parameters or {})

    def load_instruments(self, currency: str, kind: str) -> Any:
        """Fetch the instrument list used to populate an instrument selector."""
        with self._api_service_factory() as service:
            return service.get_instruments(currency=currency, kind=kind)

    def _invoke_external(self, endpoint_name: str) -> Any:
        fetcher = self._external_fetcher_factory()
        if endpoint_name == "Fear & Greed Index":
            return fetcher.fear_greed.get_latest()
        if endpoint_name == "CoinGecko Market Data":
            return fetcher.coingecko.get_global_market_data()
        raise UnknownEndpointError(f"Unknown external endpoint: {endpoint_name}")

    def _invoke_deribit(self, endpoint_name: str, parameters: Dict[str, Any]) -> Any:
        with self._api_service_factory() as service:
            method_map = {
                "Test Connection": service.check_connectivity,
                "Get Expirations": service.get_expirations,
                "Get Instruments": service.get_instruments,
                "Get Book Summary": service.get_book_summary,
                "Get Ticker": service.get_ticker,
                "Get Order Book": service.get_order_book,
                "Get Funding Chart": service.get_funding_chart_data,
                "Get Historical Volatility": service.get_historical_volatility,
                "Get Volatility Index": service.get_volatility_index_data,
                "Get Last Trades": service.get_last_trades_by_currency,
                "Get Last Trades By Time": service.get_last_trades_by_currency_and_time,
                "Get TradingView Chart": service.get_tradingview_chart_data,
            }

            method = method_map.get(endpoint_name)
            if method is None:
                raise UnknownEndpointError(f"Unknown endpoint: {endpoint_name}")

            if endpoint_name == "Test Connection":
                return method()

            params = dict(parameters)
            if endpoint_name == "Get Last Trades By Time":
                params["start_timestamp"] = int(params["start_timestamp"])
                params["end_timestamp"] = int(params["end_timestamp"])
            return method(**params)
