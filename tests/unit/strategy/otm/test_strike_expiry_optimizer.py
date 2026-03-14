# tests/unit/strategy/otm/test_strike_expiry_optimizer.py
import pytest
from coding.core.strategy.otm.signals.strike_expiry_optimizer import StrikeExpiryOptimizer
from coding.core.strategy.otm.models.otm_config import OTMConfig


@pytest.fixture
def config():
    return OTMConfig(risk_budget_usd=10_000.0)

@pytest.fixture
def opt(config):
    return StrikeExpiryOptimizer(config)


def _contract(strike=95000, dte=14, delta=0.28, vega=45.0, theta=-18.0,
              gamma=0.000012, mark_price=0.004, underlying=87000.0,
              direction="call"):
    entry_premium = mark_price * underlying
    return {
        "strike": strike, "dte": dte, "delta": delta, "vega": vega,
        "theta": theta, "gamma": gamma, "mark_price": mark_price,
        "underlying_price": underlying, "entry_premium": entry_premium,
        "direction": direction,
    }


# ── Delta range filter ────────────────────────────────────────────────────────

def test_delta_filter_passes_directional_range(opt):
    c = _contract(delta=0.28)
    assert opt._passes_delta_filter(c, mode="directional") is True

def test_delta_filter_fails_too_high(opt):
    c = _contract(delta=0.40)
    assert opt._passes_delta_filter(c, mode="directional") is False

def test_delta_filter_fails_too_low(opt):
    c = _contract(delta=0.15)
    assert opt._passes_delta_filter(c, mode="directional") is False

def test_delta_filter_event_range(opt):
    c = _contract(delta=0.15)
    assert opt._passes_delta_filter(c, mode="event") is True

def test_eth_call_avoid_range_unless_high_score(opt):
    c = _contract(delta=0.28, direction="call")
    assert opt._passes_eth_call_filter(c, asset="ETH", call_score=60.0) is False
    assert opt._passes_eth_call_filter(c, asset="ETH", call_score=85.0) is True

def test_eth_call_avoid_not_applied_outside_range(opt):
    c = _contract(delta=0.18, direction="call")
    assert opt._passes_eth_call_filter(c, asset="ETH", call_score=60.0) is True


# ── Expiry category ───────────────────────────────────────────────────────────

def test_expiry_category_short(opt):
    assert opt._classify_expiry(3) == "short"

def test_expiry_category_medium(opt):
    assert opt._classify_expiry(14) == "medium"
    assert opt._classify_expiry(7) == "medium"

def test_expiry_category_long(opt):
    assert opt._classify_expiry(45) == "long"
    assert opt._classify_expiry(30) == "long"

def test_expiry_category_boundary_1(opt):
    assert opt._classify_expiry(1) == "short"

def test_expiry_category_over_90_raises(opt):
    with pytest.raises(ValueError):
        opt._classify_expiry(95)


# ── Vega/Theta ratio ──────────────────────────────────────────────────────────

def test_vega_theta_ratio_medium_passes(opt):
    score = opt._score_vega_theta(_contract(vega=45.0, theta=-18.0, dte=14))
    assert score > 0.0

def test_vega_theta_ratio_short_lower_threshold(opt):
    c = _contract(dte=3, vega=5.0, theta=-100.0)  # ratio=0.05 — at threshold
    score = opt._score_vega_theta(c)
    assert score >= 0.0

def test_vega_theta_zero_theta_returns_zero(opt):
    c = _contract(theta=0.0)
    score = opt._score_vega_theta(c)
    assert score == 0.0


# ── Breakeven distance ────────────────────────────────────────────────────────

def test_breakeven_within_2x_move(opt):
    c = _contract(strike=92000, mark_price=0.004, underlying=87000)
    score = opt._score_breakeven(c, garch_fcast_30d=0.05)
    assert score > 0

def test_breakeven_penalized_beyond_2x_move(opt):
    c = _contract(strike=120000, mark_price=0.001, underlying=87000)
    score = opt._score_breakeven(c, garch_fcast_30d=0.05)
    assert score < 1.0


# ── Max pain tiebreaker ───────────────────────────────────────────────────────

def test_max_pain_closer_candidate_wins(opt):
    c1 = _contract(strike=95000)
    c2 = _contract(strike=99000)
    max_pain = 96000
    assert opt._max_pain_tiebreak(c1, c2, max_pain) == c1

def test_max_pain_none_returns_first(opt):
    c1 = _contract(strike=95000)
    c2 = _contract(strike=99000)
    assert opt._max_pain_tiebreak(c1, c2, None) == c1


# ── Full select method ────────────────────────────────────────────────────────

def test_select_returns_sorted_list(opt):
    contracts = [
        _contract(strike=92000, dte=14, delta=0.30, vega=50.0, theta=-15.0),
        _contract(strike=96000, dte=14, delta=0.22, vega=30.0, theta=-12.0),
        _contract(strike=100000, dte=14, delta=0.18, vega=20.0, theta=-10.0),
    ]
    result = opt.select(
        contracts=contracts, direction="call", call_score=70.0, put_score=30.0,
        gate2_score=65.0, garch_fcast_30d=0.05, max_pain_strike=93000,
        spot_price=87000.0, asset="BTC",
    )
    assert all("gate4_score" in c for c in result)
    scores = [c["gate4_score"] for c in result]
    assert scores == sorted(scores, reverse=True)

def test_select_filters_out_of_delta_range(opt):
    contracts = [
        _contract(strike=92000, delta=0.28),  # valid
        _contract(strike=80000, delta=0.05),  # too low delta → filtered
    ]
    result = opt.select(
        contracts=contracts, direction="call", call_score=70.0, put_score=30.0,
        gate2_score=65.0, garch_fcast_30d=0.05, max_pain_strike=None,
        spot_price=87000.0, asset="BTC",
    )
    assert len(result) == 1
    assert result[0]["strike"] == 92000

def test_select_returns_empty_when_no_valid_contracts(opt):
    contracts = [_contract(strike=80000, delta=0.05)]
    result = opt.select(
        contracts=contracts, direction="call", call_score=70.0, put_score=30.0,
        gate2_score=65.0, garch_fcast_30d=0.05, max_pain_strike=None,
        spot_price=87000.0, asset="BTC",
    )
    assert result == []
