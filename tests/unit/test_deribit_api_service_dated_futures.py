"""Tests for DeribitApiService.get_dated_futures_marks."""
from unittest.mock import MagicMock

from coding.service.deribit.deribit_api_service import DeribitApiService


def _service(instruments, tickers):
    service = DeribitApiService.__new__(DeribitApiService)
    service.get_instruments = MagicMock(return_value=instruments)

    def get_ticker(name):
        value = tickers[name]
        if isinstance(value, Exception):
            raise value
        return value

    service.get_ticker = MagicMock(side_effect=get_ticker)
    return service


def test_returns_mark_and_index_price_of_each_dated_future_and_skips_the_perpetual():
    service = _service(
        [{"instrument_name": "BTC-PERPETUAL"}, {"instrument_name": "BTC-31DEC26"}],
        {"BTC-31DEC26": {"mark_price": 70000.0, "index_price": 68000.0}},
    )

    assert service.get_dated_futures_marks("BTC") == [
        {"instrument_name": "BTC-31DEC26", "mark_price": 70000.0, "index_price": 68000.0}
    ]
    service.get_instruments.assert_called_once_with(currency="BTC", kind="future", expired=False)


def test_a_future_whose_ticker_fails_is_skipped_and_the_others_are_kept():
    service = _service(
        [{"instrument_name": "BTC-31DEC26"}, {"instrument_name": "BTC-26MAR27"}],
        {"BTC-31DEC26": RuntimeError("timeout"),
         "BTC-26MAR27": {"mark_price": 71000.0, "index_price": 68000.0}},
    )

    names = [mark["instrument_name"] for mark in service.get_dated_futures_marks("BTC")]

    assert names == ["BTC-26MAR27"]
