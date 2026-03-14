"""Tests for OTMSignal Pydantic model."""
import pytest
from datetime import datetime, timezone
from uuid import uuid4
from pydantic import ValidationError
from coding.core.strategy.otm.models.otm_signal import OTMSignal


def _make_signal(**overrides) -> OTMSignal:
    """Helper: build a valid OTMSignal with sensible defaults."""
    defaults = dict(
        signal_id=str(uuid4()),
        generated_at=datetime.now(timezone.utc),
        asset="BTC",
        instrument_name="BTC-28MAR25-95000-C",
        direction="call",
        strike=95000.0,
        expiry="28MAR25",
        dte=14,
        delta=0.28,
        gamma=0.000012,
        vega=45.0,
        theta=-18.0,
        mark_iv=0.65,
        entry_premium=320.0,
        underlying_price=87500.0,
        gate1_passed=True,
        gate2_score=72.0,
        gate3_call_score=68.0,
        gate3_put_score=32.0,
        gate3_directional_score=68.0,
        conviction_score=70.0,
        d1_d7_score=0.6,
        d2_score=0.4,
        d3_score=0.3,
        d4_score=0.2,
        d6_d9_score=0.5,
        d8_score=0.1,
        d10_score=0.3,
        ris_score=0.2,
        position_usd=450.0,
        p_win_prior=0.40,
        kelly_fraction=0.025,
        take_profit_multiple=3.0,
        stop_loss_pct=0.70,
        time_stop_dte=7,
        vega_theta_ratio=2.5,
        gamma_premium_ratio=0.0000375,
        breakeven_price=95320.0,
        expiry_category="medium",
        regime_flag="bull",
        gate2_suppressed=False,
    )
    defaults.update(overrides)
    return OTMSignal(**defaults)


def test_valid_signal_creates_successfully():
    signal = _make_signal()
    assert signal.asset == "BTC"
    assert signal.direction == "call"


def test_asset_must_be_btc_or_eth():
    with pytest.raises(ValidationError):
        _make_signal(asset="SOL")


def test_direction_must_be_call_or_put():
    with pytest.raises(ValidationError):
        _make_signal(direction="buy")


def test_expiry_category_values():
    for cat in ("short", "medium", "long"):
        s = _make_signal(expiry_category=cat)
        assert s.expiry_category == cat
    with pytest.raises(ValidationError):
        _make_signal(expiry_category="weekly")


def test_regime_flag_values():
    for flag in ("bull", "bear", "neutral"):
        s = _make_signal(regime_flag=flag)
        assert s.regime_flag == flag
    with pytest.raises(ValidationError):
        _make_signal(regime_flag="sideways")


def test_gate2_score_range():
    _make_signal(gate2_score=0.0)
    _make_signal(gate2_score=100.0)
    with pytest.raises(ValidationError):
        _make_signal(gate2_score=-1.0)
    with pytest.raises(ValidationError):
        _make_signal(gate2_score=101.0)


def test_gate3_scores_range():
    with pytest.raises(ValidationError):
        _make_signal(gate3_call_score=150.0)


def test_sub_signal_scores_are_minus1_to_1():
    with pytest.raises(ValidationError):
        _make_signal(d1_d7_score=2.0)
    with pytest.raises(ValidationError):
        _make_signal(d2_score=-1.5)


def test_dte_must_be_positive():
    with pytest.raises(ValidationError):
        _make_signal(dte=0)


def test_signal_serializes_to_dict():
    signal = _make_signal()
    d = signal.model_dump()
    assert "signal_id" in d
    assert "conviction_score" in d


def test_eth_signal_d10_is_zero():
    signal = _make_signal(asset="ETH", d10_score=0.0)
    assert signal.d10_score == 0.0
