"""
Unit tests for SnapshotService.

Locks in the fix for the confirmed pricing-basis bug: USD conversions of
bid/mark/mid/ask must use the INDEX price, never the per-expiry FUTURE price
(`underlying_price`). See coding/service/deribit/deribit_api_service.py
get_option_chain_snapshot docstring for the full rule.
"""

from unittest.mock import MagicMock

import pytest

from coding.service.snapshot.snapshot_service import SnapshotService


def _instrument(
    instrument_name="BTC-28AUG26-64000-C",
    bid_price=0.0480,
    ask_price=0.0495,
    mark_price=0.0488,
    mid_price=0.04875,
    underlying_price=64200.0,
    volume=5.0,
):
    return {
        "instrument_name": instrument_name,
        "bid_price": bid_price,
        "ask_price": ask_price,
        "mark_price": mark_price,
        "mid_price": mid_price,
        "underlying_price": underlying_price,
        "open_interest": 100.0,
        "volume": volume,
        "volume_usd": 0.0,
        "creation_timestamp": 1700000000000,
    }


class TestTransformToModifiedFormatUsesIndexPrice:
    """
    This is the failing-test-first proof of the bug: transform_to_modified_
    format must price bid/mark/mid/ask in USD using index_price, not the
    future (underlying_price).
    """

    def test_usd_prices_use_index_price_when_provided(self):
        service = SnapshotService()
        index_price = 63900.0
        underlying_price = 64200.0  # future, trades above index (contango)

        data = [_instrument(underlying_price=underlying_price)]
        # index_price is attached per-item, exactly as get_filtered_instruments
        # now injects it (mirrors the existing underlying_price injection).
        data[0]["index_price"] = index_price

        result = service.transform_to_modified_format(data)
        row = result[0]

        expected_ask_usd = round(0.0495 * index_price, 4)
        wrong_ask_usd = round(0.0495 * underlying_price, 4)

        assert row["ask_price_usd"] == expected_ask_usd
        assert row["ask_price_usd"] != wrong_ask_usd
        assert row["bid_price_usd"] == round(0.0480 * index_price, 4)
        assert row["mark_price_usd"] == round(0.0488 * index_price, 4)
        assert row["mid_price_usd"] == round(0.04875 * index_price, 4)

        # underlying_price (the future) must still be reported, untouched.
        assert row["underlying_price"] == underlying_price

    def test_falls_back_to_underlying_price_with_warning_if_index_missing(self, caplog):
        """Defensive fallback for legacy callers that never populate index_price."""
        service = SnapshotService()
        underlying_price = 64200.0
        data = [_instrument(underlying_price=underlying_price)]
        # No index_price key at all (legacy raw data).

        with caplog.at_level("WARNING"):
            result = service.transform_to_modified_format(data)

        row = result[0]
        assert row["ask_price_usd"] == round(0.0495 * underlying_price, 4)
        assert any("index_price" in message for message in caplog.messages)


class TestGetFilteredInstrumentsInjectsIndexPrice:
    """get_filtered_instruments must fetch and attach index_price per item."""

    def test_index_price_attached_to_every_filtered_instrument(self):
        mock_api = MagicMock()
        mock_api.get_book_summary.return_value = [
            _instrument("BTC-28AUG26-64000-C"),
            _instrument("BTC-28AUG26-65000-P"),
        ]
        mock_api.get_index_price.return_value = 63900.0

        service = SnapshotService(mock_api)
        result = service.get_filtered_instruments(
            currency="BTC",
            expirations=["28AUG26"],
        )

        assert len(result) == 2
        for item in result:
            assert item["index_price"] == 63900.0

        mock_api.get_index_price.assert_called_once_with(currency="BTC")

    def test_index_price_fetch_failure_does_not_crash(self):
        """If index_price fetch fails, filtering must still succeed (fall back to None)."""
        mock_api = MagicMock()
        mock_api.get_book_summary.return_value = [_instrument()]
        mock_api.get_index_price.side_effect = Exception("network error")

        service = SnapshotService(mock_api)
        result = service.get_filtered_instruments(
            currency="BTC",
            expirations=["28AUG26"],
        )

        assert len(result) == 1
        assert result[0].get("index_price") is None
