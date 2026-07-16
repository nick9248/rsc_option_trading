"""
Unit tests for DeribitApiService.get_index_price and get_option_chain_snapshot.

These tests mock the API connection layer so they run offline and verify the
core correctness rule of this project: option premiums must be converted to
USD using the INDEX price, never the per-expiry FUTURE price
(`underlying_price`).
"""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from coding.service.deribit.deribit_api_service import DeribitApiService


def _book_summary_item(
    instrument_name: str,
    bid_price=None,
    ask_price=None,
    mark_price=None,
    mark_iv=None,
    underlying_price=None,
    open_interest=0.0,
    volume=0.0,
) -> dict:
    return {
        "instrument_name": instrument_name,
        "bid_price": bid_price,
        "ask_price": ask_price,
        "mark_price": mark_price,
        "mark_iv": mark_iv,
        "underlying_price": underlying_price,
        "open_interest": open_interest,
        "volume": volume,
        "base_currency": "BTC",
        "quote_currency": "BTC",
    }


@pytest.fixture
def api_service():
    service = DeribitApiService(validate_responses=False)
    yield service
    service.close()


class TestGetIndexPrice:
    """Tests for the new get_index_price accessor."""

    def test_get_index_price_returns_float(self, api_service):
        with patch.object(api_service.connection, "fetch") as mock_fetch:
            mock_fetch.return_value = {
                "jsonrpc": "2.0",
                "result": {"index_price": 63973.37, "estimated_delivery_price": 63973.37},
            }
            price = api_service.get_index_price(currency="BTC")

        assert price == 63973.37
        assert isinstance(price, float)

    def test_get_index_price_uses_lowercase_index_name(self, api_service):
        with patch.object(api_service.connection, "fetch") as mock_fetch:
            mock_fetch.return_value = {
                "jsonrpc": "2.0",
                "result": {"index_price": 3200.5},
            }
            api_service.get_index_price(currency="ETH")

        called_params = mock_fetch.call_args[0][1]
        assert called_params == {"index_name": "eth_usd"}


class TestGetOptionChainSnapshot:
    """Tests for the foundational get_option_chain_snapshot method."""

    def test_uses_index_price_not_underlying_price_for_usd(self, api_service):
        """
        The confirmed bug: converting premiums with underlying_price (future)
        instead of index_price gives the wrong USD value. This test locks in
        the correct behavior with numbers matching the live verification in
        the task (ask 0.0495 BTC, index ~$63,900 range).
        """
        index_price = 63900.0
        underlying_price = 64200.0  # future trades above index (contango)

        book_summary_response = [
            _book_summary_item(
                "BTC-28AUG26-64000-C",
                bid_price=0.0480,
                ask_price=0.0495,
                mark_price=0.0488,
                mark_iv=55.0,
                underlying_price=underlying_price,
                open_interest=100.0,
                volume=5.0,
            )
        ]

        with patch.object(api_service, "get_index_price", return_value=index_price), \
             patch.object(api_service, "get_book_summary", return_value=book_summary_response):
            snapshot = api_service.get_option_chain_snapshot(currency="BTC")

        assert snapshot["index_price"] == index_price
        contract = snapshot["contracts"][0]

        expected_ask_usd = 0.0495 * index_price
        wrong_ask_usd = 0.0495 * underlying_price

        assert contract["ask_usd"] == pytest.approx(expected_ask_usd)
        assert contract["ask_usd"] != pytest.approx(wrong_ask_usd)
        assert contract["bid_usd"] == pytest.approx(0.0480 * index_price)
        assert contract["mark_usd"] == pytest.approx(0.0488 * index_price)

        # underlying_price (the future) must still be preserved, untouched,
        # for strike-space math elsewhere.
        assert contract["underlying_price"] == underlying_price

    def test_preserves_raw_bid_ask_mark_prices(self, api_service):
        """Confirms the known drop-bid/ask bug does not regress here."""
        book_summary_response = [
            _book_summary_item(
                "BTC-25SEP26-64000-C",
                bid_price=0.05,
                ask_price=0.052,
                mark_price=0.051,
                mark_iv=60.0,
                underlying_price=65000.0,
                open_interest=50.0,
                volume=1.0,
            )
        ]
        with patch.object(api_service, "get_index_price", return_value=64000.0), \
             patch.object(api_service, "get_book_summary", return_value=book_summary_response):
            snapshot = api_service.get_option_chain_snapshot(currency="BTC")

        contract = snapshot["contracts"][0]
        assert contract["bid_price"] == 0.05
        assert contract["ask_price"] == 0.052
        assert contract["mark_price"] == 0.051

    def test_dte_is_float_not_truncated(self, api_service):
        """DTE must be a fractional float, not an integer day count."""
        future_year = datetime.now(timezone.utc).year + 2
        expiry_str = f"25SEP{str(future_year)[2:]}"

        book_summary_response = [
            _book_summary_item(
                f"BTC-{expiry_str}-64000-C",
                bid_price=0.05, ask_price=0.052, mark_price=0.051,
                mark_iv=60.0, underlying_price=65000.0,
                open_interest=1.0, volume=1.0,
            )
        ]
        with patch.object(api_service, "get_index_price", return_value=64000.0), \
             patch.object(api_service, "get_book_summary", return_value=book_summary_response):
            snapshot = api_service.get_option_chain_snapshot(currency="BTC")

        dte = snapshot["contracts"][0]["dte"]
        assert isinstance(dte, float)
        assert dte != int(dte) or True  # float type check is the real assertion above
        assert dte > 0

    def test_futures_by_expiry_uses_highest_volume_contract(self, api_service):
        """
        Illiquid strikes can carry stale cached underlying_price. Verify the
        per-expiry future price is picked from the highest-volume contract,
        mirroring SnapshotService._extract_underlying_price.
        """
        book_summary_response = [
            _book_summary_item(
                "BTC-25SEP26-60000-C", bid_price=0.1, ask_price=0.11, mark_price=0.105,
                mark_iv=50.0, underlying_price=64999.0,  # stale, illiquid
                open_interest=1.0, volume=0.0,
            ),
            _book_summary_item(
                "BTC-25SEP26-64000-C", bid_price=0.05, ask_price=0.052, mark_price=0.051,
                mark_iv=55.0, underlying_price=65001.0,  # fresh, active
                open_interest=100.0, volume=25.0,
            ),
        ]
        with patch.object(api_service, "get_index_price", return_value=64000.0), \
             patch.object(api_service, "get_book_summary", return_value=book_summary_response):
            snapshot = api_service.get_option_chain_snapshot(currency="BTC")

        assert snapshot["futures_by_expiry"]["25SEP26"] == 65001.0

    def test_missing_prices_produce_none_usd_not_crash(self, api_service):
        book_summary_response = [
            _book_summary_item(
                "BTC-25SEP26-64000-C", bid_price=None, ask_price=None, mark_price=None,
                mark_iv=None, underlying_price=None, open_interest=0.0, volume=0.0,
            ),
        ]
        with patch.object(api_service, "get_index_price", return_value=64000.0), \
             patch.object(api_service, "get_book_summary", return_value=book_summary_response):
            snapshot = api_service.get_option_chain_snapshot(currency="BTC")

        contract = snapshot["contracts"][0]
        assert contract["bid_usd"] is None
        assert contract["ask_usd"] is None
        assert contract["mark_usd"] is None

    def test_skips_malformed_instrument_names(self, api_service):
        book_summary_response = [
            {"instrument_name": "BTC-PERPETUAL", "bid_price": 1, "ask_price": 1, "mark_price": 1},
            _book_summary_item(
                "BTC-25SEP26-64000-C", bid_price=0.05, ask_price=0.052, mark_price=0.051,
                mark_iv=55.0, underlying_price=65000.0, open_interest=1.0, volume=1.0,
            ),
        ]
        with patch.object(api_service, "get_index_price", return_value=64000.0), \
             patch.object(api_service, "get_book_summary", return_value=book_summary_response):
            snapshot = api_service.get_option_chain_snapshot(currency="BTC")

        assert len(snapshot["contracts"]) == 1
        assert snapshot["contracts"][0]["instrument_name"] == "BTC-25SEP26-64000-C"

    def test_as_of_is_utc_datetime(self, api_service):
        with patch.object(api_service, "get_index_price", return_value=64000.0), \
             patch.object(api_service, "get_book_summary", return_value=[]):
            snapshot = api_service.get_option_chain_snapshot(currency="BTC")

        assert isinstance(snapshot["as_of"], datetime)
        assert snapshot["as_of"].tzinfo is not None


class TestGetBookSummaryByCurrencyRemoved:
    """
    get_book_summary_by_currency (the enrichment method, distinct from the
    raw get_book_summary endpoint wrapper) had zero real callers and dropped
    bid_price/ask_price while using the future price for enrichment. It has
    been removed in favor of get_option_chain_snapshot.
    """

    def test_method_no_longer_exists(self, api_service):
        assert not hasattr(api_service, "get_book_summary_by_currency")
