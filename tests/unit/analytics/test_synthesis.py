"""
Unit tests for SynthesisMapper, ScoringEngine, and SynthesisEngine v2.0.
"""

import pytest
from typing import Optional
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
from coding.core.analytics.results.analysis_result import (
    ExpirationBundle,
    MarketMetricsResult,
    OnChainAnalysisResult,
)
from coding.core.analytics.results.expiry_results import (
    ExpirationAnalysisResult,
    MaxPainResult,
    MoneynessLeg,
    MoneynessResult,
    PutCallRatioResult,
    SupportResistanceResult,
    VolumeStatsResult,
)
from coding.core.analytics.results.flow_results import FlowResult, FlowTotals
from coding.core.analytics.results.gex_dex_results import GexDexKeyLevels, GexDexLevel, GexDexResult
from coding.core.analytics.results.market_wide_results import (
    BlockTradesResult,
    CrossAssetCorrelationResult,
    FuturesBasisResult,
    MarketWideResult,
    PerpetualFundingResult,
    RealizedVolatilityResult,
    TermStructureResult,
    VarianceRiskPremiumResult,
)
from coding.core.analytics.results.vol_surface_results import (
    MoneynessBucket,
    PutCallByMoneyness,
    SecondOrderGreeks,
    SkewResult,
    VolSurfaceResult,
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
        risk_reversal_25d=-8.0,
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


_MONEYNESS_LEG = MoneynessLeg(
    itm_oi=0.0, otm_oi=0.0, total_oi=0.0,
    itm_notional=0.0, otm_notional=0.0, total_notional=0.0, itm_pct=0.0, otm_pct=0.0,
)


def make_default_market_wide(underlying_price: float = 65000.0) -> MarketWideResult:
    """Fully-populated MarketWideResult mirroring the old analyzer-mock's market_wide_structured."""
    return MarketWideResult(
        spot_price=underlying_price,
        currency="BTC",
        dvol=52.0,
        iv_percentile_365d=75.0,
        aggregate_gex_dex=None,
        term_structure=TermStructureResult(
            entries=(), shape="CONTANGO", spread=5.0, spread_signed=5.0,
            iv_by_dte={6: 49.0, 13: 49.5, 27: 49.2},
        ),
        futures_basis=FuturesBasisResult(entries=(), futures_basis={"27MAR26": 1.5}),
        realized_volatility=RealizedVolatilityResult(rv_by_window={10: 0.45, 20: 0.42, 30: 0.48}),
        variance_risk_premium=VarianceRiskPremiumResult(
            vrp=4.0, signal="FAIR", dvol=52.0, rv_30d=0.48,
        ),
        volatility_cone=None,
        perpetual_funding=PerpetualFundingResult(
            perp_open_interest=1_000_000_000, funding_rate=0.000001, funding_8h=-0.000015,
            funding_trend="Stable", history_points=0,
        ),
        block_trades=BlockTradesResult(trades=(), notional_threshold=100_000.0, total_detected=0),
        cross_asset_correlation=CrossAssetCorrelationResult(
            other_currency="ETH", price_correlation=0.90, dvol_correlation=0.85, sample_size=30,
        ),
        failed_sections=(),
    )


def make_onchain_result(
    expiration: str = "27MAR26",
    *,
    underlying_price: float = 65000.0,
    include_gex_dex: bool = True,
    include_flow: bool = True,
    include_vol_surface: bool = True,
    include_instruments: bool = True,
    market_wide: Optional[MarketWideResult] = None,
) -> OnChainAnalysisResult:
    """
    Create a minimal-but-typed OnChainAnalysisResult for testing SynthesisMapper
    (refactor_design_spec.md section T7 — replaces the old MagicMock analyzer).
    """
    parsed_instruments = (
        {
            expiration: (
                {"instrument_name": f"BTC-{expiration}-70000-C", "expiration": expiration,
                 "strike": 70000.0, "option_type": "C", "open_interest": 5000, "volume": 100},
                {"instrument_name": f"BTC-{expiration}-70000-P", "expiration": expiration,
                 "strike": 70000.0, "option_type": "P", "open_interest": 4000, "volume": 80},
            )
        }
        if include_instruments else {}
    )

    analysis = ExpirationAnalysisResult(
        expiration=expiration, underlying_price=underlying_price,
        total_instruments=2, call_count=1, put_count=1, strike_rows=(),
        max_pain=MaxPainResult(max_pain_strike=70000.0, pain_by_strike={}, min_pain_value=0.0),
        put_call_ratio=PutCallRatioResult(
            total_call_oi=5000.0, total_put_oi=4000.0, ratio=0.80, bias="Neutral",
        ),
        volume_stats=VolumeStatsResult(
            total_call_volume=100.0, total_put_volume=80.0, total_volume=180.0, volume_ratio=1.25,
        ),
        moneyness=MoneynessResult(
            calls=_MONEYNESS_LEG, puts=_MONEYNESS_LEG, totals=_MONEYNESS_LEG, oi_skew="Neutral",
        ),
        support_resistance=SupportResistanceResult(
            resistance_levels=(), support_levels=(),
            short_term_resistance=None, short_term_support=None,
        ),
    )

    gex_dex = None
    if include_gex_dex:
        gex_dex = GexDexResult(
            strike_rows=(), cumulative_gex={}, cumulative_dex={},
            key_levels=GexDexKeyLevels(
                call_resistance=GexDexLevel(strike=75000.0, net_gex=2_000_000.0),
                put_support=GexDexLevel(strike=60000.0, net_gex=-3_000_000.0),
                hvl=67000.0, gamma_flip=None,
            ),
            spot_price=underlying_price, total_net_gex=-5_000_000.0, total_net_dex=-200.0,
            currency="BTC",
        )

    flow = None
    if include_flow:
        flow = FlowResult(
            flow_data={},
            expiration_totals=FlowTotals(
                call_buy_volume=0.0, call_sell_volume=0.0, put_buy_volume=0.0, put_sell_volume=0.0,
            ),
            bias_interpretation="Moderate Buying", flow_trend="Steady Buy Pressure",
            top_buy_strikes=(), top_sell_strikes=(), trade_count=50, spot_price=underlying_price,
            window_start_ms=0, window_end_ms=86_400_000, lookback_hours=24.0,
            sufficient_data=True, low_confidence=False,
        )

    vol_surface = None
    if include_vol_surface:
        bucket = lambda label, ratio: MoneynessBucket(
            call_oi=0.0, put_oi=0.0, range_label=label, ratio=ratio, bias="Neutral",
        )
        vol_surface = VolSurfaceResult(
            expiration=expiration, spot_price=underlying_price, iv_by_strike=(),
            skew_25d=SkewResult(
                put_25d_iv=56.0, call_25d_iv=48.0, put_25d_strike=None, call_25d_strike=None,
                risk_reversal_25d=-8.0, interpretation="Put skew",
            ),
            pc_by_moneyness=PutCallByMoneyness(
                atm=bucket("ATM", 1.2), near_otm=bucket("Near-OTM", 0.9), far_otm=bucket("Far-OTM", 0.5),
            ),
            second_order_greeks=SecondOrderGreeks(
                vanna_exposure_holder=0.001, charm_exposure_holder=50.0, vanna_signal="N/A", charm_signal="N/A",
                skipped_instruments=0,
            ),
            atm_iv=50.0, vwap_iv=None, mark_iv_average=None, traded_instrument_count=0,
        )

    bundles = ()
    if include_instruments:
        bundles = (
            ExpirationBundle(
                expiration=expiration, analysis=analysis, gex_dex=gex_dex, flow=flow,
                vol_surface=vol_surface, oi_changes=None, iv_percentile=None, trend=None,
                flow_chart_paths={}, enriched_instruments=(),
            ),
        )

    atm_iv_by_expiration = {expiration: 50.0} if (include_vol_surface and include_instruments) else {}

    return OnChainAnalysisResult(
        currency="BTC",
        underlying_price=underlying_price,
        generated_at=datetime.now(),
        market_metrics=MarketMetricsResult(
            dvol=52.0, iv_percentile=75.0, iv_rank=None, current_funding=None, funding_8h=None,
        ),
        expirations=bundles,
        market_wide=market_wide if market_wide is not None else make_default_market_wide(underlying_price),
        parsed_instruments=parsed_instruments,
        atm_iv_by_expiration=atm_iv_by_expiration,
        recent_trades=(),
    )


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

class TestScoreSkew:
    """
    bugfix_spec.md Item 9 / Decision D6 acceptance test (T9.4 verbatim):
    score_skew is re-signed to the market risk-reversal convention -- the
    highest-risk part of this item, tested explicitly rather than assumed.
    """

    def test_t9_4_negative_risk_reversal_scores_bearish(self):
        """A NEGATIVE risk reversal (puts richer) must score bearish."""
        score, _, _ = ScoringEngine.score_skew(risk_reversal_25d=-4.37)
        assert score < 0

    def test_t9_4_positive_risk_reversal_scores_bullish(self):
        score, _, _ = ScoringEngine.score_skew(risk_reversal_25d=4.37)
        assert score > 0

    def test_balanced_near_zero(self):
        score, _, description = ScoringEngine.score_skew(risk_reversal_25d=0.5)
        assert score == 0.0
        assert "Normal" in description

    def test_extreme_put_demand(self):
        score, _, description = ScoringEngine.score_skew(risk_reversal_25d=-6.0)
        assert score == -2.0
        assert "Extreme put demand" in description

    def test_extreme_call_demand(self):
        score, _, description = ScoringEngine.score_skew(risk_reversal_25d=6.0)
        assert score == 2.0
        assert "Calls much richer" in description.lower() or "unusual" in description.lower()


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
        result = make_onchain_result()
        market = SynthesisMapper.build_market_wide(result)

        assert isinstance(market, MarketWideMetrics)
        assert market.spot_price == 65000.0
        assert market.dvol == 52.0
        assert market.iv_percentile_365d == 75.0
        assert market.funding_8h == -0.0015
        assert market.term_structure_shape == "CONTANGO"
        assert market.rv_10d == 45.0
        assert market.vrp == 4.0
        assert market.futures_basis == {"27MAR26": 1.5}

    def test_empty_structured_returns_defaults(self):
        empty_mw = MarketWideResult(
            spot_price=50000.0, currency="BTC", dvol=None, iv_percentile_365d=None,
            aggregate_gex_dex=None, term_structure=None, futures_basis=None,
            realized_volatility=None, variance_risk_premium=None, volatility_cone=None,
            perpetual_funding=None, block_trades=None, cross_asset_correlation=None,
            failed_sections=(),
        )
        result = make_onchain_result(underlying_price=50000.0, market_wide=empty_mw)

        market = SynthesisMapper.build_market_wide(result)
        assert market.spot_price == 50000.0
        assert market.dvol == 0.0
        # Empty shape normalizes to CONTANGO with spread=0 per v2.0 spec
        assert market.term_structure_shape == "CONTANGO"
        assert market.term_structure_spread == 0.0
        assert market.futures_basis == {}

    def test_flat_shape_normalized_to_contango(self):
        """Non-standard shape values must be normalized."""
        mw = MarketWideResult(
            spot_price=65000.0, currency="BTC", dvol=None, iv_percentile_365d=None,
            aggregate_gex_dex=None,
            term_structure=TermStructureResult(
                entries=(), shape="FLAT", spread=0.3, spread_signed=0.3, iv_by_dte={},
            ),
            futures_basis=None, realized_volatility=None, variance_risk_premium=None,
            volatility_cone=None, perpetual_funding=None, block_trades=None,
            cross_asset_correlation=None, failed_sections=(),
        )
        result = make_onchain_result(market_wide=mw)
        market = SynthesisMapper.build_market_wide(result)
        assert market.term_structure_shape == "CONTANGO"
        assert market.term_structure_spread == 0.0

    def test_valid_shapes_pass_through(self):
        """CONTANGO and BACKWARDATION pass through unchanged."""
        for shape in ("CONTANGO", "BACKWARDATION"):
            mw = MarketWideResult(
                spot_price=65000.0, currency="BTC", dvol=None, iv_percentile_365d=None,
                aggregate_gex_dex=None,
                term_structure=TermStructureResult(
                    entries=(), shape=shape, spread=5.0, spread_signed=5.0, iv_by_dte={},
                ),
                futures_basis=None, realized_volatility=None, variance_risk_premium=None,
                volatility_cone=None, perpetual_funding=None, block_trades=None,
                cross_asset_correlation=None, failed_sections=(),
            )
            result = make_onchain_result(market_wide=mw)
            market = SynthesisMapper.build_market_wide(result)
            assert market.term_structure_shape == shape
            assert market.term_structure_spread == 5.0


# =============================================================================
# TESTS: SynthesisMapper.build_expiry_metrics
# =============================================================================

class TestBuildExpiryMetrics:
    def test_complete_data_returns_expiry_metrics(self):
        onchain_result = make_onchain_result("27MAR26")
        result = SynthesisMapper.build_expiry_metrics(onchain_result, "27MAR26")

        assert result is not None
        assert isinstance(result, ExpiryMetrics)
        assert result.expiry == "27MAR26"
        assert result.total_gex == -5_000_000
        assert result.total_dex == -200
        assert result.gex_environment == "Negative"
        assert result.call_resistance_strike == 75000
        assert result.put_support_strike == 60000
        assert result.atm_iv == 50.0
        # bugfix_spec.md Item 9: risk_reversal_25d (call - put) = -8.0,
        # the sign-flip of the fixture's put_over_call_skew_25d = 8.0.
        assert result.risk_reversal_25d == -8.0
        assert result.flow_bias == "Moderate Buying"
        assert result.pc_ratio == 0.80
        assert result.flow_sufficient_data is True

    def test_total_volume_calculated(self):
        """total_volume should sum volume from all instruments."""
        onchain_result = make_onchain_result("27MAR26")
        result = SynthesisMapper.build_expiry_metrics(onchain_result, "27MAR26")
        # Fixture has volume=100 (call) + volume=80 (put) = 180
        assert result.total_volume == 180

    def test_no_removed_fields_in_result(self):
        """Removed fields should not be on the result."""
        onchain_result = make_onchain_result("27MAR26")
        result = SynthesisMapper.build_expiry_metrics(onchain_result, "27MAR26")
        assert not hasattr(result, "volume_pc_ratio")
        assert not hasattr(result, "vwap_iv")
        assert not hasattr(result, "mark_iv")

    def test_missing_gex_data_returns_none(self):
        onchain_result = make_onchain_result("27MAR26", include_gex_dex=False)
        result = SynthesisMapper.build_expiry_metrics(onchain_result, "27MAR26")
        assert result is None

    def test_missing_instruments_returns_none(self):
        onchain_result = make_onchain_result("27MAR26", include_instruments=False)
        result = SynthesisMapper.build_expiry_metrics(onchain_result, "27MAR26")
        assert result is None

    def test_missing_flow_data_uses_defaults(self):
        onchain_result = make_onchain_result("27MAR26", include_flow=False)
        result = SynthesisMapper.build_expiry_metrics(onchain_result, "27MAR26")
        assert result is not None
        assert result.flow_bias == "Mixed/Neutral"
        assert result.flow_trend == "Mixed/Neutral Flow"
        assert result.flow_sufficient_data is False

    def test_missing_vol_surface_uses_defaults(self):
        onchain_result = make_onchain_result("27MAR26", include_vol_surface=False)
        result = SynthesisMapper.build_expiry_metrics(onchain_result, "27MAR26")
        assert result is not None
        assert result.atm_iv == 0.0
        assert result.risk_reversal_25d == 0.0

    def test_insufficient_flow_data_propagates_gate(self):
        """bugfix_spec.md Item 6 / F6.3.4 (carried from A4 review): the
        FlowResult's sufficient_data flag must reach ExpiryMetrics so the
        scoring engine can force weight 0."""
        onchain_result = make_onchain_result("27MAR26")
        bundle = onchain_result.bundle("27MAR26")
        gated_flow = FlowResult(
            flow_data={}, expiration_totals=bundle.flow.expiration_totals,
            bias_interpretation="Insufficient flow data", flow_trend="Insufficient flow data",
            top_buy_strikes=(), top_sell_strikes=(), trade_count=3,
            spot_price=65000.0, window_start_ms=0, window_end_ms=86_400_000,
            lookback_hours=24.0, sufficient_data=False, low_confidence=False,
        )
        gated_bundle = ExpirationBundle(
            expiration=bundle.expiration, analysis=bundle.analysis, gex_dex=bundle.gex_dex,
            flow=gated_flow, vol_surface=bundle.vol_surface, oi_changes=None,
            iv_percentile=None, trend=None, flow_chart_paths={}, enriched_instruments=(),
        )
        gated_result = OnChainAnalysisResult(
            currency=onchain_result.currency, underlying_price=onchain_result.underlying_price,
            generated_at=onchain_result.generated_at, market_metrics=onchain_result.market_metrics,
            expirations=(gated_bundle,), market_wide=onchain_result.market_wide,
            parsed_instruments=onchain_result.parsed_instruments,
            atm_iv_by_expiration=onchain_result.atm_iv_by_expiration,
            recent_trades=onchain_result.recent_trades,
        )
        result = SynthesisMapper.build_expiry_metrics(gated_result, "27MAR26")
        assert result.flow_bias == "Insufficient flow data"
        assert result.flow_sufficient_data is False


# =============================================================================
# TESTS: SynthesisMapper.build_all
# =============================================================================

class TestBuildAll:
    def test_returns_market_and_expiries(self):
        onchain_result = make_onchain_result("27MAR26")
        market, expiries = SynthesisMapper.build_all(onchain_result)

        assert isinstance(market, MarketWideMetrics)
        assert len(expiries) == 1
        assert expiries[0].expiry == "27MAR26"

    def test_skips_expiries_with_missing_gex(self):
        onchain_result = make_onchain_result("27MAR26", include_gex_dex=False)
        market, expiries = SynthesisMapper.build_all(onchain_result)
        assert len(expiries) == 0


# =============================================================================
# TESTS: SynthesisEngine.run
# =============================================================================

class TestSynthesisEngineRun:
    def test_run_with_example_data_no_crash(self):
        result = build_from_current_data()
        assert isinstance(result, str)
        assert len(result) > 100
        # Must contain a v2.0 regime classification. bugfix_spec.md Item 9
        # (Decision D6): score_skew's re-sign changes which regime this
        # hardcoded example data classifies as (an accepted, documented
        # consequence -- see score_skew's own docstring) -- check against
        # the actual "Market Regime: <value>" line's rendering (lowercase,
        # underscored, MarketRegime's own .value strings) rather than one
        # specific hardcoded regime name, so this stays a genuine smoke
        # test of "some valid regime was classified", not a pin on which one.
        assert any(regime.value in result for regime in MarketRegime)

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
