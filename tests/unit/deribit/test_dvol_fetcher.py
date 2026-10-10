import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from coding.service.deribit.dvol_fetcher import DVOLFetcher


@pytest.fixture
def fetcher():
    return DVOLFetcher()


def _mock_deribit_response(values: list) -> dict:
    """Build a Deribit-style get_volatility_index_data response.
    API returns OHLC rows: [timestamp_ms, open, high, low, close].
    We use close (index 4) as the DVOL value.
    """
    return {
        "result": {
            "data": [[int(datetime(2025, 1, d+1, tzinfo=timezone.utc).timestamp() * 1000),
                      v, v, v, v]  # open=high=low=close=v for simplicity
                     for d, v in enumerate(values)]
        }
    }


def test_parse_dvol_response_returns_list_of_tuples(fetcher):
    raw = _mock_deribit_response([55.1, 60.2, 48.3])
    result = fetcher._parse_response(raw)
    assert len(result) == 3
    assert all(isinstance(ts, datetime) and isinstance(v, float) for ts, v in result)


def test_parse_dvol_response_empty_data(fetcher):
    raw = {"result": {"data": []}}
    result = fetcher._parse_response(raw)
    assert result == []


def test_parse_dvol_response_invalid_structure(fetcher):
    with pytest.raises(KeyError):
        fetcher._parse_response({"result": {}})


def test_build_url_btc(fetcher):
    url = fetcher._build_url("BTC", 1_000_000, 2_000_000)
    assert "currency=BTC" in url
    assert "1000000" in url
    assert "2000000" in url


def test_build_url_eth(fetcher):
    url = fetcher._build_url("ETH", 1_000_000, 2_000_000)
    assert "currency=ETH" in url


def test_build_url_invalid_asset(fetcher):
    with pytest.raises(ValueError):
        fetcher._build_url("SOL", 0, 1)


@patch("coding.service.deribit.dvol_fetcher.requests.get")
def test_fetch_history_returns_list(mock_get, fetcher):
    # Response with no continuation token — single batch, no pagination loop
    response_data = _mock_deribit_response([50.0, 55.0, 60.0])
    # No "continuation" key in result → loop terminates after first batch
    mock_get.return_value = MagicMock(
        status_code=200,
        json=lambda: response_data,
    )
    result = fetcher.fetch_history("BTC", months=3)
    assert len(result) == 3
    assert all(isinstance(v, float) for _, v in result)
