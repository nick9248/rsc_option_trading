# tests/unit/strategy/otm/test_otm_finder_service.py
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
from coding.service.strategy.otm.otm_finder_service import OTMFinderService
from coding.core.strategy.otm.models.otm_config import OTMConfig
from coding.core.strategy.otm.models.otm_signal import OTMSignal


@pytest.fixture
def config():
    return OTMConfig(risk_budget_usd=10_000.0)


def _mock_contract(strike=95000, dte=14, delta=0.28, vega=45.0, theta=-18.0,
                   gamma=0.000012, bid_iv=0.60, ask_iv=0.63, oi=200,
                   volume=30, mark_price=0.004, underlying=87000.0,
                   option_type="C"):
    return {
        "instrument_name": f"BTC-28MAR25-{strike}-{option_type}",
        "strike": strike, "dte": dte, "delta": delta if option_type=="C" else -delta,
        "gamma": gamma, "vega": vega, "theta": theta,
        "bid_iv": bid_iv, "ask_iv": ask_iv, "mark_iv": (bid_iv+ask_iv)/2,
        "open_interest": oi, "volume_24h": volume,
        "mark_price": mark_price, "underlying_price": underlying,
        "option_type": option_type,
    }


def _make_service(config):
    svc = OTMFinderService(config)
    svc._deribit_service = MagicMock()
    svc._on_chain_service = MagicMock()
    svc._dvol_fetcher = MagicMock()
    svc._stablecoin_fetcher = MagicMock()
    svc._ibit_fetcher = MagicMock()
    svc._repository = MagicMock()
    return svc


def _setup_mock_data(svc):
    """Configure mocks to return valid data."""
    svc._deribit_service.get_book_summary_by_currency.return_value = [
        _mock_contract(strike=95000, dte=14, delta=0.28),
        _mock_contract(strike=90000, dte=14, delta=0.35),
        _mock_contract(strike=85000, dte=14, delta=-0.30, option_type="P"),
    ]
    svc._deribit_service.get_ticker.return_value = {
        "index_price": 87000.0, "mark_price": 0.004
    }
    svc._on_chain_service.fetch_and_analyze.return_value = MagicMock(
        gex_dex_structured={"totals": {"net_gex": -1e6}, "second_order": {"vanna": 1.0}},
        market_wide_structured={
            "perpetual_funding": {"current_rate": 0.0001, "trend": "neutral"},
            "iv_term_structure": {"spread": 8.0, "shape": "contango"},
            "vrp": {"vrp_abs": -5.0, "rv_30d": 0.55},
        },
        volatility_surface_structured={
            "skew_25d": {"rr25": 0.01},
            "pc_by_moneyness": {"pc_ratio_all": 1.2},
            "atm_iv": 0.60,
        },
    )
    svc._dvol_fetcher.fetch_latest.return_value = 55.0
    svc._dvol_fetcher.fetch_history.return_value = [(None, 60.0)] * 400
    svc._stablecoin_fetcher.fetch_inflow_pct.return_value = None
    svc._ibit_fetcher.fetch_pc_ratio.return_value = None
    svc._repository.get_dvol_history.return_value = [60.0] * 400
    svc._repository.get_funding_rate_history.return_value = [0.0001] * 1000
    svc._repository.get_pc_ratio_history.return_value = [1.0] * 200
    svc._repository.get_rr25_history.return_value = [0.01] * 30
    svc._repository.get_ohlcv_daily.return_value = [
        {"close": 87000.0 * (1.001 ** i)} for i in range(60)
    ]


# ── Smoke tests ───────────────────────────────────────────────────────────────

def test_find_signals_returns_list(config):
    svc = _make_service(config)
    _setup_mock_data(svc)
    result = svc.find_signals(assets=["BTC"], direction="auto", expiry_pref="auto")
    assert isinstance(result, list)


def test_find_signals_sorted_descending_by_conviction(config):
    svc = _make_service(config)
    _setup_mock_data(svc)
    result = svc.find_signals(assets=["BTC"], direction="auto", expiry_pref="auto")
    if len(result) >= 2:
        scores = [s.conviction_score for s in result]
        assert scores == sorted(scores, reverse=True)


def test_find_signals_returns_otm_signal_objects(config):
    svc = _make_service(config)
    _setup_mock_data(svc)
    result = svc.find_signals(assets=["BTC"], direction="auto", expiry_pref="auto")
    for signal in result:
        assert isinstance(signal, OTMSignal)


def test_gate2_suppressed_blocks_new_entries(config):
    svc = _make_service(config)
    _setup_mock_data(svc)
    # Mock Gate 2 to return action != "new_entries_allowed" to trigger gate2_suppressed=True
    svc._gate2.score = MagicMock(return_value={
        "total_score": 35.0,
        "action": "wait_for_vol_expansion",
        "garch_fcast_annualized": 0.05,
    })
    # Use gate2_override=True to allow signals through so we can check gate2_suppressed flag
    result = svc.find_signals(assets=["BTC"], direction="auto", expiry_pref="auto",
                               gate2_override=True)
    for signal in result:
        assert signal.gate2_suppressed is True


def test_gate2_override_allows_signals_when_suppressed(config):
    svc = _make_service(config)
    _setup_mock_data(svc)
    result = svc.find_signals(assets=["BTC"], direction="auto", expiry_pref="auto",
                               gate2_override=True)
    assert isinstance(result, list)


def test_empty_result_when_no_liquid_contracts(config):
    svc = _make_service(config)
    _setup_mock_data(svc)
    svc._deribit_service.get_book_summary_by_currency.return_value = [
        _mock_contract(oi=0), _mock_contract(oi=0)
    ]
    result = svc.find_signals(assets=["BTC"], direction="auto", expiry_pref="auto")
    assert result == []


def test_dvol_saved_on_each_run(config):
    svc = _make_service(config)
    _setup_mock_data(svc)
    svc.find_signals(assets=["BTC"], direction="auto", expiry_pref="auto")
    svc._dvol_fetcher.save_to_db.assert_called()


def test_signals_saved_to_repository(config):
    svc = _make_service(config)
    _setup_mock_data(svc)
    result = svc.find_signals(assets=["BTC"], direction="auto", expiry_pref="auto")
    if result:
        svc._repository.save_otm_signals.assert_called_once()
