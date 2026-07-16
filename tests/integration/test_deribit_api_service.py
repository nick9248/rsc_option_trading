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

    def test_get_index_price_btc(self, api_service):
        """Test getting the live BTC index (spot) price."""
        price = api_service.get_index_price(currency="BTC")

        assert isinstance(price, float)
        assert price > 0

    def test_get_index_price_eth(self, api_service):
        """Test getting the live ETH index (spot) price."""
        price = api_service.get_index_price(currency="ETH")

        assert isinstance(price, float)
        assert price > 0


class TestGetOptionChainSnapshot:
    """
    Live integration tests for the foundational get_option_chain_snapshot
    method — cross-checks that USD conversion matches index_price, not
    underlying_price (the confirmed pricing bug this method fixes).
    """

    def test_snapshot_structure_and_pricing_basis(self, api_service):
        """
        Verifies the full snapshot shape and, critically, that ask_usd for a
        real live contract equals ask_price * index_price (not
        underlying_price). This is the live cross-check proof required
        alongside the unit tests.
        """
        snapshot = api_service.get_option_chain_snapshot(currency="BTC")

        assert "as_of" in snapshot
        assert "index_price" in snapshot
        assert "contracts" in snapshot
        assert "futures_by_expiry" in snapshot
        assert snapshot["index_price"] > 0
        assert len(snapshot["contracts"]) > 0

        priced = [
            c for c in snapshot["contracts"]
            if c["ask_price"] is not None and c["ask_usd"] is not None
        ]
        assert priced, "Expected at least one contract with a live ask price"

        contract = priced[0]
        expected_ask_usd = contract["ask_price"] * snapshot["index_price"]
        assert contract["ask_usd"] == pytest.approx(expected_ask_usd)

        # Sanity: underlying_price (future) must differ from index_price by
        # only a small basis, and both must be positive.
        if contract["underlying_price"]:
            basis_pct = abs(
                contract["underlying_price"] - snapshot["index_price"]
            ) / snapshot["index_price"] * 100
            assert basis_pct < 5.0  # futures basis is never anywhere near 5%

    def test_dte_is_positive_float_for_future_expiries(self, api_service):
        snapshot = api_service.get_option_chain_snapshot(currency="BTC")
        future_contracts = [c for c in snapshot["contracts"] if c["dte"] > 0]
        assert future_contracts
        assert isinstance(future_contracts[0]["dte"], float)


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
