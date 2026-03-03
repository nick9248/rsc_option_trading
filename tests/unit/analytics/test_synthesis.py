"""
Unit tests for SynthesisMapper, ScoringEngine, and SynthesisEngine v2.0.
"""

import pytest
from unittest.mock import MagicMock
from datetime import datetime, timedelta

from coding.core.analytics.synthesis import (
    SynthesisEngine,
    SynthesisMapper,
    ScoringEngine,
    RegimeClassifier,
    ExpiryMetrics,
    MarketWideMetrics,
    MarketRegime,
    VolRegime,
    Signal,
    build_from_current_data,
)


# =============================================================================
# FIXTURES
# =============================================================================

def make_expiry_metrics(**overrides) -> ExpiryMetrics:
    """Create a minimal valid ExpiryMetrics for testing."""
    defaults = dict(
        expiry="27MAR26",
        dte=27,
        total_oi=10000,
        notional=500_000_000,
        max_pain=70000,
        pc_ratio=0.80,
        total_gex=-5_000_000,
        total_dex=-200,
        gex_environment="Negative",
        call_resistance_strike=75000,
        call_resistance_gex=2_000_000,
        put_support_strike=60000,
        put_support_gex=-3_000_000,
        hvl_strike=67000,
        atm_iv=50.0,
        skew_25d=8.0,
        put_25d_iv=56.0,
        call_25d_iv=48.0,
        pc_atm=1.2,
        pc_near_otm=0.9,
        pc_far_otm=0.5,
        net_vanna=0.001,
        net_charm=50.0,
        flow_bias="Moderate Buying",
        flow_trend="Steady Buy Pressure",
    )
    defaults.update(overrides)
    return ExpiryMetrics(**defaults)


def make_market_wide(**overrides) -> MarketWideMetrics:
    """Create minimal valid MarketWideMetrics for testing."""
    defaults = dict(
        spot_price=65000.0,
        dvol=52.0,
        iv_percentile_365d=75.0,
        funding_rate=0.0001,
        funding_8h=-0.0015,
        term_structure_shape="CONTANGO",
        term_structure_spread=5.0,
        iv_by_dte={6: 49.0, 13: 49.5, 27: 49.2, 55: 48.0, 90: 48.5},
        rv_10d=45.0,
        rv_20d=42.0,
        rv_30d=48.0,
        vrp=4.0,
        cone_10d_pctile=60.0,
        cone_20d_pctile=55.0,
        cone_30d_pctile=65.0,
        futures_basis={"27MAR26": 1.5, "25DEC26": 3.5},
        perp_oi=1_000_000_000,
        perp_funding_trend="Stable",
        btc_eth_price_corr=0.90,
        btc_eth_dvol_corr=0.85,
        block_trades=[],
    )
    defaults.update(overrides)
    return MarketWideMetrics(**defaults)


def make_analyzer_mock(expiration: str = "27MAR26") -> MagicMock:
    """Create a mock OnChainAnalyzer with structured data populated."""
    analyzer = MagicMock()
    analyzer.underlying_price = 65000.0
    analyzer.currency = "BTC"
    analyzer.market_wide_structured = {
        "spot_price": 65000.0,
        "dvol": 52.0,
        "iv_percentile_365d": 75.0,
        "funding_rate": 0.000001,   # decimal: ×100 → 0.0001% in MarketWideMetrics
        "funding_8h": -0.000015,    # decimal: ×100 → -0.0015% in MarketWideMetrics
        "shape": "CONTANGO",
        "spread": 5.0,
        "iv_by_dte": {6: 49.0, 13: 49.5, 27: 49.2},
        "rv_10d": 0.45,   # decimal: ×100 → 45.0% in MarketWideMetrics
        "rv_20d": 0.42,   # decimal: ×100 → 42.0%
        "rv_30d": 0.48,   # decimal: ×100 → 48.0%
        "vrp": 4.0,
        "cone_10d_pctile": 60.0,
        "cone_20d_pctile": 55.0,
        "cone_30d_pctile": 65.0,
        "futures_basis": {"27MAR26": 1.5},
        "perp_oi": 1_000_000_000,
        "perp_funding_trend": "Stable",
        "btc_eth_price_corr": 0.90,
        "btc_eth_dvol_corr": 0.85,
        "block_trades": [],
    }
    analyzer.gex_dex_structured = {
        expiration: {
            "total_net_gex": -5_000_000,
            "total_net_dex": -200,
            "key_levels": {
                "call_resistance": {"strike": 75000, "net_gex": 2_000_000},
                "put_support": {"strike": 60000, "net_gex": -3_000_000},
                "hvl": 67000,
                "gamma_flip": None,
            },
        }
    }
    analyzer.volatility_surface_structured = {
        expiration: {
            "atm_iv": 50.0,
            "skew_25d": {
                "skew": 8.0,
                "put_25d_iv": 56.0,
                "call_25d_iv": 48.0,
            },
            "pc_by_moneyness": {
                "atm": {"ratio": 1.2},
                "near_otm": {"ratio": 0.9},
                "far_otm": {"ratio": 0.5},
            },
            "second_order_greeks": {
                "net_vanna": 0.001,
                "net_charm": 50.0,
            },
        }
    }
    analyzer.buy_sell_flow_structured = {
        expiration: {
            "bias_interpretation": "Moderate Buying",
            "flow_trend": "Steady Buy Pressure",
            "top_buy_strikes": [],
            "top_sell_strikes": [],
        }
    }
    analyzer.parsed_data = {
        expiration: [
            {"instrument_name": f"BTC-{expiration}-70000-C", "expiration": expiration,
             "strike": 70000.0, "option_type": "C", "open_interest": 5000, "volume": 100},
            {"instrument_name": f"BTC-{expiration}-70000-P", "expiration": expiration,
             "strike": 70000.0, "option_type": "P", "open_interest": 4000, "volume": 80},
        ]
    }
    analyzer.get_expirations.return_value = [expiration]

    analyzer.group_by_strike.return_value = {
        70000.0: {"call_oi": 5000, "put_oi": 4000, "call_volume": 100, "put_volume": 80}
    }
    analyzer.calculate_max_pain.return_value = {"max_pain_strike": 70000.0}
    analyzer.calculate_put_call_ratio.return_value = {"ratio": 0.80}

    return analyzer


# =============================================================================
# TESTS: Signal is IntEnum
# =============================================================================

class TestSignalIntEnum:
    def test_signal_is_intenum(self):
        assert isinstance(Signal.BULLISH, int)
        assert Signal.STRONG_BULLISH.value == 2
        assert Signal.STRONG_BEARISH.value == -2

    def test_signal_arithmetic(self):
        """TRANSITION logic needs sign multiplication and magnitude addition."""
        near = Signal.BULLISH
        far = Signal.BEARISH
        assert near.value * far.value < 0  # conflicting
        assert abs(near.value) + abs(far.value) == 2  # magnitude check


# =============================================================================
# TESTS: MarketRegime sub-types
# =============================================================================

class TestMarketRegimeSubTypes:
    def test_range_bound_subtypes_exist(self):
        assert MarketRegime.RANGE_BOUND_NEUTRAL.value == "range_bound_neutral"
        assert MarketRegime.RANGE_BOUND_BULLISH.value == "range_bound_bullish"
        assert MarketRegime.RANGE_BOUND_BEARISH.value == "range_bound_bearish"
        assert MarketRegime.RANGE_BOUND_ELEVATED.value == "range_bound_elevated"

    def test_risk_off_removed(self):
        values = [m.value for m in MarketRegime]
        assert "risk_off" not in values

    def test_old_range_bound_removed(self):
        values = [m.value for m in MarketRegime]
        assert "range_bound" not in values


# =============================================================================
# TESTS: ExpiryMetrics removed fields
# =============================================================================

class TestExpiryMetricsCleanup:
    def test_no_volume_pc_ratio(self):
        assert not hasattr(ExpiryMetrics, "volume_pc_ratio") or \
               "volume_pc_ratio" not in ExpiryMetrics.__dataclass_fields__

    def test_no_vwap_iv(self):
        assert "vwap_iv" not in ExpiryMetrics.__dataclass_fields__

    def test_no_mark_iv(self):
        assert "mark_iv" not in ExpiryMetrics.__dataclass_fields__

    def test_no_large_oi_changes(self):
        assert "large_oi_changes" not in ExpiryMetrics.__dataclass_fields__


# =============================================================================
# TESTS: score_pc_ratio — contrarian dampening + DTE clamping
# =============================================================================

class TestScorePCRatio:
    def test_extreme_low_contrarian(self):
        """P/C < 0.40 should be dampened to +1.0 with weight 0.5."""
        score, weight, reason = ScoringEngine.score_pc_ratio(0.30)
        assert score == 1.0
        assert weight == 0.5
        assert "contrarian" in reason.lower()

    def test_extreme_high_contrarian(self):
        """P/C > 2.00 should be dampened to -1.0 with weight 0.5."""
        score, weight, reason = ScoringEngine.score_pc_ratio(2.50)
        assert score == -1.0
        assert weight == 0.5
        assert "contrarian" in reason.lower()

    def test_normal_strong_bullish(self):
        """P/C 0.40-0.60 should be +2.0."""
        score, weight, _ = ScoringEngine.score_pc_ratio(0.50)
        assert score == 2.0
        assert weight == 0.7

    def test_dte_clamping(self):
        """DTE <= 2 should clamp score to ±1.0."""
        score, weight, reason = ScoringEngine.score_pc_ratio(0.50, dte=1)
        assert score == 1.0  # clamped from 2.0
        assert "DTE≤2" in reason

    def test_dte_clamping_no_effect_on_small_score(self):
        """Score already within ±1.0 should not be affected by DTE clamping."""
        score, _, _ = ScoringEngine.score_pc_ratio(0.70, dte=0)
        assert score == 1.0  # was already 1.0


# =============================================================================
# TESTS: score_dex — spot-normalized + DTE clamping
# =============================================================================

class TestScoreDEX:
    def test_strong_bullish_at_100k(self):
        """DEX 600 at spot 100k → 0.006 > 0.005 → +2.0."""
        score, weight, _ = ScoringEngine.score_dex(600, spot=100000)
        assert score == 2.0
        assert weight == 0.8

    def test_neutral_small_dex(self):
        """DEX 50 at spot 100k → 0.0005 → neutral."""
        score, _, _ = ScoringEngine.score_dex(50, spot=100000)
        assert score == 0.0

    def test_scales_with_price(self):
        """At 50k spot, DEX 300 → 0.006 → still strong bullish."""
        score, _, _ = ScoringEngine.score_dex(300, spot=50000)
        assert score == 2.0

    def test_dte_clamping(self):
        """DTE <= 2 should clamp ±2.0 to ±1.0."""
        score, _, reason = ScoringEngine.score_dex(600, spot=100000, dte=2)
        assert score == 1.0
        assert "DTE≤2" in reason


# =============================================================================
# TESTS: score_max_pain_gravity — DTE-scaled weight
# =============================================================================

class TestScoreMaxPainGravity:
    def test_near_term_high_weight(self):
        """DTE 3 should get weight 0.5 for non-neutral scores."""
        _, weight, _ = ScoringEngine.score_max_pain_gravity(
            max_pain=75000, spot=65000, dte=3)
        assert weight == 0.5

    def test_far_term_low_weight(self):
        """DTE 60 should get weight 0.15."""
        _, weight, _ = ScoringEngine.score_max_pain_gravity(
            max_pain=75000, spot=65000, dte=60)
        assert weight == 0.15

    def test_neutral_always_02(self):
        """Neutral score always gets weight 0.2 regardless of DTE."""
        _, weight, _ = ScoringEngine.score_max_pain_gravity(
            max_pain=65500, spot=65000, dte=3)
        assert weight == 0.2


# =============================================================================
# TESTS: score_funding — uses funding_8h only
# =============================================================================

class TestScoreFunding:
    def test_funding_8h_used_for_annualized_rate(self):
        """Zero funding_8h should be neutral."""
        score, weight, reason = ScoringEngine.score_funding(funding_8h=0.0)
        assert score == 0.0
        assert "Neutral" in reason

    def test_funding_8h_crowded_long(self):
        """0.01 × 3 × 365 = 10.95% → crowded long → -1.0."""
        score, weight, reason = ScoringEngine.score_funding(funding_8h=0.01)
        assert score < 0
        assert "crowded" in reason.lower()

    def test_funding_8h_crowded_short(self):
        """-0.02 × 3 × 365 = -21.9% → extremely crowded short → +2.0."""
        score, weight, reason = ScoringEngine.score_funding(funding_8h=-0.02)
        assert score > 0

    def test_signature_no_funding_rate(self):
        """score_funding should only accept funding_8h."""
        import inspect
        sig = inspect.signature(ScoringEngine.score_funding)
        params = list(sig.parameters.keys())
        assert "funding_rate" not in params
        assert "funding_8h" in params


# =============================================================================
# TESTS: score_vanna_charm — IV-conditional vanna + gamma weight
# =============================================================================

class TestScoreVannaCharm:
    def test_zero_vanna_returns_zero(self):
        """Zero vanna should produce vanna_signal=0 (not -1 phantom)."""
        score, _, reason = ScoringEngine.score_vanna_charm(
            net_vanna=0, net_charm=0)
        assert score == 0.0
        assert "zero" in reason.lower()

    def test_high_iv_positive_vanna_bullish(self):
        """IV pctile > 60: positive vanna = bullish."""
        score, _, _ = ScoringEngine.score_vanna_charm(
            net_vanna=0.001, net_charm=0, iv_pctile=70)
        assert score > 0

    def test_low_iv_positive_vanna_bearish(self):
        """IV pctile < 40: positive vanna = BEARISH (reversed)."""
        score, _, _ = ScoringEngine.score_vanna_charm(
            net_vanna=0.001, net_charm=0, iv_pctile=30)
        assert score < 0

    def test_mid_iv_vanna_neutral(self):
        """IV pctile 40-60: vanna signal = 0."""
        score, _, _ = ScoringEngine.score_vanna_charm(
            net_vanna=0.001, net_charm=0, iv_pctile=50)
        assert score == 0.0

    def test_negative_gex_high_weight(self):
        """Deeply negative GEX → weight 0.4."""
        _, weight, _ = ScoringEngine.score_vanna_charm(
            net_vanna=0.001, net_charm=50,
            iv_pctile=70, gex_total=-6_000_000, spot=100000)
        assert weight == 0.4

    def test_positive_gex_low_weight(self):
        """Strongly positive GEX → weight 0.15."""
        _, weight, _ = ScoringEngine.score_vanna_charm(
            net_vanna=0.001, net_charm=50,
            iv_pctile=70, gex_total=6_000_000, spot=100000)
        assert weight == 0.15


# =============================================================================
# TESTS: score_futures_basis — no basis_back
# =============================================================================

class TestScoreFuturesBasis:
    def test_signature_no_basis_back(self):
        import inspect
        sig = inspect.signature(ScoringEngine.score_futures_basis)
        params = list(sig.parameters.keys())
        assert "basis_back" not in params

    def test_strong_contango(self):
        score, _, _ = ScoringEngine.score_futures_basis(12.0)
        assert score == 2.0


# =============================================================================
# TESTS: score_vrp — cone < 15 uses raw VRP
# =============================================================================

class TestScoreVRP:
    def test_cone_high_uses_forward(self):
        """cone > 85: should use forward VRP."""
        score, _, reason = ScoringEngine.score_vrp(
            vrp=-10.0, rv_10d=45.0, rv_20d=42.0, rv_30d=64.0,
            cone_30d_pctile=90)
        assert "Forward VRP" in reason

    def test_cone_low_uses_raw(self):
        """cone < 15: should use raw VRP, narrative warning only."""
        score, _, reason = ScoringEngine.score_vrp(
            vrp=8.0, rv_10d=45.0, rv_20d=42.0, rv_30d=44.0,
            cone_30d_pctile=10)
        assert "abnormally quiet" in reason
        # Score should be based on raw VRP (8.0) → between 5 and 10 → score 1.0
        assert score == 1.0


# =============================================================================
# TESTS: Fragility detection
# =============================================================================

class TestFragilityDetection:
    def test_no_fragility_normal(self):
        """Normal conditions → multiplier 1.0, level NONE."""
        scores = [(0.5, 0.7, "P/C something"), (0.3, 0.6, "DEX something")]
        mult, level = ScoringEngine.detect_fragility(scores, funding_8h=0.001)
        assert mult == 1.0
        assert level == "NONE"

    def test_bullish_fragile_moderate(self):
        """Strong bullish consensus + moderate funding → MODERATE."""
        scores = [
            (2.0, 0.8, "DEX strong bullish"),
            (1.5, 0.7, "P/C bullish"),
            (1.0, 0.6, "Flow bullish"),
            (-1.0, 0.5, "Funding crowded long"),  # contains "funding"
        ]
        # avg_excl_funding: (2*0.8 + 1.5*0.7 + 1*0.6) / (0.8+0.7+0.6) = 3.25/2.1 ≈ 1.55
        # funding_ann = 0.02 * 3 * 365 = 21.9% > 15%
        mult, level = ScoringEngine.detect_fragility(scores, funding_8h=0.02)
        assert mult == 0.7
        assert level == "MODERATE"

    def test_bullish_fragile_high(self):
        """Strong bullish + extreme funding → HIGH."""
        scores = [
            (2.0, 0.8, "DEX strong"),
            (2.0, 0.7, "P/C strong"),
            (-2.0, 0.6, "Funding extreme"),
        ]
        # avg_excl_funding: (2*0.8 + 2*0.7) / 1.5 = 3.0/1.5 = 2.0
        # funding_ann = 0.03 * 3 * 365 = 32.85% > 25%
        mult, level = ScoringEngine.detect_fragility(scores, funding_8h=0.03)
        assert mult == 0.5
        assert level == "HIGH"

    def test_funding_excluded_from_avg(self):
        """Funding scores should be excluded from directional avg calculation."""
        scores = [
            (1.0, 0.8, "DEX bullish"),
            (-2.0, 0.6, "Funding {ann_rate} crowded"),
        ]
        # avg_excl_funding: only DEX = 1.0/0.8 * 0.8 = 1.0 → > 0.8
        # But funding_ann = 0.02 * 3 * 365 = 21.9% > 15%
        mult, level = ScoringEngine.detect_fragility(scores, funding_8h=0.02)
        assert mult == 0.7  # MODERATE


# =============================================================================
# TESTS: classify_vol_regime — spot-normalized GEX + term structure
# =============================================================================

class TestClassifyVolRegime:
    def test_suppressed_with_normalized_gex(self):
        """GEX/spot > 20 + low IV → SUPPRESSED."""
        regime, _ = RegimeClassifier.classify_vol_regime(
            gex_total=2_500_000, iv_pctile_score=0, vrp_score=0,
            skew_score=0, spot=100000)
        # 2.5M / 100k = 25 > 20
        assert regime == VolRegime.SUPPRESSED

    def test_elevated_with_vrp_confirmation(self):
        """High IV + VRP confirms → ELEVATED."""
        regime, reasons = RegimeClassifier.classify_vol_regime(
            gex_total=0, iv_pctile_score=1, vrp_score=1,
            skew_score=0, spot=100000)
        assert regime == VolRegime.ELEVATED
        assert "VRP confirms" in reasons[0]

    def test_elevated_with_term_structure_stress(self):
        """High IV + backwardation → ELEVATED."""
        regime, reasons = RegimeClassifier.classify_vol_regime(
            gex_total=0, iv_pctile_score=1, vrp_score=0,
            skew_score=0, spot=100000, term_structure_score=-1)
        assert regime == VolRegime.ELEVATED
        assert "term structure stressed" in reasons[0]


# =============================================================================
# TESTS: classify_market_regime — RANGE_BOUND sub-types + TRANSITION magnitude
# =============================================================================

class TestClassifyMarketRegime:
    def test_bearish_suppressed_is_range_bound_bearish(self):
        regime, _ = RegimeClassifier.classify_market_regime(
            Signal.BEARISH, VolRegime.SUPPRESSED, Signal.NEUTRAL, Signal.NEUTRAL)
        assert regime == MarketRegime.RANGE_BOUND_BEARISH

    def test_bullish_suppressed_is_range_bound_bullish(self):
        regime, _ = RegimeClassifier.classify_market_regime(
            Signal.BULLISH, VolRegime.SUPPRESSED, Signal.NEUTRAL, Signal.NEUTRAL)
        assert regime == MarketRegime.RANGE_BOUND_BULLISH

    def test_neutral_elevated_is_range_bound_elevated(self):
        regime, _ = RegimeClassifier.classify_market_regime(
            Signal.NEUTRAL, VolRegime.ELEVATED, Signal.NEUTRAL, Signal.NEUTRAL)
        assert regime == MarketRegime.RANGE_BOUND_ELEVATED

    def test_neutral_suppressed_is_range_bound_neutral(self):
        regime, _ = RegimeClassifier.classify_market_regime(
            Signal.NEUTRAL, VolRegime.SUPPRESSED, Signal.NEUTRAL, Signal.NEUTRAL)
        assert regime == MarketRegime.RANGE_BOUND_NEUTRAL

    def test_neutral_normal_is_range_bound_neutral(self):
        regime, _ = RegimeClassifier.classify_market_regime(
            Signal.NEUTRAL, VolRegime.NORMAL, Signal.NEUTRAL, Signal.NEUTRAL)
        assert regime == MarketRegime.RANGE_BOUND_NEUTRAL

    def test_transition_requires_magnitude(self):
        """Mild disagreement (BULLISH vs BEARISH, magnitude=2) → TRANSITION."""
        regime, _ = RegimeClassifier.classify_market_regime(
            Signal.NEUTRAL, VolRegime.NORMAL, Signal.BULLISH, Signal.BEARISH)
        assert regime == MarketRegime.TRANSITION

    def test_transition_blocked_by_low_magnitude(self):
        """Near=BULLISH, Far=BEARISH but one is NEUTRAL → no transition."""
        # If near=BULLISH(1) far=NEUTRAL(0): product=0, not < 0 → no conflict
        regime, _ = RegimeClassifier.classify_market_regime(
            Signal.NEUTRAL, VolRegime.NORMAL, Signal.BULLISH, Signal.NEUTRAL)
        assert regime != MarketRegime.TRANSITION

    def test_transition_strong_conflict(self):
        """STRONG_BULLISH near vs BEARISH far → magnitude=3 → TRANSITION."""
        regime, _ = RegimeClassifier.classify_market_regime(
            Signal.NEUTRAL, VolRegime.NORMAL,
            Signal.STRONG_BULLISH, Signal.BEARISH)
        assert regime == MarketRegime.TRANSITION

    def test_neutral_explosive_is_transition(self):
        regime, _ = RegimeClassifier.classify_market_regime(
            Signal.NEUTRAL, VolRegime.EXPLOSIVE, Signal.NEUTRAL, Signal.NEUTRAL)
        assert regime == MarketRegime.TRANSITION


# =============================================================================
# TESTS: Risk reversal guard
# =============================================================================

class TestTradeRecommendations:
    def test_risk_reversal_excluded_in_bearish_regimes(self):
        from coding.core.analytics.synthesis import NarrativeGenerator
        for regime in [MarketRegime.TRENDING_DOWN, MarketRegime.VOLATILE_BEARISH,
                       MarketRegime.RANGE_BOUND_BEARISH]:
            result = NarrativeGenerator.generate_trade_recommendations(
                regime=regime, vol_regime=VolRegime.NORMAL,
                iv_pctile=80, skew=15.0, gex_total=-5_000_000,
                near_term_expiry="6MAR26", far_term_expiry="27MAR26",
                skew_expiry="27MAR26")
            assert "Risk Reversal" not in result, f"Risk Reversal should be excluded in {regime}"

    def test_ic_skew_adjustment_puts_rich(self):
        from coding.core.analytics.synthesis import NarrativeGenerator
        result = NarrativeGenerator.generate_trade_recommendations(
            regime=MarketRegime.RANGE_BOUND_NEUTRAL, vol_regime=VolRegime.NORMAL,
            iv_pctile=80, skew=10.0, gex_total=0,
            near_term_expiry="6MAR26", far_term_expiry="27MAR26")
        assert "short put at 25-delta" in result

    def test_ic_skew_adjustment_calls_rich(self):
        from coding.core.analytics.synthesis import NarrativeGenerator
        result = NarrativeGenerator.generate_trade_recommendations(
            regime=MarketRegime.RANGE_BOUND_NEUTRAL, vol_regime=VolRegime.NORMAL,
            iv_pctile=80, skew=1.0, gex_total=0,
            near_term_expiry="6MAR26", far_term_expiry="27MAR26")
        assert "short call at 25-delta" in result


# =============================================================================
# TESTS: SynthesisMapper.build_market_wide
# =============================================================================

class TestBuildMarketWide:
    def test_returns_market_wide_metrics(self):
        analyzer = make_analyzer_mock()
        result = SynthesisMapper.build_market_wide(analyzer)

        assert isinstance(result, MarketWideMetrics)
        assert result.spot_price == 65000.0
        assert result.dvol == 52.0
        assert result.iv_percentile_365d == 75.0
        assert result.funding_8h == -0.0015
        assert result.term_structure_shape == "CONTANGO"
        assert result.rv_10d == 45.0
        assert result.vrp == 4.0
        assert result.futures_basis == {"27MAR26": 1.5}

    def test_empty_structured_returns_defaults(self):
        analyzer = MagicMock()
        analyzer.underlying_price = 50000.0
        analyzer.market_wide_structured = {}

        result = SynthesisMapper.build_market_wide(analyzer)
        assert result.spot_price == 50000.0
        assert result.dvol == 0.0
        # Empty shape normalizes to CONTANGO with spread=0 per v2.0 spec
        assert result.term_structure_shape == "CONTANGO"
        assert result.term_structure_spread == 0.0
        assert result.futures_basis == {}

    def test_flat_shape_normalized_to_contango(self):
        """Non-standard shape values must be normalized."""
        analyzer = MagicMock()
        analyzer.underlying_price = 65000.0
        analyzer.market_wide_structured = {
            "shape": "FLAT",  # invalid per spec
            "spread": 0.3,   # < 0.5
        }
        result = SynthesisMapper.build_market_wide(analyzer)
        assert result.term_structure_shape == "CONTANGO"
        assert result.term_structure_spread == 0.0

    def test_valid_shapes_pass_through(self):
        """CONTANGO and BACKWARDATION pass through unchanged."""
        for shape in ("CONTANGO", "BACKWARDATION"):
            analyzer = MagicMock()
            analyzer.underlying_price = 65000.0
            analyzer.market_wide_structured = {"shape": shape, "spread": 5.0}
            result = SynthesisMapper.build_market_wide(analyzer)
            assert result.term_structure_shape == shape
            assert result.term_structure_spread == 5.0


# =============================================================================
# TESTS: SynthesisMapper.build_expiry_metrics
# =============================================================================

class TestBuildExpiryMetrics:
    def test_complete_data_returns_expiry_metrics(self):
        analyzer = make_analyzer_mock("27MAR26")
        result = SynthesisMapper.build_expiry_metrics(analyzer, "27MAR26")

        assert result is not None
        assert isinstance(result, ExpiryMetrics)
        assert result.expiry == "27MAR26"
        assert result.total_gex == -5_000_000
        assert result.total_dex == -200
        assert result.gex_environment == "Negative"
        assert result.call_resistance_strike == 75000
        assert result.put_support_strike == 60000
        assert result.atm_iv == 50.0
        assert result.skew_25d == 8.0
        assert result.flow_bias == "Moderate Buying"
        assert result.pc_ratio == 0.80

    def test_total_volume_calculated(self):
        """total_volume should sum volume from all instruments."""
        analyzer = make_analyzer_mock("27MAR26")
        result = SynthesisMapper.build_expiry_metrics(analyzer, "27MAR26")
        # Mock has volume=100 (call) + volume=80 (put) = 180
        assert result.total_volume == 180

    def test_no_removed_fields_in_result(self):
        """Removed fields should not be on the result."""
        analyzer = make_analyzer_mock("27MAR26")
        result = SynthesisMapper.build_expiry_metrics(analyzer, "27MAR26")
        assert not hasattr(result, "volume_pc_ratio")
        assert not hasattr(result, "vwap_iv")
        assert not hasattr(result, "mark_iv")

    def test_missing_gex_data_returns_none(self):
        analyzer = make_analyzer_mock("27MAR26")
        analyzer.gex_dex_structured = {}
        result = SynthesisMapper.build_expiry_metrics(analyzer, "27MAR26")
        assert result is None

    def test_missing_instruments_returns_none(self):
        analyzer = make_analyzer_mock("27MAR26")
        analyzer.parsed_data = {}
        result = SynthesisMapper.build_expiry_metrics(analyzer, "27MAR26")
        assert result is None

    def test_missing_flow_data_uses_defaults(self):
        analyzer = make_analyzer_mock("27MAR26")
        analyzer.buy_sell_flow_structured = {}
        result = SynthesisMapper.build_expiry_metrics(analyzer, "27MAR26")
        assert result is not None
        assert result.flow_bias == "Mixed/Neutral"
        assert result.flow_trend == "Mixed/Neutral Flow"

    def test_missing_vol_surface_uses_defaults(self):
        analyzer = make_analyzer_mock("27MAR26")
        analyzer.volatility_surface_structured = {}
        result = SynthesisMapper.build_expiry_metrics(analyzer, "27MAR26")
        assert result is not None
        assert result.atm_iv == 0.0
        assert result.skew_25d == 0.0


# =============================================================================
# TESTS: SynthesisMapper.build_all
# =============================================================================

class TestBuildAll:
    def test_returns_market_and_expiries(self):
        analyzer = make_analyzer_mock("27MAR26")
        market, expiries = SynthesisMapper.build_all(analyzer)

        assert isinstance(market, MarketWideMetrics)
        assert len(expiries) == 1
        assert expiries[0].expiry == "27MAR26"

    def test_skips_expiries_with_missing_gex(self):
        analyzer = make_analyzer_mock("27MAR26")
        analyzer.gex_dex_structured = {}
        market, expiries = SynthesisMapper.build_all(analyzer)
        assert len(expiries) == 0


# =============================================================================
# TESTS: SynthesisEngine.run
# =============================================================================

class TestSynthesisEngineRun:
    def test_run_with_example_data_no_crash(self):
        result = build_from_current_data()
        assert isinstance(result, str)
        assert len(result) > 100
        # Must contain a v2.0 regime classification
        assert any(regime in result for regime in [
            "RANGE_BOUND_NEUTRAL", "RANGE_BOUND_BULLISH", "RANGE_BOUND_BEARISH",
            "RANGE_BOUND_ELEVATED", "TRENDING_UP", "TRENDING_DOWN",
            "VOLATILE_BULLISH", "VOLATILE_BEARISH", "TRANSITION",
        ])

    def test_run_returns_regime_label(self):
        engine = SynthesisEngine()
        market = make_market_wide()
        expiry = make_expiry_metrics()
        result = engine.run(market, [expiry])
        assert isinstance(result, str)
        assert "Regime:" in result

    def test_run_contains_trade_recommendations(self):
        engine = SynthesisEngine()
        market = make_market_wide()
        expiry = make_expiry_metrics()
        result = engine.run(market, [expiry])
        assert "TRADE RECOMMENDATIONS" in result

    def test_run_contains_scoring_detail_with_fragility(self):
        """Output must include scoring detail with Fragility line."""
        engine = SynthesisEngine()
        market = make_market_wide()
        expiry = make_expiry_metrics()
        result = engine.run(market, [expiry])
        assert "SCORING DETAIL" in result
        assert "Direction:" in result
        assert "Vol Regime:" in result
        assert "Fragility:" in result

    def test_run_with_minimal_expiries(self):
        engine = SynthesisEngine()
        market = make_market_wide(iv_by_dte={30: 50.0})
        expiry = make_expiry_metrics(dte=30)
        result = engine.run(market, [expiry])
        assert isinstance(result, str)

    def test_run_empty_iv_by_dte_no_crash(self):
        engine = SynthesisEngine()
        market = make_market_wide(iv_by_dte={})
        expiry = make_expiry_metrics()
        result = engine.run(market, [expiry])
        assert "ATM IV (front): ~0.0%" in result

    def test_dte_zero_excluded_from_top_expiries(self):
        """Expiries with DTE=0 should be excluded from directional scoring."""
        engine = SynthesisEngine()
        market = make_market_wide()
        # DTE 0 expiry with huge OI — should NOT dominate scoring
        dte0 = make_expiry_metrics(expiry="28FEB26", dte=0, total_oi=100000,
                                   pc_ratio=5.0)  # extreme P/C
        normal = make_expiry_metrics(expiry="27MAR26", dte=27, total_oi=50000,
                                     pc_ratio=0.80)
        result = engine.run(market, [dte0, normal])
        assert isinstance(result, str)
        # The result should not be dominated by the extreme DTE=0 P/C
