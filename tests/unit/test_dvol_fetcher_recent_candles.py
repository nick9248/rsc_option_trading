"""Tests for DVOLFetcher.fetch_recent_completed_candles: the candle still forming is
never returned, because its close keeps changing until the hour ends."""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from coding.service.deribit.dvol_fetcher import DVOLFetcher

NOW = datetime(2026, 10, 10, 23, 5, tzinfo=timezone.utc)


def _ms(hour, day=10):
    return int(datetime(2026, 10, day, hour, 0, tzinfo=timezone.utc).timestamp() * 1000)


def _response(candle_hours):
    """Deribit OHLC rows: [start_ms, open, high, low, close]."""
    response = MagicMock(status_code=200)
    response.json.return_value = {
        "result": {"data": [[_ms(hour), 1.0, 2.0, 0.5, 30.0 + hour] for hour in candle_hours]}
    }
    return response


def test_the_candle_still_forming_is_dropped():
    with patch("coding.service.deribit.dvol_fetcher.requests.get", return_value=_response([21, 22, 23])):
        candles = DVOLFetcher().fetch_recent_completed_candles("BTC", hours=48, now=NOW)

    # 23:00 is the current hour (it is 23:05): not complete yet
    assert [start.hour for start, _ in candles] == [21, 22]


def test_values_are_the_candle_close_and_timestamps_are_utc_aware():
    with patch("coding.service.deribit.dvol_fetcher.requests.get", return_value=_response([22])):
        candles = DVOLFetcher().fetch_recent_completed_candles("BTC", hours=48, now=NOW)

    assert candles == [(datetime(2026, 10, 10, 22, 0, tzinfo=timezone.utc), 52.0)]
    assert candles[0][0].tzinfo is not None


def test_result_is_sorted_oldest_first():
    with patch("coding.service.deribit.dvol_fetcher.requests.get", return_value=_response([22, 20, 21])):
        candles = DVOLFetcher().fetch_recent_completed_candles("ETH", now=NOW)

    assert [start.hour for start, _ in candles] == [20, 21, 22]


def test_the_request_covers_the_requested_window_from_the_hour_boundary():
    with patch("coding.service.deribit.dvol_fetcher.requests.get", return_value=_response([22])) as mock_get:
        DVOLFetcher().fetch_recent_completed_candles("BTC", hours=48, now=NOW)

    url = mock_get.call_args.args[0]
    assert f"start_timestamp={_ms(23, day=8)}" in url
    assert "resolution=3600" in url and "currency=BTC" in url


def test_http_error_returns_none():
    failed = MagicMock(status_code=500)
    with patch("coding.service.deribit.dvol_fetcher.requests.get", return_value=failed):
        assert DVOLFetcher().fetch_recent_completed_candles("BTC", now=NOW) is None


def test_network_error_returns_none():
    with patch("coding.service.deribit.dvol_fetcher.requests.get", side_effect=OSError("down")):
        assert DVOLFetcher().fetch_recent_completed_candles("BTC", now=NOW) is None
