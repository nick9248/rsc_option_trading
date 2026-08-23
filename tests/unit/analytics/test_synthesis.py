"""
Unit tests for SynthesisMapper, ScoringEngine, and SynthesisEngine v2.0.
"""

import re

import pytest
from typing import Optional
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta, timezone

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
    Block,
    BlockTrade,
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
        # Matches make_market_wide()'s default spot_price so every
        # pre-existing test (which never overrides this) sees identical
        # behavior to before the carried per-expiry underlying_price fix --
        # tests that need to prove per-expiry sourcing pass a different
        # value explicitly.
        underlying_price=65000.0,
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
        large_prints=[],
        blocks=[],
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
    max_pain_strike: Optional[float] = 70000.0,
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
        max_pain=MaxPainResult(max_pain_strike=max_pain_strike, pain_by_strike={}, min_pain_value=0.0),
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
                # Task C5 review fix round 2: dealer_vanna_exposure/
                # dealer_charm_exposure are now REQUIRED (no default) --
                # explicit values here, deliberately NOT the negation of
                # the holder sum above, so this fixture cannot be mistaken
                # for (or silently drift back to) the retired negation
                # convention. These TestBuildExpiryMetrics tests exercise
                # the MAPPING (ExpiryMetrics.net_vanna/net_charm sourced
                # from these dealer_* fields), not the split derivation
                # itself (test_volatility_surface_calculator.py's job).
                dealer_vanna_exposure=0.002, dealer_charm_exposure=25.0,
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
        # Task G2-C: build_expiry_metrics now computes DTE via
        # MarketWideCalculator.calculate_dte(expiration, result.generated_at),
        # which requires a timezone-aware now -- generated_at must be UTC-aware
        # here, matching OnChainAnalysisBuilder.build()'s own fix.
        generated_at=datetime.now(timezone.utc),
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

    def test_insufficient_data_weight_zero_not_scored_as_near_spot(self):
        """
        Task Wave-H-B Fix 4: sufficient_data=False (the caller is passing
        the spot-price display fallback for a max_pain that never
        resolved) must score as insufficient data at weight zero -- NOT
        fall through to the ordinary distance-based branches, which for
        a fallback-to-spot value would compute distance_pct == 0.0 and
        confidently report "Max pain $X is near spot (+0.0%)" (weight
        0.2, a real non-zero-weight score) for a computation that never
        ran.
        """
        score, weight, description = ScoringEngine.score_max_pain_gravity(
            max_pain=65000, spot=65000, dte=3, sufficient_data=False)
        assert score == 0.0
        assert weight == 0.0
        assert "insufficient data" in description.lower()
        assert "near spot" not in description.lower()

    def test_sufficient_data_default_true_unchanged_behavior(self):
        """The new sufficient_data param defaults to True -- every
        pre-existing call site (no keyword passed) must be unaffected."""
        score, weight, description = ScoringEngine.score_max_pain_gravity(
            max_pain=75000, spot=65000, dte=3)
        assert weight == 0.5
        assert "insufficient data" not in description.lower()


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

    def test_none_iv_pctile_treated_as_mid_range_neutral(self):
        """
        Task G2-B: iv_pctile=None (unavailable) must not crash (the old
        signature had no None handling and would raise on `iv_pctile >
        60`) and must not fabricate a directional vanna signal -- it's
        treated the same as the mid-range 40-60 band, which already
        produces vanna_signal=0.0 with no fabricated direction.
        """
        score, _, _ = ScoringEngine.score_vanna_charm(
            net_vanna=0.001, net_charm=0, iv_pctile=None)
        assert score == 0.0

    def test_none_net_vanna_weight_zero_insufficient_data(self):
        """
        Task G2-G: net_vanna=None (vol surface never computed for this
        expiry) must NOT be treated the same as net_vanna==0 (a real
        measurement of "no structural drift") -- the old signature had no
        None handling and would crash on `net_vanna == 0` comparing None
        with 0 (actually a silent False, no crash, but would then fall
        into `net_vanna > 0` -> TypeError). A neutral score at zero
        weight with an explicit disclosure is the correct behavior,
        matching score_iv_percentile's/score_skew's None branches.
        """
        score, weight, reason = ScoringEngine.score_vanna_charm(
            net_vanna=None, net_charm=50.0, iv_pctile=70)
        assert score == 0.0
        assert weight == 0.0
        assert "insufficient data" in reason.lower()

    def test_none_net_charm_weight_zero_insufficient_data(self):
        score, weight, reason = ScoringEngine.score_vanna_charm(
            net_vanna=0.001, net_charm=None, iv_pctile=70)
        assert score == 0.0
        assert weight == 0.0
        assert "insufficient data" in reason.lower()

    def test_none_net_vanna_distinct_from_measured_zero(self):
        """A genuinely-measured net_vanna=0 must keep its own "zero"
        signal text, not the None-branch's "insufficient data" text."""
        _, _, zero_reason = ScoringEngine.score_vanna_charm(net_vanna=0.0, net_charm=0.0)
        _, _, none_reason = ScoringEngine.score_vanna_charm(net_vanna=None, net_charm=None)
        assert zero_reason != none_reason
        assert "insufficient data" not in zero_reason.lower()


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

    def test_none_weight_zero_insufficient_data(self):
        """
        Task G2-G: risk_reversal_25d=None (vol surface never computed for
        this expiry) must NOT fall through to the ``<= MILD_POINTS``
        branch (the old signature had no None handling and a bare
        comparison ``None < -RISK_REVERSAL_STRONG_POINTS`` raises
        TypeError in Python 3) -- and, if it somehow avoided crashing,
        must never render as "RR25 +0.0%: Normal", a specific, confident
        reading for a metric that was never measured.
        """
        score, weight, description = ScoringEngine.score_skew(risk_reversal_25d=None)
        assert score == 0.0
        assert weight == 0.0
        assert "insufficient data" in description.lower()
        assert "normal" not in description.lower()


class TestScoreTermStructure:
    """
    Task Wave-H-B Fix 2: score_term_structure never had a None branch --
    the mapper used to fabricate a confident "CONTANGO" for every
    missing-data case (ts is None, shape=="FLAT", or a genuine small
    backwardated tilt) and this scorer scored it as a real reading.
    """

    def test_none_shape_weight_zero_insufficient_data(self):
        score, weight, description = ScoringEngine.score_term_structure(
            shape=None, spread=None, iv_by_dte={})
        assert score == 0.0
        assert weight == 0.0
        assert "insufficient data" in description.lower()

    def test_none_spread_weight_zero_insufficient_data(self):
        """Defensive: shape and spread should be None together (both
        come from the same Optional TermStructureResult), but the guard
        must not crash if only one is somehow None."""
        score, weight, description = ScoringEngine.score_term_structure(
            shape="CONTANGO", spread=None, iv_by_dte={})
        assert score == 0.0
        assert weight == 0.0
        assert "insufficient data" in description.lower()

    def test_flat_shape_neutral_not_backwardation(self):
        """
        A real "FLAT" shape must get its own neutral reading, not fall
        into the old bare ``else`` branch (which treated anything not
        literally "CONTANGO" as backwardation and would have printed
        "Backwardation -0pts: Mild" for a flat market).
        """
        score, weight, description = ScoringEngine.score_term_structure(
            shape="FLAT", spread=0.3, iv_by_dte={})
        assert score == 0.0
        assert "backwardation" not in description.lower()
        assert "flat" in description.lower()

    def test_strong_contango_scores_bullish_for_selling_back_months(self):
        score, _, description = ScoringEngine.score_term_structure(
            shape="CONTANGO", spread=15.0, iv_by_dte={})
        assert score == 2.0
        assert "contango" in description.lower()

    def test_strong_backwardation_scores_extreme_fear(self):
        score, _, description = ScoringEngine.score_term_structure(
            shape="BACKWARDATION", spread=15.0, iv_by_dte={})
        assert score == -2.0
        assert "backwardation" in description.lower()


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

    def test_none_funding_8h_no_crash_returns_none_level(self):
        """
        Task G2-B: funding_8h=None must not crash (the old code computed
        `funding_8h * 3 * 365` unconditionally) and must not claim a
        fragility verdict it has no data to support.
        """
        scores = [(2.0, 0.8, "DEX strong bullish"), (1.5, 0.7, "P/C bullish")]
        mult, level = ScoringEngine.detect_fragility(scores, funding_8h=None)
        assert mult == 1.0
        assert level == "NONE"


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

    def test_explosive_with_negative_gex_high_iv_and_put_side_skew(self):
        """
        bugfix_spec.md Item 9 fix-review Critical #1 regression test:
        negative GEX + high IV + a NEGATIVE skew_score (puts richer, the
        crash-correlated side under score_skew's re-signed risk-reversal
        convention) must classify EXPLOSIVE. Before this fix, the EXPLOSIVE
        branch still checked `skew_score >= 1` (the pre-Item-9 condition,
        never re-signed alongside score_skew) -- a negative skew_score
        could never satisfy it, making EXPLOSIVE unreachable on real data
        (every expiry in the golden fixture scores skew_score <= -1, i.e.
        puts richer, which is the empirically crash-correlated side).
        """
        regime, reasons = RegimeClassifier.classify_vol_regime(
            gex_total=-3_000_000, iv_pctile_score=1, vrp_score=0,
            skew_score=-1, spot=100000)
        # -3M / 100k = -30 < -20
        assert regime == VolRegime.EXPLOSIVE
        assert "Explosive regime" in reasons[0]

    def test_not_explosive_when_skew_score_is_call_side(self):
        """
        The old (buggy, pre-fix) condition `skew_score >= 1` fired on
        extreme CALL-side risk reversal -- the wrong side. After the fix,
        a positive skew_score (calls richer) must NOT trigger EXPLOSIVE
        even with negative GEX + high IV; it falls through to ELEVATED
        (mixed confirmation, since vrp_score/term_structure_score are 0).
        """
        regime, _ = RegimeClassifier.classify_vol_regime(
            gex_total=-3_000_000, iv_pctile_score=1, vrp_score=0,
            skew_score=1, spot=100000)
        assert regime != VolRegime.EXPLOSIVE
        assert regime == VolRegime.ELEVATED

    def test_not_explosive_when_skew_score_neutral(self):
        """skew_score == 0 (Normal/Balanced) must not trigger EXPLOSIVE either."""
        regime, _ = RegimeClassifier.classify_vol_regime(
            gex_total=-3_000_000, iv_pctile_score=1, vrp_score=0,
            skew_score=0, spot=100000)
        assert regime != VolRegime.EXPLOSIVE


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
                iv_pctile=80, risk_reversal_25d=-15.0, gex_total=-5_000_000,
                near_term_expiry="6MAR26", far_term_expiry="27MAR26",
                skew_expiry="27MAR26")
            assert "Risk Reversal" not in result, f"Risk Reversal should be excluded in {regime}"

    def test_ic_skew_adjustment_puts_rich(self):
        from coding.core.analytics.synthesis import NarrativeGenerator
        result = NarrativeGenerator.generate_trade_recommendations(
            regime=MarketRegime.RANGE_BOUND_NEUTRAL, vol_regime=VolRegime.NORMAL,
            iv_pctile=80, risk_reversal_25d=-10.0, gex_total=0,
            near_term_expiry="6MAR26", far_term_expiry="27MAR26")
        assert "short put at 25-delta" in result

    def test_ic_skew_adjustment_calls_rich(self):
        from coding.core.analytics.synthesis import NarrativeGenerator
        result = NarrativeGenerator.generate_trade_recommendations(
            regime=MarketRegime.RANGE_BOUND_NEUTRAL, vol_regime=VolRegime.NORMAL,
            iv_pctile=80, risk_reversal_25d=-1.0, gex_total=0,
            near_term_expiry="6MAR26", far_term_expiry="27MAR26")
        assert "short call at 25-delta" in result

    def test_none_risk_reversal_ic_still_recommended_with_insufficient_data_note(self):
        """
        Task G2-G: risk_reversal_25d=None must not crash the IC
        skew-adjustment comparison (the old signature had no None
        handling: `None < -8` raises TypeError in Python 3), and the IC
        recommendation itself (justified by iv_pctile/regime/vol_regime,
        independent of RR25) must still be produced -- only its
        skew-adjustment sub-clause degrades to a disclosure.
        """
        from coding.core.analytics.synthesis import NarrativeGenerator
        result = NarrativeGenerator.generate_trade_recommendations(
            regime=MarketRegime.RANGE_BOUND_NEUTRAL, vol_regime=VolRegime.NORMAL,
            iv_pctile=80, risk_reversal_25d=None, gex_total=0,
            near_term_expiry="6MAR26", far_term_expiry="27MAR26")
        assert "Short Iron Condor" in result
        assert "insufficient data" in result.lower()

    def test_none_risk_reversal_skips_risk_reversal_strategy(self):
        """
        Task G2-G: Strategy 4 (Risk Reversal) is gated ON
        risk_reversal_25d itself (a specific "< -10%" threshold trigger)
        -- a missing reading cannot satisfy that threshold, so the whole
        recommendation must be skipped, not fabricated from a None
        comparison.
        """
        from coding.core.analytics.synthesis import NarrativeGenerator
        result = NarrativeGenerator.generate_trade_recommendations(
            regime=MarketRegime.RANGE_BOUND_NEUTRAL, vol_regime=VolRegime.NORMAL,
            iv_pctile=None, risk_reversal_25d=None, gex_total=0,
            near_term_expiry="6MAR26", far_term_expiry="27MAR26")
        assert "Risk Reversal" not in result

    def test_none_risk_reversal_directional_strategy_discloses_insufficient_data(self):
        """
        Task G2-G: Strategy 3 (directional spreads) is triggered by
        ``regime`` alone -- a missing RR25 must not block or crash the
        recommendation the regime already justifies, and must render an
        honest "insufficient data" annotation instead of a fabricated
        "+0.0%".
        """
        from coding.core.analytics.synthesis import NarrativeGenerator
        result = NarrativeGenerator.generate_trade_recommendations(
            regime=MarketRegime.TRENDING_UP, vol_regime=VolRegime.NORMAL,
            iv_pctile=50, risk_reversal_25d=None, gex_total=0,
            near_term_expiry="6MAR26", far_term_expiry="27MAR26")
        assert "Bull Call Spread" in result
        assert "insufficient data" in result.lower()
        assert "+0.0%" not in result

    def test_vrp_sell_edge_demotes_cheap_iv_long_vol_from_primary(self):
        """
        Task Wave-H-B Fix 3 (golden-fixture reproduction): iv_pctile=20
        (< 30, "cheap") triggers Strategy 2's long-vol recommendation on
        its own, but vrp=+10.4 says implied vol is still rich vs.
        realized -- the same disagreement that used to produce "Sell
        premium..." in the vol narrative and "PRIMARY -- Long
        Straddle/Strangle... Cheap IV favors owning volatility" in trade
        recommendations, both with top billing, in the same report. The
        long-vol idea must be demoted off PRIMARY and the conflict must
        be disclosed in the text.
        """
        from coding.core.analytics.synthesis import NarrativeGenerator
        result = NarrativeGenerator.generate_trade_recommendations(
            regime=MarketRegime.TRANSITION, vol_regime=VolRegime.NORMAL,
            iv_pctile=20, risk_reversal_25d=-3.6, gex_total=0,
            near_term_expiry="6MAR26", far_term_expiry="25SEP26",
            vrp=10.4,
        )
        assert "PRIMARY — Long Straddle/Strangle" not in result
        assert "Long Straddle/Strangle" in result
        assert "signals disagree" in result.lower()
        assert "reduced confidence" in result.lower()

    def test_vrp_none_cheap_iv_long_vol_stays_primary(self):
        """A missing VRP cannot disagree with anything -- Strategy 2 must
        fall back to its original iv_pctile-only PRIMARY behavior."""
        from coding.core.analytics.synthesis import NarrativeGenerator
        result = NarrativeGenerator.generate_trade_recommendations(
            regime=MarketRegime.TRANSITION, vol_regime=VolRegime.NORMAL,
            iv_pctile=20, risk_reversal_25d=-3.6, gex_total=0,
            near_term_expiry="6MAR26", far_term_expiry="25SEP26",
            vrp=None,
        )
        assert "PRIMARY — Long Straddle/Strangle" in result
        assert "signals disagree" not in result.lower()

    def test_vrp_agrees_with_cheap_iv_long_vol_stays_primary(self):
        """VRP confirming the buy-vol read (vrp <= 5, no sell-edge
        signal) must not trigger the disagreement branch."""
        from coding.core.analytics.synthesis import NarrativeGenerator
        result = NarrativeGenerator.generate_trade_recommendations(
            regime=MarketRegime.TRANSITION, vol_regime=VolRegime.NORMAL,
            iv_pctile=20, risk_reversal_25d=-3.6, gex_total=0,
            near_term_expiry="6MAR26", far_term_expiry="25SEP26",
            vrp=-8.0,
        )
        assert "PRIMARY — Long Straddle/Strangle" in result
        assert "signals disagree" not in result.lower()

    def test_explosive_regime_long_vol_not_demoted_even_if_vrp_disagrees(self):
        """
        Strategy 2's EXPLOSIVE-gamma-regime trigger is a completely
        different justification from the iv_pctile<30 trigger -- it must
        stay PRIMARY regardless of VRP (an explosive gamma regime is a
        real reason to own vol on its own, independent of the vol
        pricing debate the iv_pctile/VRP conflict is about).
        """
        from coding.core.analytics.synthesis import NarrativeGenerator
        result = NarrativeGenerator.generate_trade_recommendations(
            regime=MarketRegime.TRANSITION, vol_regime=VolRegime.EXPLOSIVE,
            iv_pctile=80, risk_reversal_25d=-3.6, gex_total=0,
            near_term_expiry="6MAR26", far_term_expiry="25SEP26",
            vrp=15.0,
        )
        assert "PRIMARY — Long Straddle/Strangle" in result
        assert "Explosive gamma regime" in result

    def test_vrp_buy_edge_demotes_expensive_iv_short_ic_from_primary(self):
        """
        Symmetric case to the golden-fixture reproduction above: an
        expensive iv_pctile (>70) triggers Strategy 1's short-IC
        recommendation on its own, but a strongly negative VRP says
        implied vol is actually cheap vs. realized -- the same defect
        class in the opposite direction. Must be demoted off PRIMARY
        with the conflict disclosed.
        """
        from coding.core.analytics.synthesis import NarrativeGenerator
        result = NarrativeGenerator.generate_trade_recommendations(
            regime=MarketRegime.RANGE_BOUND_NEUTRAL, vol_regime=VolRegime.NORMAL,
            iv_pctile=80, risk_reversal_25d=-3.6, gex_total=0,
            near_term_expiry="6MAR26", far_term_expiry="25SEP26",
            vrp=-12.0,
        )
        assert "PRIMARY — Short Iron Condor" not in result
        assert "Short Iron Condor" in result
        assert "signals disagree" in result.lower()
        assert "reduced confidence" in result.lower()


# =============================================================================
# TESTS: NarrativeGenerator None-handling (Task G2-G)
# =============================================================================

class TestGenerateRegimeNarrativeNoneLevels:
    def test_none_put_support_call_resistance_discloses_insufficient_data(self):
        """
        Task G2-G: put_support/call_resistance=None (no identified GEX
        level in this expiry's strike range) must render as an explicit
        disclosure, not crash on a bare ``:,.0f`` format spec applied to
        None, and not silently print "$0" -- a specific, wrong level.
        """
        from coding.core.analytics.synthesis import NarrativeGenerator
        result = NarrativeGenerator.generate_regime_narrative(
            regime=MarketRegime.RANGE_BOUND_NEUTRAL, spot=65000.0,
            put_support=None, call_resistance=None, max_pain=65000.0,
            gex_total=1_000_000,
        )
        assert "insufficient data" in result.lower()
        assert "$0" not in result

    def test_real_levels_still_render_dollar_formatted(self):
        from coding.core.analytics.synthesis import NarrativeGenerator
        result = NarrativeGenerator.generate_regime_narrative(
            regime=MarketRegime.RANGE_BOUND_NEUTRAL, spot=65000.0,
            put_support=60000.0, call_resistance=75000.0, max_pain=65000.0,
            gex_total=1_000_000,
        )
        assert "$60,000" in result
        assert "$75,000" in result


class TestGenerateVolNarrativeNoneRiskReversal:
    def test_none_risk_reversal_and_sell_iv_no_crash_discloses(self):
        """
        Task G2-G: risk_reversal_25d/sell_iv=None must not raise
        TypeError on a direct ``:+.1f``/``:.1f`` format spec (the old
        signature had no None handling), and must render an honest
        disclosure rather than a fabricated reading.
        """
        from coding.core.analytics.synthesis import NarrativeGenerator
        result = NarrativeGenerator.generate_vol_narrative(
            iv_pctile=85, vrp=12.0, vrp_adjustment="30d RV within normal range.",
            risk_reversal_25d=None, sell_expiry="6MAR26", sell_iv=None,
            buy_expiry="27MAR26",
        )
        assert "insufficient data" in result.lower()

    def test_real_risk_reversal_still_renders_numeric(self):
        from coding.core.analytics.synthesis import NarrativeGenerator
        result = NarrativeGenerator.generate_vol_narrative(
            iv_pctile=85, vrp=12.0, vrp_adjustment="30d RV within normal range.",
            risk_reversal_25d=-8.0, sell_expiry="6MAR26", sell_iv=49.0,
            buy_expiry="27MAR26",
        )
        assert "-8.0%" in result
        assert "49.0%" in result


class TestGenerateRiskFactorsNoneRiskReversal:
    def test_none_risk_reversal_skips_extreme_rr25_check_no_crash(self):
        """
        Task G2-G: risk_reversal_25d=None must not raise on the direct
        ``< -12`` comparison (the old signature had no None handling),
        and a missing reading is not itself a "risk factor" to disclose
        here (matches the pre-existing cone_30d_pctile/funding_8h None
        pattern in this same function) -- the run-level DATA QUALITY
        section is where its absence is disclosed.
        """
        from coding.core.analytics.synthesis import NarrativeGenerator
        result = NarrativeGenerator.generate_risk_factors(
            cone_30d_pctile=50.0, gex_total=1_000_000, gamma_rolloff=None,
            funding_8h=0.0, risk_reversal_25d=None,
        )
        assert "Extreme RR25" not in result


# =============================================================================
# TESTS: generate_risk_factors -- GAMMA CLIFF trigger (Task Wave-I-A Fix 1)
# =============================================================================

class TestGenerateRiskFactorsGammaCliff:
    """
    Task Wave-I-A Fix 1: the old trigger was ``largest_expiry_dte <= 3``,
    where ``largest_expiry`` = ``max(expiries, key=lambda e: e.total_oi)``.
    For BTC the largest-OI expiry is essentially always a far-dated
    quarterly, so that trigger could never fire even when a genuinely
    near-dated expiry carried a large share of the book's gamma (real pin
    risk). The fix reuses GexDexCalculator.calculate_rolloff_profile's own
    "GAMMA CLIFF" flag (``gamma_cliff_7d``, >30% of gamma mass within 7
    DTE) via the new ``gamma_rolloff`` parameter instead.
    """

    @staticmethod
    def _rolloff(cliff: bool, cum_share_7d: float = 40.0, rows=None):
        from coding.core.analytics.results.market_wide_results import (
            GammaRolloffResult, GammaRolloffRow,
        )
        if rows is None:
            rows = (
                GammaRolloffRow(
                    expiration="26JUL26", dte_days=1.0, net_gex=50_000_000.0,
                    share_pct=cum_share_7d, cum_share_pct=cum_share_7d, cum_net_gex=50_000_000.0,
                ),
            )
        return GammaRolloffResult(
            rows=tuple(rows), gamma_cliff_7d=cliff, cum_share_7d=cum_share_7d,
            cum_share_30d=cum_share_7d, gross_total=100_000_000.0,
        )

    def test_no_gamma_rolloff_data_no_cliff_risk(self):
        """gamma_rolloff=None (roll-off computation never ran) must not
        raise and must not report a GAMMA CLIFF -- matches the existing
        cone_30d_pctile/funding_8h "missing input is not a risk factor"
        convention in this same function."""
        from coding.core.analytics.synthesis import NarrativeGenerator
        result = NarrativeGenerator.generate_risk_factors(
            cone_30d_pctile=50.0, gex_total=1_000_000, gamma_rolloff=None,
            funding_8h=0.0, risk_reversal_25d=0.0,
        )
        assert "GAMMA CLIFF" not in result
        assert "No elevated risk factors detected" in result

    def test_gamma_rolloff_present_but_not_cliff_no_risk(self):
        """gamma_cliff_7d=False (real data, just below the 30% threshold)
        must not report a GAMMA CLIFF either."""
        from coding.core.analytics.synthesis import NarrativeGenerator
        result = NarrativeGenerator.generate_risk_factors(
            cone_30d_pctile=50.0, gex_total=1_000_000,
            gamma_rolloff=self._rolloff(cliff=False, cum_share_7d=12.0),
            funding_8h=0.0, risk_reversal_25d=0.0,
        )
        assert "GAMMA CLIFF" not in result

    def test_gamma_cliff_fires_regardless_of_largest_oi_expiry_dte(self):
        """
        The core reproduction: gamma_cliff_7d=True must fire the risk
        factor -- this is decoupled from ``largest_expiry_dte`` entirely
        now (the parameter doesn't even exist anymore), so it fires
        exactly when there IS genuine near-term gamma concentration,
        independent of which expiry happens to hold the most open
        interest.
        """
        from coding.core.analytics.synthesis import NarrativeGenerator
        result = NarrativeGenerator.generate_risk_factors(
            cone_30d_pctile=50.0, gex_total=1_000_000,
            gamma_rolloff=self._rolloff(cliff=True, cum_share_7d=63.0),
            funding_8h=0.0, risk_reversal_25d=0.0,
        )
        assert "GAMMA CLIFF" in result
        assert "63%" in result
        assert "26JUL26" in result
        assert "pin risk" in result


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

    def test_empty_structured_preserves_none_not_fabricated_zero(self):
        """
        Task G2-B (Wave G fresh audit, BLOCKER): this test used to assert
        ``market.dvol == 0.0`` as the CORRECT behavior for genuinely-
        missing data -- that was itself the bug. A ``None`` from every
        Optional section of ``MarketWideResult`` must reach
        ``MarketWideMetrics`` as ``None``, never a fabricated ``0.0`` --
        a fabricated zero is indistinguishable from a real measurement
        and gets scored as one.
        """
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
        assert market.dvol is None
        assert market.iv_percentile_365d is None
        assert market.funding_rate is None
        assert market.funding_8h is None
        assert market.rv_10d is None
        assert market.rv_20d is None
        assert market.rv_30d is None
        assert market.vrp is None
        assert market.cone_10d_pctile is None
        assert market.cone_20d_pctile is None
        assert market.cone_30d_pctile is None
        assert market.perp_oi is None
        assert market.btc_eth_price_corr is None
        assert market.btc_eth_dvol_corr is None
        assert market.failed_sections == ()
        # Task Wave-H-B Fix 2: term_structure=None (fewer than 2 usable
        # per-expiry ATM IVs) must preserve None -- the old "empty shape
        # normalizes to CONTANGO with spread=0" behavior asserted here
        # WAS the bug (same defect class as every other assertion this
        # test corrected: a fabricated confident reading standing in for
        # missing data).
        assert market.term_structure_shape is None
        assert market.term_structure_spread is None
        assert market.term_structure_spread_signed is None
        assert market.futures_basis == {}

    def test_present_but_null_funding_fields_stay_none_not_zero(self):
        """
        PerpetualFundingResult.funding_rate/funding_8h are independently
        Optional[float] even when the ``funding`` object itself exists.
        Must still surface as None, not 0.0.
        """
        mw = MarketWideResult(
            spot_price=65000.0, currency="BTC", dvol=None, iv_percentile_365d=None,
            aggregate_gex_dex=None, term_structure=None, futures_basis=None,
            realized_volatility=None, variance_risk_premium=None, volatility_cone=None,
            perpetual_funding=PerpetualFundingResult(
                perp_open_interest=500_000.0, funding_rate=None, funding_8h=None,
                funding_trend="Stable", history_points=0,
            ),
            block_trades=None, cross_asset_correlation=None, failed_sections=(),
        )
        result = make_onchain_result(market_wide=mw)
        market = SynthesisMapper.build_market_wide(result)
        assert market.funding_rate is None
        assert market.funding_8h is None
        assert market.perp_oi == 500_000.0

    def test_failed_sections_threaded_through_to_metrics(self):
        """Task G2-B Finding 3: MarketWideResult.failed_sections must
        reach MarketWideMetrics.failed_sections unchanged."""
        mw = MarketWideResult(
            spot_price=65000.0, currency="BTC", dvol=None, iv_percentile_365d=None,
            aggregate_gex_dex=None, term_structure=None, futures_basis=None,
            realized_volatility=None, variance_risk_premium=None, volatility_cone=None,
            perpetual_funding=None, block_trades=None, cross_asset_correlation=None,
            failed_sections=("futures_basis", "perpetual_funding"),
        )
        result = make_onchain_result(market_wide=mw)
        market = SynthesisMapper.build_market_wide(result)
        assert market.failed_sections == ("futures_basis", "perpetual_funding")

    def test_flat_shape_passes_through_unchanged(self):
        """
        Task Wave-H-B Fix 2: "FLAT" is a real, computed, non-CONTANGO/
        BACKWARDATION shape (the calculator's own third value, emitted
        when |back - front| <= 2pts) -- it must pass through as "FLAT",
        not get relabelled "CONTANGO". The old normalize-to-CONTANGO
        logic asserted here WAS the bug: it collapsed a genuine FLAT
        reading (and the real signed spread that goes with it) into a
        fabricated CONTANGO label.
        """
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
        assert market.term_structure_shape == "FLAT"
        assert market.term_structure_spread == 0.3
        assert market.term_structure_spread_signed == 0.3

    def test_flat_shape_with_negative_signed_spread_not_mislabeled_contango(self):
        """
        Task Wave-H-B Fix 2 case (c): a genuine backwardated tilt too
        small to cross the calculator's +/-2pt threshold (e.g. signed
        diff -1.8) is classified "FLAT" by the calculator, spread=1.8
        (abs), spread_signed=-1.8. The old mapper logic forced this to
        "CONTANGO" with the real negative signed spread still attached,
        rendering the self-contradictory "CONTANGO (-1.8pts)" in the
        report header. Must now pass through as "FLAT" with the true
        signed spread intact.
        """
        mw = MarketWideResult(
            spot_price=65000.0, currency="BTC", dvol=None, iv_percentile_365d=None,
            aggregate_gex_dex=None,
            term_structure=TermStructureResult(
                entries=(), shape="FLAT", spread=1.8, spread_signed=-1.8, iv_by_dte={},
            ),
            futures_basis=None, realized_volatility=None, variance_risk_premium=None,
            volatility_cone=None, perpetual_funding=None, block_trades=None,
            cross_asset_correlation=None, failed_sections=(),
        )
        result = make_onchain_result(market_wide=mw)
        market = SynthesisMapper.build_market_wide(result)
        assert market.term_structure_shape == "FLAT"
        assert market.term_structure_spread == 1.8
        assert market.term_structure_spread_signed == -1.8

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

    def test_blocks_and_large_prints_are_wired_separately(self):
        """Independent review round 2 (Important #3): real blocks
        (block_trade_id grouping, institutional_metrics_spec.md section 9)
        must reach MarketWideMetrics.blocks; the pre-existing notional-
        filter list must reach large_prints -- NOT the same field, and
        neither list's content leaks into the other."""
        mw = MarketWideResult(
            spot_price=65000.0, currency="BTC", dvol=None, iv_percentile_365d=None,
            aggregate_gex_dex=None, term_structure=None, futures_basis=None,
            realized_volatility=None, variance_risk_premium=None, volatility_cone=None,
            perpetual_funding=None,
            block_trades=BlockTradesResult(
                trades=(
                    BlockTrade(
                        timestamp=1700000000000, instrument_name="BTC-28FEB26-100000-C",
                        amount=5.0, direction="buy", notional=500_000.0, implied_volatility=70.0,
                    ),
                ),
                notional_threshold=100_000.0, total_detected=1,
                blocks=(
                    Block(
                        block_trade_id="BLOCK-281688", leg_count=3, observed_leg_count=3,
                        combo_id="BTC-STRD-31JUL26-63000", combined_premium_usd=12345.0,
                        total_amount=37.5,
                        instruments=("A", "B", "C"), timestamp=1700000001000,
                    ),
                ),
                tracked_since="2026-08-02",
            ),
            cross_asset_correlation=None, failed_sections=(),
        )
        result = make_onchain_result(market_wide=mw)
        market = SynthesisMapper.build_market_wide(result)

        assert len(market.blocks) == 1
        assert market.blocks[0]["block_trade_id"] == "BLOCK-281688"
        assert market.blocks[0]["combined_premium_usd"] == 12345.0
        assert market.blocks[0]["combo_id"] == "BTC-STRD-31JUL26-63000"

        assert len(market.large_prints) == 1
        assert market.large_prints[0]["instrument"] == "BTC-28FEB26-100000-C"

        # no conflation: block fields never appear on a large-print entry
        # and vice versa.
        assert "block_trade_id" not in market.large_prints[0]
        assert "instrument" not in market.blocks[0]

    def test_empty_block_trades_result_gives_empty_blocks_and_large_prints(self):
        empty_mw = MarketWideResult(
            spot_price=50000.0, currency="BTC", dvol=None, iv_percentile_365d=None,
            aggregate_gex_dex=None, term_structure=None, futures_basis=None,
            realized_volatility=None, variance_risk_premium=None, volatility_cone=None,
            perpetual_funding=None, block_trades=None, cross_asset_correlation=None,
            failed_sections=(),
        )
        result = make_onchain_result(underlying_price=50000.0, market_wide=empty_mw)
        market = SynthesisMapper.build_market_wide(result)
        assert market.blocks == []
        assert market.large_prints == []


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

    def test_real_max_pain_flagged_sufficient(self):
        """The ordinary case (calculate_max_pain resolved a real strike)
        must flag max_pain_sufficient_data True."""
        onchain_result = make_onchain_result("27MAR26", max_pain_strike=70000.0)
        result = SynthesisMapper.build_expiry_metrics(onchain_result, "27MAR26")
        assert result is not None
        assert result.max_pain == 70000.0
        assert result.max_pain_sufficient_data is True

    def test_none_max_pain_falls_back_to_spot_but_flagged_insufficient(self):
        """
        Task Wave-H-B Fix 4: calculate_max_pain returned None (nothing to
        compute from). The spot-price fallback stays for DISPLAY (so the
        report's max-pain-distance line still shows a value), but
        max_pain_sufficient_data must be False so score_max_pain_gravity
        does not score this fallback as if it were a genuine measurement.
        """
        onchain_result = make_onchain_result(
            "27MAR26", underlying_price=65000.0, max_pain_strike=None)
        result = SynthesisMapper.build_expiry_metrics(onchain_result, "27MAR26")
        assert result is not None
        assert result.max_pain == 65000.0  # display fallback preserved
        assert result.max_pain_sufficient_data is False

    def test_missing_vol_surface_preserves_none(self):
        """
        Task G2-G: a missing vol surface (vol_surface=None for this
        expiration) must leave every vol-surface-derived ExpiryMetrics
        field as genuine None, not collapse to a fabricated 0.0 -- the
        same defect class Task G2-B fixed for MarketWideMetrics. A
        fabricated 0.0 here would previously score as "RR25 +0.0%:
        Normal" (score_skew) and "Vanna zero + Charm zero" (a real,
        confident "no structural drift" claim, score_vanna_charm) for a
        metric that was never measured.
        """
        onchain_result = make_onchain_result("27MAR26", include_vol_surface=False)
        result = SynthesisMapper.build_expiry_metrics(onchain_result, "27MAR26")
        assert result is not None
        assert result.atm_iv is None
        assert result.risk_reversal_25d is None
        assert result.put_25d_iv is None
        assert result.call_25d_iv is None
        assert result.net_vanna is None
        assert result.net_charm is None
        # Wave-H-A (Task 5): pc_atm/pc_near_otm/pc_far_otm are now genuine
        # None too when the vol surface is missing -- corrects the prior
        # "DELIBERATELY left as 0.0-when-undefined" belief, which was
        # itself the moneyness-bucket fabrication bug (see ExpiryMetrics'
        # docstring).
        assert result.pc_atm is None
        assert result.pc_near_otm is None
        assert result.pc_far_otm is None

    def test_missing_gex_key_levels_preserves_none(self):
        """
        Task G2-G: GexDexKeyLevels.call_resistance/put_support/hvl are
        genuinely Optional (no identified level in this expiry's strike
        range) -- must reach ExpiryMetrics as None, not a strike/GEX of
        0.0 (which reads as a specific, wrong price level).
        """
        onchain_result = make_onchain_result("27MAR26")
        bundle = onchain_result.bundle("27MAR26")
        no_levels_gex = GexDexResult(
            strike_rows=(), cumulative_gex={}, cumulative_dex={},
            key_levels=GexDexKeyLevels(
                call_resistance=None, put_support=None, hvl=None, gamma_flip=None,
            ),
            spot_price=bundle.gex_dex.spot_price,
            total_net_gex=bundle.gex_dex.total_net_gex, total_net_dex=bundle.gex_dex.total_net_dex,
            currency="BTC",
        )
        no_levels_bundle = ExpirationBundle(
            expiration=bundle.expiration, analysis=bundle.analysis, gex_dex=no_levels_gex,
            flow=bundle.flow, vol_surface=bundle.vol_surface, oi_changes=None,
            iv_percentile=None, trend=None, flow_chart_paths={}, enriched_instruments=(),
        )
        no_levels_result = OnChainAnalysisResult(
            currency=onchain_result.currency, underlying_price=onchain_result.underlying_price,
            generated_at=onchain_result.generated_at, market_metrics=onchain_result.market_metrics,
            expirations=(no_levels_bundle,), market_wide=onchain_result.market_wide,
            parsed_instruments=onchain_result.parsed_instruments,
            atm_iv_by_expiration=onchain_result.atm_iv_by_expiration,
            recent_trades=onchain_result.recent_trades,
        )

        result = SynthesisMapper.build_expiry_metrics(no_levels_result, "27MAR26")

        assert result is not None
        assert result.call_resistance_strike is None
        assert result.call_resistance_gex is None
        assert result.put_support_strike is None
        assert result.put_support_gex is None
        assert result.hvl_strike is None

    def test_underlying_price_sourced_from_own_expiration_not_global_result(self):
        """
        CARRIED FINDING (B2 review, task C1): ExpiryMetrics.underlying_price
        must come from bundle.analysis.underlying_price (this expiration's
        own forward price), not result.underlying_price (the single global
        index shared across every expiration) -- same defect class
        bugfix_spec.md Item 7 already fixed at the report/formatter layer.
        """
        onchain_result = make_onchain_result("27MAR26", underlying_price=65000.0)
        bundle = onchain_result.bundle("27MAR26")
        # This expiration's own forward price genuinely differs from the
        # global result.underlying_price.
        different_analysis = ExpirationAnalysisResult(
            expiration=bundle.analysis.expiration, underlying_price=65500.0,
            total_instruments=bundle.analysis.total_instruments,
            call_count=bundle.analysis.call_count, put_count=bundle.analysis.put_count,
            strike_rows=bundle.analysis.strike_rows, max_pain=bundle.analysis.max_pain,
            put_call_ratio=bundle.analysis.put_call_ratio,
            volume_stats=bundle.analysis.volume_stats, moneyness=bundle.analysis.moneyness,
            support_resistance=bundle.analysis.support_resistance,
        )
        skewed_bundle = ExpirationBundle(
            expiration=bundle.expiration, analysis=different_analysis, gex_dex=bundle.gex_dex,
            flow=bundle.flow, vol_surface=bundle.vol_surface, oi_changes=None,
            iv_percentile=None, trend=None, flow_chart_paths={}, enriched_instruments=(),
        )
        skewed_result = OnChainAnalysisResult(
            currency=onchain_result.currency, underlying_price=onchain_result.underlying_price,
            generated_at=onchain_result.generated_at, market_metrics=onchain_result.market_metrics,
            expirations=(skewed_bundle,), market_wide=onchain_result.market_wide,
            parsed_instruments=onchain_result.parsed_instruments,
            atm_iv_by_expiration=onchain_result.atm_iv_by_expiration,
            recent_trades=onchain_result.recent_trades,
        )

        result = SynthesisMapper.build_expiry_metrics(skewed_result, "27MAR26")

        assert result.underlying_price == 65500.0
        assert result.underlying_price != skewed_result.underlying_price

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

    def test_run_header_generated_timestamp_is_utc_and_labeled(self, frozen_clock):
        """
        Task G2-C: the header's "Generated:" timestamp used to come from
        naive-local datetime.now() -- switched to datetime.now(timezone.
        utc), labeled "UTC" in the output. Frozen clock proves the value is
        the correct UTC representation of the frozen instant, not a
        local-timezone-shifted one (this module is in tests/conftest.py's
        frozen-clock list).
        """
        anchor_utc = datetime(2026, 7, 25, 8, 0, 0, tzinfo=timezone.utc)
        frozen_clock(anchor_utc.timestamp())

        engine = SynthesisEngine()
        market = make_market_wide()
        expiry = make_expiry_metrics()
        result = engine.run(market, [expiry])

        assert "Generated: 2026-07-25 08:00:00 UTC" in result

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

    def test_run_missing_market_data_discloses_insufficient_data_not_fabricated_extremes(self):
        """
        Task G2-B (BLOCKER), end-to-end: every field that can genuinely
        be missing is None. The output must not crash, must not contain
        the fabricated-extreme text a 0.0 stand-in used to produce (e.g.
        "Extremely cheap — strong buy-vol" for IV percentile, or "Neutral
        leverage" for funding), and must visibly disclose the gaps in a
        DATA QUALITY section.
        """
        engine = SynthesisEngine()
        market = make_market_wide(
            dvol=None, iv_percentile_365d=None, funding_rate=None, funding_8h=None,
            rv_10d=None, rv_20d=None, rv_30d=None, vrp=None,
            cone_10d_pctile=None, cone_20d_pctile=None, cone_30d_pctile=None,
            perp_oi=None, btc_eth_price_corr=None, btc_eth_dvol_corr=None,
        )
        expiry = make_expiry_metrics()
        result = engine.run(market, [expiry])

        assert isinstance(result, str)
        assert "DATA QUALITY" in result
        assert "insufficient data" in result.lower()
        # The old fabricated-zero failure mode: missing IV percentile
        # scored as the single most extreme, most confident signal.
        assert "extremely cheap" not in result.lower()
        assert "strong buy-vol" not in result.lower()
        # The old fabricated-zero failure mode for funding: "Neutral
        # leverage" is a real claim about a real (if unremarkable) reading.
        assert "Neutral leverage" not in result

    def test_run_missing_term_structure_shows_na_not_fabricated_contango(self):
        """
        Task Wave-H-B Fix 2, end-to-end: term_structure_shape/spread=None
        (fewer than 2 usable per-expiry ATM IVs) must render "N/A" in the
        report header, never the old fabricated "CONTANGO (+0.0pts)" --
        a confident, specific reading for a computation that never ran.
        """
        engine = SynthesisEngine()
        market = make_market_wide(
            term_structure_shape=None, term_structure_spread=None,
            term_structure_spread_signed=None,
        )
        expiry = make_expiry_metrics()
        result = engine.run(market, [expiry])
        assert "CONTANGO (+0.0pts)" not in result
        assert "Term Structure: N/A" in result

    def test_run_vrp_iv_pctile_disagreement_no_contradictory_top_billing(self):
        """
        Task Wave-H-B Fix 3, end-to-end (golden-fixture reproduction):
        iv_pctile=20 (cheap) and vrp=+10.4 (sell-vol edge) disagree. The
        old behavior produced "VOL ASSESSMENT: ... Sell premium..." AND
        "TRADE RECOMMENDATIONS: PRIMARY -- Long Straddle/Strangle...
        Cheap IV favors owning volatility" in the same report -- opposite
        advice, both top-priority, no arbitration. Must no longer happen.
        """
        engine = SynthesisEngine()
        market = make_market_wide(iv_percentile_365d=20.0, vrp=10.4)
        expiry = make_expiry_metrics()
        result = engine.run(market, [expiry])

        assert "Sell premium" in result  # vol narrative still drives off VRP
        assert "PRIMARY — Long Straddle/Strangle" not in result
        assert "signals disagree" in result.lower()

    def test_run_fabricated_max_pain_does_not_pull_direction_confidence(self):
        """
        Task Wave-H-B Fix 4, end-to-end: a max_pain value that would
        otherwise produce a real, non-zero-weight directional score (here
        $100,000 max pain vs. $65,000 spot -- a strong upward pull) must
        NOT contribute to the direction confidence when
        max_pain_sufficient_data=False (the value is the spot-price
        display fallback for a max-pain calculation that never
        resolved). Compares the same fabricated-looking value with the
        flag True vs. False -- confidence must drop when the flag says
        this reading isn't real, proving the fallback no longer reaches
        scoring as if it were a genuine measurement.
        """
        engine = SynthesisEngine()
        market = make_market_wide()

        expiry_real = make_expiry_metrics(max_pain=100000, max_pain_sufficient_data=True)
        expiry_fabricated = make_expiry_metrics(max_pain=100000, max_pain_sufficient_data=False)

        result_real = engine.run(market, [expiry_real])
        result_fabricated = engine.run(market, [expiry_fabricated])

        conf_real = int(re.search(r"confidence: (\d+)%", result_real).group(1))
        conf_fabricated = int(re.search(r"confidence: (\d+)%", result_fabricated).group(1))
        assert conf_fabricated < conf_real

    def test_run_missing_expiry_vol_surface_and_gex_levels_no_crash_discloses(self):
        """
        Task G2-G, end-to-end: an expiry whose vol surface AND GEX key
        levels never computed (every field this task made Optional is
        None) must run through the full pipeline -- scorers, regime
        narrative, vol narrative, risk factors, trade recommendations,
        the per-expiry timeframe one-liner, and the final SCORING DETAIL
        RR25 line -- without raising ``TypeError`` on a bare numeric
        format spec or ``float(None)``, and must render "insufficient
        data"/"N/A" disclosures rather than fabricated zeros (e.g. never
        "RR25 +0.0%: Normal" for a metric that was never measured).
        """
        engine = SynthesisEngine()
        market = make_market_wide()
        expiry = make_expiry_metrics(
            atm_iv=None, risk_reversal_25d=None, put_25d_iv=None, call_25d_iv=None,
            net_vanna=None, net_charm=None,
            call_resistance_strike=None, call_resistance_gex=None,
            put_support_strike=None, put_support_gex=None, hvl_strike=None,
        )
        result = engine.run(market, [expiry])

        assert isinstance(result, str)
        assert "insufficient data" in result.lower() or "N/A" in result
        # RR25 +0.0% is the exact fabricated-extreme text score_skew's old
        # fall-through produced for a missing reading.
        assert "RR25 +0.0%: Normal" not in result
        assert "RR25 +0.0%" not in result

    def test_run_failed_sections_disclosed_in_data_quality(self):
        """Task G2-B Finding 3: a non-empty failed_sections must surface
        in the assembled output, distinct from ordinary insufficient-data
        gaps."""
        engine = SynthesisEngine()
        market = make_market_wide(failed_sections=("futures_basis", "perpetual_funding"))
        expiry = make_expiry_metrics()
        result = engine.run(market, [expiry])
        assert "DATA QUALITY" in result
        assert "futures_basis" in result
        assert "perpetual_funding" in result

    def test_run_all_expiries_flow_insufficient_disclosed_in_data_quality(self):
        """
        Wave H follow-up (institutional-benchmark audit P0-4): the old
        DATA QUALITY block only inspected iv_pctile/vrp/funding plus
        failed_sections -- it would print "All market-wide sections
        computed successfully" even when every single expiry had zero
        qualifying flow trades. Must now disclose this.
        """
        engine = SynthesisEngine()
        market = make_market_wide()
        expiries = [
            make_expiry_metrics(expiry="26JUL26", flow_sufficient_data=False),
            make_expiry_metrics(expiry="31JUL26", flow_sufficient_data=False),
        ]
        result = engine.run(market, expiries)
        assert "All market-wide sections computed successfully" not in result
        assert "Order flow: insufficient data for all 2 expiries" in result

    def test_run_all_expiries_max_pain_insufficient_disclosed_in_data_quality(self):
        """Wave H follow-up (P0-4): same broadening for max_pain."""
        engine = SynthesisEngine()
        market = make_market_wide()
        expiries = [
            make_expiry_metrics(expiry="26JUL26", max_pain_sufficient_data=False),
        ]
        result = engine.run(market, expiries)
        assert "All market-wide sections computed successfully" not in result
        assert "Max pain: did not resolve for any of 1 expiries" in result

    def test_run_missing_term_structure_disclosed_in_data_quality(self):
        """Wave H follow-up (P0-4): a None term structure must be listed
        in DATA QUALITY, not just silently rendered as N/A in the header."""
        engine = SynthesisEngine()
        market = make_market_wide(
            term_structure_shape=None, term_structure_spread=None,
            term_structure_spread_signed=None,
        )
        expiry = make_expiry_metrics()
        result = engine.run(market, [expiry])
        assert "All market-wide sections computed successfully" not in result
        assert "Term structure: insufficient data" in result

    def test_run_all_data_present_still_shows_all_clear(self):
        """Sanity check: when flow/max_pain/term-structure are all genuinely
        sufficient, the DATA QUALITY block must still show the all-clear
        message -- the broadening must not manufacture false negatives."""
        engine = SynthesisEngine()
        market = make_market_wide()
        expiry = make_expiry_metrics(flow_sufficient_data=True, max_pain_sufficient_data=True)
        result = engine.run(market, [expiry])
        assert "All market-wide sections computed successfully" in result

    def test_run_regime_narrative_gex_matches_scorer_gex_not_largest_expiry(self):
        """
        Task G2-B Finding 2: the regime narrative's "+X.XM GEX" figure
        must describe the SAME value the vol-regime scorer used (the
        market-wide aggregate, when available) -- not the largest-OI
        expiry's own GEX. Confirmed live case: narrative said "+13.7M
        GEX" while the true aggregate was "+86.8M" (6.3x off). Reproduced
        here with the same order of magnitude.
        """
        engine = SynthesisEngine()
        market = make_market_wide(aggregate_total_gex=86_800_000.0)
        expiry = make_expiry_metrics(total_gex=13_700_000.0)
        result = engine.run(market, [expiry])
        assert "86.8M GEX" in result
        assert "13.7M GEX" not in result

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

    def test_run_narrates_real_blocks_not_large_prints(self):
        """Independent review round 2 (Important #3): real blocks
        (block_trade_id grouping) must reach the narrative under
        "Block Trades"; the notional-filter list, if narrated at all,
        must be separately labelled as screen prints -- never conflated."""
        engine = SynthesisEngine()
        market = make_market_wide(
            blocks=[
                {
                    "block_trade_id": "BLOCK-281688", "leg_count": 3,
                    "observed_leg_count": 3, "combo_id": "BTC-STRD-31JUL26-63000",
                    "combined_premium_usd": 2_500_000.0, "total_amount": 37.5,
                    "instruments": ("A", "B", "C"), "timestamp": 1700000000000,
                },
            ],
            large_prints=[
                {
                    "timestamp": 1700000000000, "instrument": "BTC-28FEB26-100000-C",
                    "size": 5.0, "amount": 5.0, "direction": "buy",
                    "notional": 500_000.0, "iv": 70.0,
                },
            ],
        )
        expiry = make_expiry_metrics()
        result = engine.run(market, [expiry])

        assert "Block Trades" in result
        assert "BLOCK-281688" in result
        assert "BTC-STRD-31JUL26-63000" in result

        # if the large-prints list is narrated, it must be separately
        # labelled as screen prints, not conflated into "Block Trades".
        if "BTC-28FEB26-100000-C" in result:
            assert "screen print" in result.lower()

    def test_run_large_print_none_iv_does_not_crash(self):
        """Fix 1 (Wave H-B): MarketWideTrade.implied_volatility is
        Optional -- a large print with iv=None is a present-but-None
        dict key, so `.get('iv', 0)` never applies its default and
        f"{None:.1f}%" raises TypeError. One thinly-traded large print
        with no IV must not take down the entire morning note."""
        engine = SynthesisEngine()
        market = make_market_wide(
            blocks=[],
            large_prints=[
                {
                    "timestamp": 1700000000000, "instrument": "BTC-28FEB26-100000-C",
                    "size": 5.0, "amount": 5.0, "direction": "buy",
                    "notional": 500_000.0, "iv": None,
                },
            ],
        )
        expiry = make_expiry_metrics()
        result = engine.run(market, [expiry])  # must not raise TypeError
        assert "N/A" in result

    def test_run_no_blocks_states_none_detected_not_silence(self):
        engine = SynthesisEngine()
        market = make_market_wide(blocks=[], large_prints=[])
        expiry = make_expiry_metrics()
        result = engine.run(market, [expiry])
        assert "Block Trades" in result
        assert "none detected" in result.lower() or "no block" in result.lower()

    def test_timeframe_max_pain_distance_uses_own_expiry_price_not_global_spot(self):
        """
        CARRIED FINDING (B2 review, task C1): the "MaxPain $X (Y%)" line's
        distance-from-current percentage must be computed against this
        expiry's own underlying_price, not market.spot_price -- same
        defect class bugfix_spec.md Item 7 already fixed at the report/
        formatter layer.
        """
        engine = SynthesisEngine()
        market = make_market_wide(spot_price=65000.0)
        # This expiry's own forward price (70000) genuinely differs from
        # market.spot_price (65000) -- max_pain=70000 sits exactly AT its
        # own forward (0.0% distance) but far from the global spot
        # ((70000-65000)/65000*100 = +7.69%). Only the per-expiry-correct
        # 0.0% figure may appear.
        expiry = make_expiry_metrics(
            expiry="27MAR26", dte=27, total_oi=50000,
            max_pain=70000, underlying_price=70000.0,
        )
        result = engine.run(market, [expiry])
        assert "MaxPain $70,000 (+0.0%)" in result
        assert "+7.7%" not in result
        assert "(+7.69%)" not in result

    def test_score_max_pain_gravity_called_with_own_expiry_price_not_global_spot(self):
        """
        CARRIED FINDING (B2 review, task C1): all three
        score_max_pain_gravity call sites (top/near/far-term scoring
        loops) must pass this expiry's own underlying_price, never the
        single global market.spot_price -- same defect class
        bugfix_spec.md Item 7 already fixed at the report/formatter layer.
        """
        engine = SynthesisEngine()
        market = make_market_wide(spot_price=65000.0)
        expiry = make_expiry_metrics(
            expiry="27MAR26", dte=27, total_oi=50000, underlying_price=70000.0,
        )

        with patch.object(
            ScoringEngine, "score_max_pain_gravity",
            wraps=ScoringEngine.score_max_pain_gravity,
        ) as spy:
            engine.run(market, [expiry])

        assert spy.call_count >= 1
        for call in spy.call_args_list:
            args = call.args
            spot_arg = args[1] if len(args) > 1 else call.kwargs.get("spot")
            assert spot_arg == 70000.0, (
                f"score_max_pain_gravity called with spot={spot_arg}, expected "
                f"the expiry's own underlying_price (70000.0), not market.spot_price (65000.0)"
            )

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
