# tests/unit/strategy/otm/test_directional_scorer.py
import pytest
import numpy as np
from coding.core.strategy.otm.signals.directional_scorer import DirectionalScorer
from coding.core.strategy.otm.models.otm_config import OTMConfig


@pytest.fixture
def config():
    return OTMConfig(risk_budget_usd=10_000.0)

@pytest.fixture
def scorer(config):
    return DirectionalScorer(config)

def _ohlcv(n=60, trend="up"):
    price = 50000.0
    rows = []
    for _ in range(n):
        price *= 1.001 if trend == "up" else 0.999
        rows.append({"close": price})
    return rows


# ── D1+D7 ─────────────────────────────────────────────────────────────────────

def test_d1d7_bullish_negative_gex(scorer):
    gex = {"totals": {"net_gex": -2e6, "net_dex": 0}, "second_order": {"vanna": 1.0}}
    assert scorer._score_d1_d7(gex) > 0.0

def test_d1d7_bearish_positive_gex(scorer):
    gex = {"totals": {"net_gex": 2e6, "net_dex": 0}, "second_order": {"vanna": -1.0}}
    assert scorer._score_d1_d7(gex) < 0.0

def test_d1d7_neutral_zero_gex(scorer):
    gex = {"totals": {"net_gex": 0.0, "net_dex": 0}, "second_order": {"vanna": 0.0}}
    assert scorer._score_d1_d7(gex) == 0.0


# ── D2 ────────────────────────────────────────────────────────────────────────

def test_d2_bullish_low_funding_peak_shorts(scorer):
    history = [0.0001]*900 + [0.0008]*100
    assert scorer._score_d2(0.00005, history, spot_making_new_30d_low=True) > 0.0

def test_d2_bearish_high_funding_with_divergence(scorer):
    history = [0.0001]*900 + [0.0008]*100
    assert scorer._score_d2(0.0008, history, bearish_divergence=True) < 0.0

def test_d2_neutral_empty_history(scorer):
    assert scorer._score_d2(0.0001, []) == 0.0


# ── D3 ────────────────────────────────────────────────────────────────────────

def test_d3_bullish_low_z_score(scorer):
    history = [0.02 + 0.001*(i%5-2) for i in range(30)]
    assert scorer._score_d3(current_rr=0.01, rr25_history=history) > 0.0

def test_d3_bearish_high_z_score(scorer):
    history = [-0.01 + 0.001*(i%3-1) for i in range(30)]
    assert scorer._score_d3(current_rr=0.02, rr25_history=history) < 0.0

def test_d3_neutral_within_threshold(scorer):
    assert scorer._score_d3(0.01, [0.01]*30) == 0.0

def test_d3_neutral_insufficient_history(scorer):
    assert scorer._score_d3(0.01, [0.01]*5) == 0.0


# ── D4 ────────────────────────────────────────────────────────────────────────

def test_d4_bullish_high_pc_ratio(scorer):
    history = [1.0 + 0.01*i for i in range(100)]
    assert scorer._score_d4(1.95, history) > 0.0

def test_d4_bearish_low_pc_ratio(scorer):
    history = [1.0 + 0.01*i for i in range(100)]
    assert scorer._score_d4(1.0, history) < 0.0

def test_d4_neutral_mid_range(scorer):
    history = [1.0 + 0.01*i for i in range(100)]
    assert scorer._score_d4(1.50, history) == 0.0


# ── D6+D9 ─────────────────────────────────────────────────────────────────────

def test_d6d9_bullish_call_blocks_positive_dex(scorer):
    assert scorer._score_d6_d9({"blocks_detected": True, "direction": "call"},
                                dex_sign_flipped_positive=True) > 0.0

def test_d6d9_bearish_put_blocks_negative_dex(scorer):
    assert scorer._score_d6_d9({"blocks_detected": True, "direction": "put"},
                                dex_sign_flipped_negative=True) < 0.0

def test_d6d9_neutral_no_blocks(scorer):
    assert scorer._score_d6_d9({"blocks_detected": False}) == 0.0


# ── D8 ────────────────────────────────────────────────────────────────────────

def test_d8_bullish_large_inflow(scorer):    assert scorer._score_d8(0.8) > 0.0
def test_d8_bearish_large_outflow(scorer):   assert scorer._score_d8(-0.8) < 0.0
def test_d8_neutral_small(scorer):           assert scorer._score_d8(0.2) == 0.0
def test_d8_neutral_none(scorer):            assert scorer._score_d8(None) == 0.0


# ── D10 ───────────────────────────────────────────────────────────────────────

def test_d10_bullish_below_avg(scorer):      assert scorer._score_d10(0.5, 0.9) > 0.0
def test_d10_bearish_above_avg(scorer):      assert scorer._score_d10(1.3, 0.9) < 0.0
def test_d10_neutral_none(scorer):           assert scorer._score_d10(None, None) == 0.0


# ── RIS ───────────────────────────────────────────────────────────────────────

def test_ris_bullish_calls_cheap(scorer):
    assert scorer._score_ris(rr25_30d_mean=0.03, rr25_current=0.00) > 0.0

def test_ris_bearish_puts_cheap(scorer):
    assert scorer._score_ris(rr25_30d_mean=-0.03, rr25_current=0.00) < 0.0

def test_ris_neutral_within_threshold(scorer):
    assert scorer._score_ris(0.01, 0.005) == 0.0


# ── Regime ────────────────────────────────────────────────────────────────────

def test_regime_bull(scorer):
    assert scorer._detect_regime(_ohlcv(60, "up")) == "bull"

def test_regime_bear(scorer):
    assert scorer._detect_regime(_ohlcv(60, "down")) == "bear"

def test_regime_neutral_insufficient_data(scorer):
    assert scorer._detect_regime([{"close": 50000.0}]*5) == "neutral"

def test_regime_scaling_amplifies_directional_in_bull(scorer):
    weights = {"D1_D7": 0.22, "D2": 0.15, "D8": 0.08}
    scaled = scorer._apply_regime_scaling(weights, "call", "bull", "BTC")
    assert scaled["D1_D7"] > weights["D1_D7"]  # directional scaled up
    assert abs(scaled["D8"] - weights["D8"]/sum(weights.values())) < 0.05

def test_weights_sum_to_1_after_scaling(scorer):
    weights = {"D1_D7": 0.22, "D2": 0.15, "D3": 0.14, "D4": 0.11,
               "D6_D9": 0.14, "D8": 0.08, "D10": 0.09, "RIS": 0.07}
    scaled = scorer._apply_regime_scaling(weights, "call", "bull", "BTC")
    assert abs(sum(scaled.values()) - 1.0) < 0.001


# ── Conflict rules ────────────────────────────────────────────────────────────

def test_d3d4_conflict_reduces_contribution(scorer):
    raw = 1.0*0.14 + (-1.0)*0.11
    adjusted = scorer._apply_d3d4_conflict_rule(1.0, -1.0, 0.14, 0.11)
    assert abs(adjusted) < abs(raw)

def test_d3d4_no_conflict_same_direction(scorer):
    expected = 1.0*0.14 + 0.8*0.11
    assert scorer._apply_d3d4_conflict_rule(1.0, 0.8, 0.14, 0.11) == pytest.approx(expected, abs=0.001)


# ── ETH call penalty ─────────────────────────────────────────────────────────

def test_eth_call_penalty_applied(scorer):
    assert scorer._apply_eth_call_penalty(80.0, "ETH", "call", 0.28) == pytest.approx(68.0, abs=0.1)

def test_eth_call_penalty_skipped_outside_delta(scorer):
    assert scorer._apply_eth_call_penalty(80.0, "ETH", "call", 0.18) == 80.0

def test_eth_call_penalty_skipped_btc(scorer):
    assert scorer._apply_eth_call_penalty(80.0, "BTC", "call", 0.28) == 80.0


# ── Full score ────────────────────────────────────────────────────────────────

def test_full_score_returns_required_keys(scorer):
    result = scorer.score(
        asset="BTC", gex_dex={"totals": {"net_gex": -1e6}, "second_order": {"vanna": 1.0}},
        current_funding_rate=0.0001, funding_rate_history=[0.0001]*1000,
        vol_surface={"rr25": 0.01, "pc_by_moneyness": {"pc_ratio_all": 1.0}},
        rr25_history=[0.01]*30, pc_ratio_history=[1.0]*200,
        block_trades={"blocks_detected": False},
        stablecoin_inflow_pct=None, ibit_pc_ratio=None, ibit_pc_30d_avg=None,
        ohlcv_daily=_ohlcv(60), spot_close=87000.0,
    )
    assert all(k in result for k in ("call_score", "put_score", "regime", "breakdown"))
    assert 0.0 <= result["call_score"] <= 100.0

def test_eth_d10_always_zero(scorer):
    result = scorer.score(
        asset="ETH", gex_dex={"totals": {"net_gex": 0}, "second_order": {"vanna": 0}},
        current_funding_rate=0.0001, funding_rate_history=[0.0001]*1000,
        vol_surface={"rr25": 0.01, "pc_by_moneyness": {"pc_ratio_all": 1.0}},
        rr25_history=[0.01]*30, pc_ratio_history=[1.0]*200,
        block_trades={"blocks_detected": False},
        stablecoin_inflow_pct=None, ibit_pc_ratio=0.5, ibit_pc_30d_avg=0.9,
        ohlcv_daily=_ohlcv(60), spot_close=87000.0,
    )
    assert result["breakdown"]["D10"] == 0.0
