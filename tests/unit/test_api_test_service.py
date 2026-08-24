"""
Unit tests for ApiTestService (Task Wave-J-F Fix 1).

Proves the dispatch table, per-endpoint parameter coercion, and API client
construction that used to live in coding/gui/tabs/api_connection_tab.py's
QThread workers now live here and behave the same way. Everything is
exercised with fakes/mocks -- no live API calls.
"""

from unittest.mock import MagicMock

import pytest

from coding.service.api_testing.api_test_service import (
    ApiTestService,
    UnknownEndpointError,
)


def _make_api_service_factory():
    """
    Returns (factory, mock_service) where factory() is a context manager
    yielding mock_service, mirroring ``with DeribitApiService() as
    service:``.
    """
    mock_service = MagicMock(name="deribit_api_service")
    mock_context = MagicMock()
    mock_context.__enter__.return_value = mock_service
    mock_context.__exit__.return_value = False
    factory = MagicMock(return_value=mock_context)
    return factory, mock_service


class TestInvokeDispatchesToDeribitService:
    def test_test_connection_calls_check_connectivity_with_no_params(self):
        factory, mock_service = _make_api_service_factory()
        mock_service.check_connectivity.return_value = {"status": "ok"}
        service = ApiTestService(api_service_factory=factory)

        result = service.invoke("Test Connection", {"save_to_csv": False})

        mock_service.check_connectivity.assert_called_once_with()
        assert result == {"status": "ok"}

    def test_get_ticker_forwards_parameters_to_service_method(self):
        factory, mock_service = _make_api_service_factory()
        mock_service.get_ticker.return_value = {"last_price": 90000}
        service = ApiTestService(api_service_factory=factory)

        result = service.invoke(
            "Get Ticker",
            {"instrument_name": "BTC-PERPETUAL", "save_to_csv": True},
        )

        mock_service.get_ticker.assert_called_once_with(
            instrument_name="BTC-PERPETUAL", save_to_csv=True
        )
        assert result == {"last_price": 90000}

    def test_get_last_trades_by_time_coerces_timestamps_to_int(self):
        """
        Carried behavior: the GUI's parameter widgets hand back strings for
        timestamp fields (QLineEdit.text()); the timestamps must be coerced
        to int before being passed to the service method.
        """
        factory, mock_service = _make_api_service_factory()
        mock_service.get_last_trades_by_currency_and_time.return_value = []
        service = ApiTestService(api_service_factory=factory)

        service.invoke(
            "Get Last Trades By Time",
            {
                "currency": "BTC",
                "kind": "option",
                "start_timestamp": "1700000000000",
                "end_timestamp": "1700003600000",
                "count": 50,
            },
        )

        mock_service.get_last_trades_by_currency_and_time.assert_called_once_with(
            currency="BTC",
            kind="option",
            start_timestamp=1700000000000,
            end_timestamp=1700003600000,
            count=50,
        )

    def test_unknown_endpoint_raises(self):
        factory, _ = _make_api_service_factory()
        service = ApiTestService(api_service_factory=factory)

        with pytest.raises(UnknownEndpointError, match="Unknown endpoint"):
            service.invoke("Not A Real Endpoint", {})

    def test_service_error_propagates(self):
        factory, mock_service = _make_api_service_factory()
        mock_service.get_expirations.side_effect = RuntimeError("api down")
        service = ApiTestService(api_service_factory=factory)

        with pytest.raises(RuntimeError, match="api down"):
            service.invoke("Get Expirations", {"currency": "BTC"})


class TestInvokeDispatchesToExternalFetcher:
    def test_fear_greed_index_calls_fear_greed_get_latest(self):
        mock_fetcher = MagicMock()
        mock_fetcher.fear_greed.get_latest.return_value = {"value": 42}
        external_factory = MagicMock(return_value=mock_fetcher)
        factory, _ = _make_api_service_factory()
        service = ApiTestService(
            api_service_factory=factory, external_fetcher_factory=external_factory
        )

        result = service.invoke("Fear & Greed Index", {})

        mock_fetcher.fear_greed.get_latest.assert_called_once_with()
        assert result == {"value": 42}

    def test_coingecko_market_data_calls_coingecko_get_global_market_data(self):
        mock_fetcher = MagicMock()
        mock_fetcher.coingecko.get_global_market_data.return_value = {"btc_dominance": 55.0}
        external_factory = MagicMock(return_value=mock_fetcher)
        factory, _ = _make_api_service_factory()
        service = ApiTestService(
            api_service_factory=factory, external_fetcher_factory=external_factory
        )

        result = service.invoke("CoinGecko Market Data", {})

        mock_fetcher.coingecko.get_global_market_data.assert_called_once_with()
        assert result == {"btc_dominance": 55.0}

    def test_external_endpoint_does_not_construct_deribit_api_service(self):
        mock_fetcher = MagicMock()
        external_factory = MagicMock(return_value=mock_fetcher)
        factory, _ = _make_api_service_factory()
        service = ApiTestService(
            api_service_factory=factory, external_fetcher_factory=external_factory
        )

        service.invoke("Fear & Greed Index", {})

        factory.assert_not_called()

    def test_is_external_endpoint(self):
        service = ApiTestService()
        assert service.is_external_endpoint("Fear & Greed Index") is True
        assert service.is_external_endpoint("CoinGecko Market Data") is True
        assert service.is_external_endpoint("Get Ticker") is False


class TestLoadInstruments:
    def test_calls_get_instruments_with_currency_and_kind(self):
        factory, mock_service = _make_api_service_factory()
        mock_service.get_instruments.return_value = [{"instrument_name": "BTC-1JAN27-100000-C"}]
        service = ApiTestService(api_service_factory=factory)

        result = service.load_instruments("BTC", "option")

        mock_service.get_instruments.assert_called_once_with(currency="BTC", kind="option")
        assert result == [{"instrument_name": "BTC-1JAN27-100000-C"}]
