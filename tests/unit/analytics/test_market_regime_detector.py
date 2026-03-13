"""
Tests for the redesigned MarketRegimeDetector.

Covers all 5 scoring functions, confidence formula, regime thresholds,
and ADX override. Each test uses exact bucket boundary values to verify
the scoring rules from the spec.
"""
import pytest
from coding.core.analytics.market_regime_detector import MarketRegimeDetector


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def make_detector():
    return MarketRegimeDetector()


def minimal_indicators(overrides=None):
    """Minimal technical indicators dict — all None by default, apply overrides."""
    base = {
        "sma_50": None, "sma_200": None, "adx": None,
        "plus_di": None, "minus_di": None,
        "ema_50": None, "rsi": None,
        "macd": None, "macd_signal": None, "macd_histogram": None,
        "atr_percentile": None,
    }
    if overrides:
        base.update(overrides)
    return base


def minimal_onchain(overrides=None):
    base = {
        "wings_skew": None, "funding_rate": None, "oi_direction": 0,
        "dvol_percentile": None, "dvol_term_structure_ratio": None,
        "vrp_percentage": None,
    }
    if overrides:
        base.update(overrides)
    return base


def minimal_external(overrides=None):
    base = {
        "fear_greed": {"value": 50, "classification": "Neutral"},
        "fear_greed_7d_avg": 50.0,
        "btc_dominance": 50.0,
        "market_cap_change_24h": 0.0,
    }
    if overrides:
        base.update(overrides)
    return base


def minimal_velocity(overrides=None):
    base = {"ema_50_velocity": 0.0, "rsi_velocity": 0.0, "macd_histogram_velocity": 0.0}
    if overrides:
        base.update(overrides)
    return base


# ═══════════════════════════════════════════════════════════════════════════════
# TREND COMPONENT
# ═══════════════════════════════════════════════════════════════════════════════

class TestTrendComponent:

    def test_di_strong_bullish_spread(self):
        """DI+ - DI- > 15 contributes +35."""
        d = make_detector()
        ind = minimal_indicators({"plus_di": 30.0, "minus_di": 10.0, "adx": 30.0})
        score = d._score_trend_component(ind, 100.0)
        # DI spread = 20 → +35; no MA data → 0; ADX 30 → ×1.0; no velocity
        assert score == pytest.approx(35.0, rel=0.01)

    def test_di_strong_bearish_spread(self):
        """DI- - DI+ > 15 contributes -35."""
        d = make_detector()
        ind = minimal_indicators({"plus_di": 10.0, "minus_di": 30.0, "adx": 30.0})
        score = d._score_trend_component(ind, 100.0)
        assert score == pytest.approx(-35.0, rel=0.01)

    def test_di_no_conviction(self):
        """DI spread < 5 → 0 from DI step."""
        d = make_detector()
        ind = minimal_indicators({"plus_di": 22.0, "minus_di": 20.0, "adx": 30.0})
        score = d._score_trend_component(ind, 100.0)
        # spread = 2 → 0 from DI; no MA → 0; ADX ×1.0
        assert score == pytest.approx(0.0, abs=0.01)

    def test_ma_clean_uptrend(self):
        """price > SMA50 > SMA200 → +20."""
        d = make_detector()
        ind = minimal_indicators({"sma_50": 110.0, "sma_200": 100.0, "adx": 30.0})
        score = d._score_trend_component(ind, 120.0)
        # No DI → 0; MA +20; ADX 30 → ×1.0
        assert score == pytest.approx(20.0, rel=0.01)

    def test_ma_pullback_in_uptrend(self):
        """SMA50 > SMA200 but price < SMA50 → +10 (pullback)."""
        d = make_detector()
        ind = minimal_indicators({"sma_50": 110.0, "sma_200": 100.0, "adx": 30.0})
        score = d._score_trend_component(ind, 105.0)
        assert score == pytest.approx(10.0, rel=0.01)

    def test_ma_clean_downtrend(self):
        """price < SMA50 < SMA200 → -20."""
        d = make_detector()
        ind = minimal_indicators({"sma_50": 90.0, "sma_200": 100.0, "adx": 30.0})
        score = d._score_trend_component(ind, 80.0)
        assert score == pytest.approx(-20.0, rel=0.01)

    def test_ma_bounce_in_downtrend(self):
        """SMA50 < SMA200, price > SMA50 → -10 (bear bounce)."""
        d = make_detector()
        ind = minimal_indicators({"sma_50": 90.0, "sma_200": 100.0, "adx": 30.0})
        score = d._score_trend_component(ind, 95.0)
        assert score == pytest.approx(-10.0, rel=0.01)

    def test_adx_multiplier_very_strong(self):
        """ADX > 40 applies ×1.4 to DI+MA sum."""
        d = make_detector()
        # DI +20 → +35; MA clean uptrend → +20; sum = +55; ADX 45 → ×1.4 = +77
        ind = minimal_indicators({
            "plus_di": 35.0, "minus_di": 15.0,
            "sma_50": 110.0, "sma_200": 100.0,
            "adx": 45.0,
        })
        score = d._score_trend_component(ind, 120.0)
        assert score == pytest.approx(min(100, 55 * 1.4), rel=0.01)

    def test_adx_multiplier_weak_dampens(self):
        """ADX < 20 applies ×0.3, dampening the signal."""
        d = make_detector()
        ind = minimal_indicators({
            "plus_di": 35.0, "minus_di": 15.0,
            "sma_50": 110.0, "sma_200": 100.0,
            "adx": 15.0,
        })
        score = d._score_trend_component(ind, 120.0)
        # (35+20) × 0.3 = 16.5
        assert score == pytest.approx(55 * 0.3, rel=0.01)

    def test_adx_multiplier_missing_uses_07(self):
        """ADX missing → ×0.7."""
        d = make_detector()
        ind = minimal_indicators({"sma_50": 110.0, "sma_200": 100.0})
        score = d._score_trend_component(ind, 120.0)
        # +20 × 0.7 = 14
        assert score == pytest.approx(20 * 0.7, rel=0.01)

    def test_ema_velocity_bullish_adds_10(self):
        """EMA rising > 0.2% → +10 after multiplier."""
        d = make_detector()
        ind = minimal_indicators({"adx": 30.0})
        vel = minimal_velocity({"ema_50_velocity": 0.5})
        score = d._score_trend_component(ind, 100.0, velocity=vel)
        # No DI, no MA → 0 × 1.0 = 0; +10 from velocity
        assert score == pytest.approx(10.0, rel=0.01)

    def test_ema_velocity_bearish_subtracts_10(self):
        d = make_detector()
        ind = minimal_indicators({"adx": 30.0})
        vel = minimal_velocity({"ema_50_velocity": -0.5})
        score = d._score_trend_component(ind, 100.0, velocity=vel)
        assert score == pytest.approx(-10.0, rel=0.01)

    def test_ema_velocity_flat_no_change(self):
        d = make_detector()
        ind = minimal_indicators({"adx": 30.0})
        vel = minimal_velocity({"ema_50_velocity": 0.1})  # < 0.2 threshold
        score = d._score_trend_component(ind, 100.0, velocity=vel)
        assert score == pytest.approx(0.0, abs=0.01)

    def test_velocity_none_no_crash(self):
        """velocity_indicators=None → no crash, no velocity contribution."""
        d = make_detector()
        ind = minimal_indicators({"sma_50": 110.0, "sma_200": 100.0, "adx": 30.0})
        score_no_vel = d._score_trend_component(ind, 120.0, velocity=None)
        assert isinstance(score_no_vel, float)

    def test_missing_di_falls_back_to_ma_only(self):
        """DI missing → skip Step 1, use MA structure."""
        d = make_detector()
        ind = minimal_indicators({"sma_50": 110.0, "sma_200": 100.0, "adx": 30.0})
        score = d._score_trend_component(ind, 120.0)
        # No DI → 0; MA +20; ×1.0 = +20
        assert score == pytest.approx(20.0, rel=0.01)


# ═══════════════════════════════════════════════════════════════════════════════
# VOLATILITY COMPONENT
# ═══════════════════════════════════════════════════════════════════════════════

class TestVolatilityComponent:

    def test_dvol_low_percentile_bullish(self):
        """DVOL < 20th pct → +40."""
        d = make_detector()
        score = d._score_volatility_component(
            minimal_indicators(),
            minimal_onchain({"dvol_percentile": 10.0})
        )
        assert score == pytest.approx(40.0, rel=0.01)

    def test_dvol_high_percentile_bearish(self):
        """DVOL > 80th pct → -40."""
        d = make_detector()
        score = d._score_volatility_component(
            minimal_indicators(),
            minimal_onchain({"dvol_percentile": 85.0})
        )
        assert score == pytest.approx(-40.0, rel=0.01)

    def test_dvol_average_percentile_neutral(self):
        """DVOL 40–60th pct → 0."""
        d = make_detector()
        score = d._score_volatility_component(
            minimal_indicators(),
            minimal_onchain({"dvol_percentile": 50.0})
        )
        assert score == pytest.approx(0.0, abs=0.01)

    def test_term_structure_contango_bullish(self):
        """ratio < 0.80 → +20 (near vol cheaper than avg)."""
        d = make_detector()
        score = d._score_volatility_component(
            minimal_indicators(),
            minimal_onchain({"dvol_term_structure_ratio": 0.75})
        )
        assert score == pytest.approx(20.0, rel=0.01)

    def test_term_structure_backwardation_bearish(self):
        """ratio > 1.25 → -25 (crisis premium)."""
        d = make_detector()
        score = d._score_volatility_component(
            minimal_indicators(),
            minimal_onchain({"dvol_term_structure_ratio": 1.30})
        )
        assert score == pytest.approx(-25.0, rel=0.01)

    def test_vrp_expensive_bearish(self):
        """VRP > 20% → -20 (hedgers paying up)."""
        d = make_detector()
        score = d._score_volatility_component(
            minimal_indicators(),
            minimal_onchain({"vrp_percentage": 25.0})
        )
        assert score == pytest.approx(-20.0, rel=0.01)

    def test_vrp_very_cheap_bullish(self):
        """VRP < -20% → +20 (extreme complacency)."""
        d = make_detector()
        score = d._score_volatility_component(
            minimal_indicators(),
            minimal_onchain({"vrp_percentage": -25.0})
        )
        assert score == pytest.approx(20.0, rel=0.01)

    def test_all_dvol_missing_returns_zero(self):
        """No DVOL metrics → vol score = 0."""
        d = make_detector()
        score = d._score_volatility_component(minimal_indicators(), minimal_onchain())
        assert score == pytest.approx(0.0, abs=0.01)

    def test_combined_low_dvol_cheap_options_max_bullish(self):
        """Low DVOL pct + contango + cheap VRP → strongly bullish."""
        d = make_detector()
        score = d._score_volatility_component(
            minimal_indicators(),
            minimal_onchain({
                "dvol_percentile": 15.0,        # +40
                "dvol_term_structure_ratio": 0.75,  # +20
                "vrp_percentage": -25.0,         # +20
            })
        )
        assert score == pytest.approx(80.0, rel=0.01)


# ═══════════════════════════════════════════════════════════════════════════════
# MOMENTUM COMPONENT
# ═══════════════════════════════════════════════════════════════════════════════

class TestMomentumComponent:

    def test_rsi_strong_momentum_60_to_70(self):
        """RSI 65 → +35 (strongest bullish range)."""
        d = make_detector()
        score = d._score_momentum_component(minimal_indicators({"rsi": 65.0}))
        assert score == pytest.approx(35.0, rel=0.01)

    def test_rsi_overbought_above_70(self):
        """RSI 75 → +25 (overbought, slightly less than peak)."""
        d = make_detector()
        score = d._score_momentum_component(minimal_indicators({"rsi": 75.0}))
        assert score == pytest.approx(25.0, rel=0.01)

    def test_rsi_overbought_less_than_strong_momentum(self):
        """RSI > 70 scores +25, RSI 60–70 scores +35. Overbought is penalised."""
        d = make_detector()
        score_overbought = d._score_momentum_component(minimal_indicators({"rsi": 80.0}))
        score_strong = d._score_momentum_component(minimal_indicators({"rsi": 65.0}))
        assert score_overbought < score_strong

    def test_rsi_oversold_less_bearish_than_bear_zone(self):
        """RSI < 30 → -15, less bearish than RSI 30–40 → -30 (contrarian)."""
        d = make_detector()
        score_oversold = d._score_momentum_component(minimal_indicators({"rsi": 25.0}))
        score_bear = d._score_momentum_component(minimal_indicators({"rsi": 35.0}))
        assert score_oversold > score_bear
        assert score_oversold == pytest.approx(-15.0, rel=0.01)

    def test_rsi_velocity_accelerating_adds_10(self):
        """RSI rose > 8pts → +10 bonus."""
        d = make_detector()
        ind = minimal_indicators({"rsi": 55.0})
        vel = minimal_velocity({"rsi_velocity": 10.0})
        score_with_vel = d._score_momentum_component(ind, velocity=vel)
        score_no_vel = d._score_momentum_component(ind)
        assert score_with_vel == score_no_vel + 10.0

    def test_rsi_velocity_decelerating_subtracts_10(self):
        """RSI fell > 8pts → -10 penalty."""
        d = make_detector()
        ind = minimal_indicators({"rsi": 55.0})
        vel = minimal_velocity({"rsi_velocity": -10.0})
        score_with_vel = d._score_momentum_component(ind, velocity=vel)
        score_no_vel = d._score_momentum_component(ind)
        assert score_with_vel == score_no_vel - 10.0

    def test_macd_crossover_bullish(self):
        """MACD > signal → +25."""
        d = make_detector()
        ind = minimal_indicators({"macd": 10.0, "macd_signal": 5.0})
        score = d._score_momentum_component(ind)
        assert score == pytest.approx(25.0, rel=0.01)

    def test_macd_crossover_bearish(self):
        """MACD < signal → -25."""
        d = make_detector()
        ind = minimal_indicators({"macd": 5.0, "macd_signal": 10.0})
        score = d._score_momentum_component(ind)
        assert score == pytest.approx(-25.0, rel=0.01)

    def test_histogram_velocity_building_independent_of_crossover(self):
        """
        KEY FIX TEST: MACD above signal (+25) but histogram magnitude growing (+15).
        Both can fire independently — sum = +40, not forced ±50.
        """
        d = make_detector()
        ind = minimal_indicators({"macd": 10.0, "macd_signal": 5.0})
        vel = minimal_velocity({"macd_histogram_velocity": 5.0})  # magnitude growing
        score = d._score_momentum_component(ind, velocity=vel)
        assert score == pytest.approx(40.0, rel=0.01)

    def test_histogram_velocity_fading_reduces_crossover_signal(self):
        """
        KEY FIX TEST: MACD above signal (+25) but histogram magnitude shrinking (-15).
        Net = +10. Proves independence from crossover.
        """
        d = make_detector()
        ind = minimal_indicators({"macd": 10.0, "macd_signal": 5.0})
        vel = minimal_velocity({"macd_histogram_velocity": -5.0})  # magnitude shrinking
        score = d._score_momentum_component(ind, velocity=vel)
        assert score == pytest.approx(10.0, rel=0.01)


# ═══════════════════════════════════════════════════════════════════════════════
# ON-CHAIN COMPONENT
# ═══════════════════════════════════════════════════════════════════════════════

class TestOnChainComponent:

    def test_wings_skew_puts_very_expensive(self):
        """put_iv >> call_iv (skew > 10pp) → -40 (fear)."""
        d = make_detector()
        score = d._score_onchain_component(minimal_onchain({"wings_skew": 12.0}))
        assert score == pytest.approx(-40.0, rel=0.01)

    def test_wings_skew_calls_very_expensive(self):
        """call_iv >> put_iv (skew < -10pp) → +40 (bullish positioning)."""
        d = make_detector()
        score = d._score_onchain_component(minimal_onchain({"wings_skew": -12.0}))
        assert score == pytest.approx(40.0, rel=0.01)

    def test_wings_skew_balanced(self):
        """Skew in -5 to +5pp range → 0."""
        d = make_detector()
        score = d._score_onchain_component(minimal_onchain({"wings_skew": 2.0}))
        assert score == pytest.approx(0.0, abs=0.01)

    def test_funding_rate_very_bullish_fixed_units(self):
        """
        UNIT FIX TEST: funding_rate = 0.06 (0.06% per 8h, > 0.05 threshold) → +30.
        Old code with /100 would have made this 0.0006, landing in neutral zone.
        """
        d = make_detector()
        score = d._score_onchain_component(minimal_onchain({"funding_rate": 0.06}))
        assert score == pytest.approx(30.0, rel=0.01)

    def test_funding_rate_neutral_zone(self):
        """0.01% funding (typical) → stays in neutral zone → 0."""
        d = make_detector()
        score = d._score_onchain_component(minimal_onchain({"funding_rate": 0.01}))
        assert score == pytest.approx(0.0, abs=0.01)

    def test_funding_rate_very_bearish(self):
        """Negative funding < -0.05% → -30."""
        d = make_detector()
        score = d._score_onchain_component(minimal_onchain({"funding_rate": -0.06}))
        assert score == pytest.approx(-30.0, rel=0.01)

    def test_oi_direction_confirmed_longs(self):
        """price_up + oi_rising → +20."""
        d = make_detector()
        score = d._score_onchain_component(minimal_onchain({"oi_direction": 20}))
        assert score == pytest.approx(20.0, rel=0.01)

    def test_oi_direction_confirmed_shorts(self):
        """price_down + oi_rising → -20."""
        d = make_detector()
        score = d._score_onchain_component(minimal_onchain({"oi_direction": -20}))
        assert score == pytest.approx(-20.0, rel=0.01)

    def test_all_missing_returns_zero(self):
        """No onchain signals → 0."""
        d = make_detector()
        score = d._score_onchain_component(minimal_onchain())
        assert score == pytest.approx(0.0, abs=0.01)


# ═══════════════════════════════════════════════════════════════════════════════
# SENTIMENT COMPONENT
# ═══════════════════════════════════════════════════════════════════════════════

class TestSentimentComponent:

    def test_fgi_7d_avg_used_when_available(self):
        """7d avg overrides spot F&G value."""
        d = make_detector()
        ext = minimal_external({
            "fear_greed": {"value": 20, "classification": "Extreme Fear"},
            "fear_greed_7d_avg": 65.0,  # Should win → +35
        })
        score = d._score_sentiment_component(ext, currency="BTC")
        # 65 → greed → +35; btc_dom 50 → 0; mc_change 0 → 0
        assert score == pytest.approx(35.0, rel=0.01)

    def test_fgi_fallback_to_spot_when_no_7d_avg(self):
        """Without 7d avg, falls back to fear_greed.value."""
        d = make_detector()
        ext = minimal_external({"fear_greed_7d_avg": None, "fear_greed": {"value": 65, "classification": "Greed"}})
        score = d._score_sentiment_component(ext, currency="BTC")
        assert score == pytest.approx(35.0, rel=0.01)

    def test_extreme_fear_contrarian_in_weak_trend(self):
        """F&G < 25 in weak/no trend → +25 contrarian signal."""
        d = make_detector()
        ext = minimal_external({"fear_greed_7d_avg": 15.0})
        score = d._score_sentiment_component(ext, currency="BTC", adx=15.0, composite_trend=-10.0)
        assert score > 0
        # F&G +25; btc_dom neutral 0; mc_change 0 → +25
        assert score == pytest.approx(25.0, rel=0.01)

    def test_extreme_fear_confirms_strong_bearish_trend(self):
        """F&G < 25 during strong bearish trend → -15 (not contrarian)."""
        d = make_detector()
        ext = minimal_external({"fear_greed_7d_avg": 15.0})
        score = d._score_sentiment_component(
            ext, currency="BTC", adx=40.0, composite_trend=-50.0
        )
        assert score < 0

    def test_btc_dominance_bullish_for_btc(self):
        """BTC dom > 55% → +10 for BTC."""
        d = make_detector()
        ext = minimal_external({"fear_greed_7d_avg": 50.0, "btc_dominance": 58.0, "market_cap_change_24h": 0.0})
        score = d._score_sentiment_component(ext, currency="BTC")
        assert score == pytest.approx(10.0, rel=0.01)

    def test_btc_dominance_inverted_for_eth(self):
        """BTC dom > 55% → -10 for ETH (capital NOT in alts)."""
        d = make_detector()
        ext = minimal_external({"fear_greed_7d_avg": 50.0, "btc_dominance": 58.0, "market_cap_change_24h": 0.0})
        score = d._score_sentiment_component(ext, currency="ETH")
        assert score == pytest.approx(-10.0, rel=0.01)

    def test_low_btc_dominance_bearish_for_btc(self):
        """BTC dom < 45% → -10 for BTC."""
        d = make_detector()
        ext = minimal_external({"fear_greed_7d_avg": 50.0, "btc_dominance": 42.0, "market_cap_change_24h": 0.0})
        score = d._score_sentiment_component(ext, currency="BTC")
        assert score == pytest.approx(-10.0, rel=0.01)

    def test_low_btc_dominance_bullish_for_eth(self):
        """BTC dom < 45% → +10 for ETH (alt season)."""
        d = make_detector()
        ext = minimal_external({"fear_greed_7d_avg": 50.0, "btc_dominance": 42.0, "market_cap_change_24h": 0.0})
        score = d._score_sentiment_component(ext, currency="ETH")
        assert score == pytest.approx(10.0, rel=0.01)

    def test_market_cap_change_bullish(self):
        """Broad market +4% → +10."""
        d = make_detector()
        ext = minimal_external({"fear_greed_7d_avg": 50.0, "market_cap_change_24h": 4.0})
        score = d._score_sentiment_component(ext, currency="BTC")
        # neutral F&G 0; neutral dom 0; mc +10
        assert score == pytest.approx(10.0, rel=0.01)

    def test_market_cap_change_bearish(self):
        """Broad market -4% → -10."""
        d = make_detector()
        ext = minimal_external({"fear_greed_7d_avg": 50.0, "market_cap_change_24h": -4.0})
        score = d._score_sentiment_component(ext, currency="BTC")
        assert score == pytest.approx(-10.0, rel=0.01)


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIDENCE FORMULA
# ═══════════════════════════════════════════════════════════════════════════════

class TestConfidenceFormula:

    def test_all_bullish_returns_100(self):
        """All 5 components bullish (score > 20) → 100%."""
        d = make_detector()
        scores = [50.0, 50.0, 50.0, 50.0, 50.0]
        assert d._calculate_confidence(scores) == pytest.approx(100.0, rel=0.01)

    def test_all_bearish_returns_100(self):
        """All 5 components bearish (score < -20) → 100%."""
        d = make_detector()
        scores = [-50.0, -50.0, -50.0, -50.0, -50.0]
        assert d._calculate_confidence(scores) == pytest.approx(100.0, rel=0.01)

    def test_all_neutral_returns_zero(self):
        """All components neutral (between -20 and +20) → 0%."""
        d = make_detector()
        scores = [0.0, 0.0, 0.0, 0.0, 0.0]
        assert d._calculate_confidence(scores) == pytest.approx(0.0, abs=0.01)

    def test_perfect_split_returns_zero(self):
        """
        FORMULA FIX TEST: Perfect split (equal bullish vs bearish weight) → 0%.
        Old formula would have returned ~36%.
        """
        d = make_detector()
        # trend(0.30) + vol(0.15) bullish = 0.45 weight bullish
        # momentum(0.20) + onchain(0.25) bearish = 0.45 weight bearish
        # sentiment(0.10) neutral
        scores = [50.0, 50.0, -50.0, -50.0, 0.0]
        conf = d._calculate_confidence(scores)
        # bullish = 0.30+0.15 = 0.45; bearish = 0.20+0.25 = 0.45
        # (0.45 - 0.45) * 100 = 0
        assert conf == pytest.approx(0.0, abs=1.0)

    def test_single_fringe_component_low_confidence(self):
        """
        FORMULA FIX TEST: Only sentiment (0.10 weight) bullish → 10% confidence.
        Old formula would have returned 100%.
        """
        d = make_detector()
        scores = [0.0, 0.0, 0.0, 0.0, 50.0]  # only sentiment bullish
        conf = d._calculate_confidence(scores)
        assert conf == pytest.approx(10.0, rel=0.01)

    def test_heavy_majority_high_confidence(self):
        """trend + onchain bullish (0.55 weight) → 55% confidence."""
        d = make_detector()
        scores = [50.0, 0.0, 0.0, 50.0, 0.0]  # trend(0.30) + onchain(0.25) bullish
        conf = d._calculate_confidence(scores)
        assert conf == pytest.approx(55.0, rel=0.01)


# ═══════════════════════════════════════════════════════════════════════════════
# REGIME CLASSIFICATION + ADX OVERRIDE
# ═══════════════════════════════════════════════════════════════════════════════

class TestRegimeClassification:

    def test_strong_bullish_threshold(self):
        """Composite ≥ 55 → Strong Bullish."""
        d = make_detector()
        assert d._classify_regime(60.0) == "Strong Bullish"

    def test_weak_bullish_threshold(self):
        """Composite 20–55 → Weak Bullish."""
        d = make_detector()
        assert d._classify_regime(30.0) == "Weak Bullish"

    def test_sideways_positive_side(self):
        """Composite +15 → Sideways (not Weak Bullish as old code had it)."""
        d = make_detector()
        assert d._classify_regime(15.0) == "Sideways"

    def test_sideways_negative_side(self):
        """Composite -15 → Sideways (symmetric)."""
        d = make_detector()
        assert d._classify_regime(-15.0) == "Sideways"

    def test_old_threshold_fixed_positive_side(self):
        """
        THRESHOLD FIX TEST: Score +22 was Sideways in old code (threshold was -15 to +30).
        New symmetric threshold: +22 → Weak Bullish.
        """
        d = make_detector()
        assert d._classify_regime(22.0) == "Weak Bullish"

    def test_weak_bearish_threshold(self):
        """Composite -55 to -20 → Weak Bearish."""
        d = make_detector()
        assert d._classify_regime(-30.0) == "Weak Bearish"

    def test_strong_bearish_threshold(self):
        """Composite < -55 → Strong Bearish."""
        d = make_detector()
        assert d._classify_regime(-60.0) == "Strong Bearish"

    def test_adx_override_bullish_di_in_sideways(self):
        """ADX > 25 + composite in Sideways + DI+ > DI- by 10 → Weak Bullish."""
        d = make_detector()
        result = d._classify_regime(5.0, adx=32.0, plus_di=30.0, minus_di=20.0)
        assert result == "Weak Bullish"

    def test_adx_override_bearish_di_in_sideways(self):
        """ADX > 25 + composite in Sideways + DI- > DI+ by 10 → Weak Bearish."""
        d = make_detector()
        result = d._classify_regime(-5.0, adx=32.0, plus_di=20.0, minus_di=30.0)
        assert result == "Weak Bearish"

    def test_adx_override_mixed_di_keeps_sideways(self):
        """ADX > 25 but DI spread ≤ 5 → keep Sideways (mixed direction)."""
        d = make_detector()
        result = d._classify_regime(5.0, adx=32.0, plus_di=25.0, minus_di=23.0)
        assert result == "Sideways"

    def test_adx_override_outside_sideways_no_effect(self):
        """ADX > 25 but composite already outside Sideways → thresholds apply normally."""
        d = make_detector()
        result = d._classify_regime(35.0, adx=32.0, plus_di=30.0, minus_di=10.0)
        assert result == "Weak Bullish"

    def test_adx_override_missing_di_no_override(self):
        """ADX > 25 but DI missing → no override, falls back to threshold."""
        d = make_detector()
        result = d._classify_regime(5.0, adx=32.0, plus_di=None, minus_di=None)
        assert result == "Sideways"


# ═══════════════════════════════════════════════════════════════════════════════
# FULL detect_regime INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════════

class TestDetectRegimeIntegration:

    def test_btc_crash_scenario_strong_bearish(self):
        """
        Scenario: BTC dropped 36% (98k→63k).
        Death cross, ADX 37.5 (strong trend), RSI 16 (oversold), extreme fear.
        Must NOT classify as Sideways.
        """
        d = make_detector()
        result = d.detect_regime(
            technical_indicators={
                "sma_50": 75000.0, "sma_200": 80000.0,
                "adx": 37.5, "plus_di": 10.0, "minus_di": 35.0,
                "rsi": 16.0, "macd": -1000.0, "macd_signal": -500.0, "macd_histogram": -500.0,
            },
            onchain_metrics={"funding_rate": -0.03, "wings_skew": 8.0, "oi_direction": -20},
            external_metrics={"fear_greed": {"value": 12, "classification": "Extreme Fear"},
                              "fear_greed_7d_avg": 14.0, "btc_dominance": 55.0,
                              "market_cap_change_24h": -5.0},
            current_price=63000.0,
            currency="BTC",
        )
        assert result["regime"] in ["Weak Bearish", "Strong Bearish"]
        assert result["composite_score"] < 0

    def test_true_sideways_low_adx(self):
        """Low ADX + neutral indicators → Sideways."""
        d = make_detector()
        result = d.detect_regime(
            technical_indicators={
                "sma_50": 65000.0, "sma_200": 64000.0,
                "adx": 18.0, "plus_di": 22.0, "minus_di": 20.0,
                "rsi": 50.0, "macd": 10.0, "macd_signal": 10.0, "macd_histogram": 0.0,
            },
            onchain_metrics={"funding_rate": 0.005, "wings_skew": 1.0, "oi_direction": 0},
            external_metrics={"fear_greed": {"value": 50, "classification": "Neutral"},
                              "fear_greed_7d_avg": 50.0, "btc_dominance": 50.0,
                              "market_cap_change_24h": 0.0},
            current_price=64500.0,
            currency="BTC",
        )
        assert result["regime"] == "Sideways"

    def test_backward_compatible_no_velocity(self):
        """velocity_indicators=None (default) must not crash."""
        d = make_detector()
        result = d.detect_regime(
            technical_indicators={"sma_50": 65000.0, "sma_200": 64000.0, "adx": 25.0,
                                  "rsi": 55.0, "macd": 10.0, "macd_signal": 8.0,
                                  "macd_histogram": 2.0},
            onchain_metrics={},
            external_metrics={"fear_greed": {"value": 55, "classification": "Greed"},
                              "btc_dominance": 50.0},
            current_price=65000.0,
        )
        assert "regime" in result
        assert "composite_score" in result
        assert "confidence" in result

    def test_result_contains_required_keys(self):
        """Result dict has all required keys."""
        d = make_detector()
        result = d.detect_regime(
            technical_indicators={}, onchain_metrics={},
            external_metrics={}, current_price=50000.0,
        )
        for key in ["regime", "composite_score", "confidence", "trend_score",
                    "volatility_score", "momentum_score", "onchain_score",
                    "sentiment_score", "reasoning"]:
            assert key in result, f"Missing key: {key}"

    def test_eth_uses_inverted_dominance(self):
        """ETH with high BTC dominance gets lower sentiment than BTC would."""
        d = make_detector()

        def run(currency):
            return d.detect_regime(
                technical_indicators={},
                onchain_metrics={},
                external_metrics={"fear_greed_7d_avg": 50.0, "btc_dominance": 60.0,
                                  "market_cap_change_24h": 0.0},
                current_price=3000.0,
                currency=currency,
            )

        btc_result = run("BTC")
        eth_result = run("ETH")
        assert btc_result["sentiment_score"] > eth_result["sentiment_score"]
