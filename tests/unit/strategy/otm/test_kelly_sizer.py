# tests/unit/strategy/otm/test_kelly_sizer.py
import pytest
from coding.core.strategy.otm.scoring.kelly_sizer import KellySizer
from coding.core.strategy.otm.models.otm_config import OTMConfig


@pytest.fixture
def config():
    return OTMConfig(risk_budget_usd=10_000.0)

@pytest.fixture
def sizer(config):
    return KellySizer(config)


def test_conviction_score_50_50_blend(sizer):
    score = sizer.compute_conviction(gate2_score=80.0, gate3_directional_score=60.0)
    assert score == pytest.approx(70.0, abs=0.1)

def test_conviction_clamped_to_0_100(sizer):
    assert sizer.compute_conviction(0.0, 0.0) == 0.0
    assert sizer.compute_conviction(100.0, 100.0) == 100.0


def test_p_win_band_40_60(sizer):
    p_win, mult = sizer._lookup_priors(50.0)
    assert p_win == 0.35
    assert mult == 1.5

def test_p_win_band_60_75(sizer):
    p_win, mult = sizer._lookup_priors(70.0)
    assert p_win == 0.40
    assert mult == 2.0

def test_p_win_band_75_90(sizer):
    p_win, mult = sizer._lookup_priors(80.0)
    assert p_win == 0.45
    assert mult == 2.5

def test_p_win_band_over_90(sizer):
    p_win, mult = sizer._lookup_priors(95.0)
    assert p_win == 0.50
    assert mult == 3.0

def test_p_win_below_40_returns_none(sizer):
    result = sizer._lookup_priors(35.0)
    assert result is None

def test_return_multiple_capped_at_3(sizer):
    _, mult = sizer._lookup_priors(95.0)
    assert mult <= 3.0


def test_kelly_fraction_positive_ev(sizer):
    frac = sizer._compute_kelly_fraction(p_win=0.40, avg_return_multiple=2.0)
    assert frac == pytest.approx(0.10, abs=0.001)

def test_fractional_kelly_is_quarter(sizer):
    full_kelly = sizer._compute_kelly_fraction(0.40, 2.0)
    frac = sizer._apply_fractional_kelly(full_kelly)
    assert frac == pytest.approx(0.10 * 0.25, abs=0.001)

def test_kelly_fraction_capped_at_max_single_trade(sizer):
    result = sizer.compute_position_usd(
        gate2_score=100.0, gate3_directional_score=100.0,
        existing_same_direction_usd=0.0,
    )
    assert result["position_usd"] <= 10_000.0 * 0.10


def test_correlation_cap_reduces_new_position(sizer):
    result = sizer.compute_position_usd(
        gate2_score=80.0, gate3_directional_score=70.0,
        existing_same_direction_usd=800.0,
    )
    assert result["position_usd"] <= 200.0

def test_correlation_cap_skips_when_full(sizer):
    result = sizer.compute_position_usd(
        gate2_score=80.0, gate3_directional_score=70.0,
        existing_same_direction_usd=1000.0,
    )
    assert result["position_usd"] == 0.0
    assert "cap reached" in result.get("skip_reason", "")

def test_position_usd_zero_when_conviction_below_40(sizer):
    result = sizer.compute_position_usd(
        gate2_score=30.0, gate3_directional_score=30.0,
        existing_same_direction_usd=0.0,
    )
    assert result["position_usd"] == 0.0


def test_take_profit_short_dte_always_2x(sizer):
    tp = sizer.compute_take_profit(conviction_score=85.0, dte=3)
    assert tp == 2.0

def test_take_profit_medium_high_conviction(sizer):
    tp = sizer.compute_take_profit(conviction_score=80.0, dte=14)
    assert tp == 5.0

def test_take_profit_long_high_conviction(sizer):
    tp = sizer.compute_take_profit(conviction_score=80.0, dte=45)
    assert tp == 8.0

def test_take_profit_medium_low_conviction(sizer):
    tp = sizer.compute_take_profit(conviction_score=50.0, dte=14)
    assert tp == 2.0
