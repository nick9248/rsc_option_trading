import pytest
from unittest.mock import patch, MagicMock
from coding.service.strategy.otm.fetchers.stablecoin_fetcher import StablecoinFetcher
from coding.service.strategy.otm.fetchers.ibit_fetcher import IBITFetcher


# ── StablecoinFetcher ─────────────────────────────────────────────────────────

@pytest.fixture
def stablecoin_fetcher():
    return StablecoinFetcher()


def test_stablecoin_parse_valid_response(stablecoin_fetcher):
    raw = {"data": [{"inflow_usd": 500_000_000, "total_supply": 50_000_000_000}]}
    result = stablecoin_fetcher._parse_inflow_pct(raw)
    assert isinstance(result, float)
    assert abs(result - 1.0) < 0.001  # 500M / 50B * 100 = 1.0%


def test_stablecoin_parse_missing_key_returns_none(stablecoin_fetcher):
    result = stablecoin_fetcher._parse_inflow_pct({"data": []})
    assert result is None


@patch("coding.service.strategy.otm.fetchers.stablecoin_fetcher.requests.get")
def test_stablecoin_fetch_returns_float(mock_get, stablecoin_fetcher):
    mock_get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"data": [{"inflow_usd": 200_000_000, "total_supply": 100_000_000_000}]},
    )
    result = stablecoin_fetcher.fetch_inflow_pct()
    assert result == pytest.approx(0.2, abs=0.001)


@patch("coding.service.strategy.otm.fetchers.stablecoin_fetcher.requests.get")
def test_stablecoin_fetch_returns_none_on_error(mock_get, stablecoin_fetcher):
    mock_get.return_value = MagicMock(status_code=403)
    result = stablecoin_fetcher.fetch_inflow_pct()
    assert result is None


# ── IBITFetcher ───────────────────────────────────────────────────────────────

@pytest.fixture
def ibit_fetcher():
    return IBITFetcher()


def test_ibit_parse_valid_response(ibit_fetcher):
    raw = {"data": {"put_call_ratio": 0.85}}
    result = ibit_fetcher._parse_pc_ratio(raw)
    assert result == pytest.approx(0.85)


def test_ibit_parse_missing_key_returns_none(ibit_fetcher):
    result = ibit_fetcher._parse_pc_ratio({"data": {}})
    assert result is None


@patch("coding.service.strategy.otm.fetchers.ibit_fetcher.requests.get")
def test_ibit_fetch_returns_float(mock_get, ibit_fetcher):
    mock_get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"data": {"put_call_ratio": 0.72}},
    )
    result = ibit_fetcher.fetch_pc_ratio()
    assert result == pytest.approx(0.72)


@patch("coding.service.strategy.otm.fetchers.ibit_fetcher.requests.get")
def test_ibit_fetch_returns_none_on_http_error(mock_get, ibit_fetcher):
    mock_get.return_value = MagicMock(status_code=404)
    result = ibit_fetcher.fetch_pc_ratio()
    assert result is None


@patch("coding.service.strategy.otm.fetchers.ibit_fetcher.requests.get")
def test_ibit_fetch_returns_none_on_exception(mock_get, ibit_fetcher):
    mock_get.side_effect = Exception("Connection timeout")
    result = ibit_fetcher.fetch_pc_ratio()
    assert result is None
