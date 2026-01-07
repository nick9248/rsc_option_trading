"""
Integration tests for Deribit API service.

These tests make real API calls to Deribit's public endpoints.
"""

import pytest

from coding.core.api.exceptions import ApiUnavailableError
from coding.service.deribit.deribit_api_service import DeribitApiService


@pytest.fixture
def api_service():
    """Create a DeribitApiService instance for testing."""
    service = DeribitApiService(
        timeout=30,
        max_retries=3,
        validate_responses=True,
        strict_validation=False
    )
    yield service
    service.close()


class TestDeribitApiServiceConnectivity:
    """Tests for API connectivity."""

    def test_check_connectivity(self, api_service):
        """Test that we can connect to the Deribit API."""
        result = api_service.check_connectivity()

        assert result["connected"] is True
        assert "version" in result
        assert result["testnet"] is False


class TestDeribitApiServiceEndpoints:
    """Tests for individual endpoints."""

    def test_get_expirations(self, api_service):
        """Test getting expiration dates for ETH."""
        result = api_service.get_expirations(currency="ETH")

        assert "eth" in result
        assert "option" in result["eth"]
        assert "future" in result["eth"]
        assert isinstance(result["eth"]["option"], list)
        assert len(result["eth"]["option"]) > 0

    def test_get_instruments(self, api_service):
        """Test getting ETH option instruments."""
        result = api_service.get_instruments(currency="ETH", kind="option")

        assert isinstance(result, list)
        assert len(result) > 0

        instrument = result[0]
        assert "instrument_name" in instrument
        assert "kind" in instrument
        assert instrument["kind"] == "option"

    def test_get_book_summary(self, api_service):
        """Test getting book summary for ETH options."""
        result = api_service.get_book_summary(currency="ETH", kind="option")

        assert isinstance(result, list)
        assert len(result) > 0

        entry = result[0]
        assert "instrument_name" in entry
        assert "mark_price" in entry
        assert "open_interest" in entry

    def test_get_ticker(self, api_service):
        """Test getting ticker for ETH-PERPETUAL."""
        result = api_service.get_ticker(instrument_name="ETH-PERPETUAL")

        assert isinstance(result, dict)
        assert result["instrument_name"] == "ETH-PERPETUAL"
        assert "last_price" in result
        assert "mark_price" in result
        assert "index_price" in result
        assert "stats" in result

    def test_get_order_book(self, api_service):
        """Test getting order book for ETH-PERPETUAL."""
        result = api_service.get_order_book(
            instrument_name="ETH-PERPETUAL",
            depth=5
        )

        assert isinstance(result, dict)
        assert "bids" in result
        assert "asks" in result
        assert len(result["bids"]) <= 5
        assert len(result["asks"]) <= 5

    def test_get_funding_chart_data(self, api_service):
        """Test getting funding chart data."""
        result = api_service.get_funding_chart_data(
            instrument_name="ETH-PERPETUAL",
            length="8h"
        )

        assert isinstance(result, dict)
        assert "data" in result
        assert isinstance(result["data"], list)
        assert "interest_8h" in result

    def test_get_historical_volatility(self, api_service):
        """Test getting historical volatility for ETH."""
        result = api_service.get_historical_volatility(currency="ETH")

        assert isinstance(result, list)
        assert len(result) > 0
        assert len(result[0]) == 2

    def test_get_volatility_index_data(self, api_service):
        """Test getting DVOL data for ETH."""
        result = api_service.get_volatility_index_data(
            currency="ETH",
            resolution=3600
        )

        assert isinstance(result, dict)
        assert "data" in result
        assert isinstance(result["data"], list)


class TestDeribitApiServiceCsvExport:
    """Tests for CSV export functionality."""

    def test_get_instruments_with_csv(self, api_service, tmp_path):
        """Test that instruments can be saved to CSV."""
        result = api_service.get_instruments(
            currency="ETH",
            kind="option",
            save_to_csv=True
        )

        assert isinstance(result, list)
        assert len(result) > 0
