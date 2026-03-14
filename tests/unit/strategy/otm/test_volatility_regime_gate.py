# tests/unit/strategy/otm/test_volatility_regime_gate.py
import pytest
from coding.core.strategy.otm.signals.volatility_regime_gate import VolatilityRegimeGate
from coding.core.strategy.otm.models.otm_config import OTMConfig


@pytest.fixture
def config():
    return OTMConfig(risk_budget_usd=10_000.0)


@pytest.fixture
def gate(config):
    return VolatilityRegimeGate(config)


def _make_ohlcv(n: int = 200) -> list:
    import random; random.seed(99)
    price = 50000.0
    rows = []
    for _ in range(n):
        change = random.gauss(0, 0.02)
        close = price * (1 + change)
        rows.append({"open": price, "high": max(price, close)*1.005,
                     "low": min(price, close)*0.995, "close": close, "volume": 1000.0})
        price = close
    return rows


# ── V1: DVOL percentile ──────────────────────────────────────────────────────

def test_v1_score_100_when_dvol_low_and_below_floor(gate):
    # 200 low values + 200 high values → median ~55, std ~15, floor ~70
    history = [40.0 + i*0.1 for i in range(200)] + [70.0 + i*0.1 for i in range(200)]
    score = gate._score_v1(38.0, history)   # below 30th pctile AND below floor
    assert score == 100.0

def test_v1_score_0_when_dvol_above_floor(gate):
    history = [60.0] * 400   # all same; std=0; floor=60+0=60
    score = gate._score_v1(85.0, history)   # above floor
    assert score == 0.0

def test_v1_score_50_when_history_under_90_days(gate):
    assert gate._score_v1(60.0, [60.0] * 80) == 50.0

def test_v1_score_50_when_history_empty(gate):
    assert gate._score_v1(60.0, []) == 50.0

def test_v1_insufficient_history_logs_warning(gate, caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        gate._score_v1(60.0, [55.0] * 50)
    assert any("insufficient" in m.lower() or "dvol" in m.lower() for m in caplog.messages)

def test_v1_degraded_history_90_to_365_logs_warning(gate, caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        gate._score_v1(55.0, [60.0] * 150)
    assert any("degraded" in m.lower() for m in caplog.messages)


# ── VRP component ────────────────────────────────────────────────────────────

def test_vrp_score_100_when_vrp_below_zero(gate):
    assert gate._score_vrp(atm_iv_30d=0.50, rv_30d_parkinson=0.55) == 100.0

def test_vrp_score_100_when_vrp_below_threshold(gate):
    # VRP = (0.50-0.46)*100 = +4 pts < 5 threshold
    assert gate._score_vrp(atm_iv_30d=0.50, rv_30d_parkinson=0.46) == 100.0

def test_vrp_score_0_when_vrp_above_threshold(gate):
    # VRP = (0.50-0.40)*100 = +10 pts > 5
    assert gate._score_vrp(atm_iv_30d=0.50, rv_30d_parkinson=0.40) == 0.0


# ── GARCH component ──────────────────────────────────────────────────────────

def test_garch_score_100_when_forecast_exceeds_iv_ratio(gate):
    # 0.75/0.65=1.15 > 1.10
    assert gate._score_garch(garch_fcast_annualized=0.75, atm_iv_30d=0.65) == 100.0

def test_garch_score_0_when_forecast_below_ratio(gate):
    # 0.55/0.65=0.846 < 1.10
    assert gate._score_garch(garch_fcast_annualized=0.55, atm_iv_30d=0.65) == 0.0

def test_garch_score_50_when_fewer_than_90_candles(gate):
    assert gate._score_garch_from_ohlcv(_make_ohlcv(50)) == 50.0

def test_garch_fit_returns_positive_float_with_sufficient_data(gate):
    result = gate._fit_gjr_garch(_make_ohlcv(200))
    assert result is not None
    assert isinstance(result, float) and result > 0


# ── V3: IV term structure ─────────────────────────────────────────────────────

def test_v3_100_in_contango(gate):
    assert gate._score_v3({"spread": 8.0}) == 100.0

def test_v3_50_flat(gate):
    assert gate._score_v3({"spread": 2.0}) == 50.0

def test_v3_25_shallow_backwardation(gate):
    assert gate._score_v3({"spread": -10.0}) == 25.0

def test_v3_0_deep_backwardation(gate):
    assert gate._score_v3({"spread": -20.0}) == 0.0

def test_v3_50_when_none(gate):
    assert gate._score_v3(None) == 50.0


# ── Composite ────────────────────────────────────────────────────────────────

def test_composite_weighted_average(gate):
    # 0.30*100 + 0.40*80 + 0.30*50 = 77
    assert gate._combine_scores(100.0, 80.0, 50.0) == pytest.approx(77.0, abs=0.1)

def test_action_new_entries_allowed(gate):
    assert gate._determine_action(55.0) == "new_entries_allowed"

def test_action_no_new_entries(gate):
    assert gate._determine_action(35.0) == "no_new_entries"

def test_action_partial_exit(gate):
    assert gate._determine_action(25.0) == "partial_exit"

def test_score_method_returns_required_keys(gate):
    result = gate.score(
        dvol_history=[60.0]*400, current_dvol=45.0,
        atm_iv_30d=0.60, rv_30d_parkinson=0.55,
        ohlcv_daily=_make_ohlcv(200),
        term_structure_data={"spread": 8.0},
    )
    for key in ("total_score", "action", "v1_score", "v2v4_score", "v3_score",
                "garch_fcast_annualized"):
        assert key in result
    assert 0.0 <= result["total_score"] <= 100.0
