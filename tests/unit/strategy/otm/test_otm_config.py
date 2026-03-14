import pytest
from pydantic import ValidationError
from coding.core.strategy.otm.models.otm_config import OTMConfig


def test_default_config_creates_successfully():
    config = OTMConfig(risk_budget_usd=10_000.0)
    assert config.risk_budget_usd == 10_000.0


def test_default_gate1_thresholds():
    config = OTMConfig(risk_budget_usd=10_000.0)
    assert config.max_bid_ask_spread_relative == 0.08
    assert config.max_bid_ask_spread_absolute == 4.0
    assert config.min_volume_oi_ratio == 0.05
    assert config.min_oi_btc == 50
    assert config.min_oi_eth == 200
    assert config.tx_cost_floor_multiplier == 5.0


def test_default_gate2_thresholds():
    config = OTMConfig(risk_budget_usd=10_000.0)
    assert config.gate2_suppress_threshold == 40.0
    assert config.gate2_position_exit_threshold == 30.0
    assert config.dvol_percentile_threshold == 30.0
    assert config.dvol_lookback_months == 36
    assert config.dvol_floor_std_multiplier == 1.0
    assert config.vrp_cheap_threshold == 5.0
    assert config.garch_iv_ratio_threshold == 1.10
    assert config.term_structure_contango_threshold == 5.0
    assert config.term_structure_shallow_back_threshold == -5.0
    assert config.term_structure_deep_back_threshold == -15.0


def test_default_kelly_priors():
    config = OTMConfig(risk_budget_usd=10_000.0)
    assert config.p_win_priors["40_60"] == 0.35
    assert config.p_win_priors["90_100"] == 0.50
    assert config.avg_return_priors["90_100"] == 3.0   # capped at 3×


def test_risk_budget_required():
    with pytest.raises(ValidationError):
        OTMConfig()   # missing required field


def test_risk_budget_must_be_positive():
    with pytest.raises(ValidationError):
        OTMConfig(risk_budget_usd=-100.0)


def test_max_single_trade_pct_must_be_positive():
    with pytest.raises(ValidationError):
        OTMConfig(risk_budget_usd=10_000.0, max_single_trade_pct=0.0)


def test_kelly_divisor_must_be_positive():
    with pytest.raises(ValidationError):
        OTMConfig(risk_budget_usd=10_000.0, kelly_divisor=0.0)


def test_custom_overrides_work():
    config = OTMConfig(
        risk_budget_usd=50_000.0,
        min_oi_btc=100,
        gate2_suppress_threshold=50.0,
    )
    assert config.min_oi_btc == 100
    assert config.gate2_suppress_threshold == 50.0


def test_config_is_immutable():
    config = OTMConfig(risk_budget_usd=10_000.0)
    with pytest.raises(Exception):  # pydantic v2 frozen raises ValidationError (subclass of Exception)
        config.risk_budget_usd = 999.0
