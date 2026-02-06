"""
Test market regime detection fixes.

Verifies that the narrowed thresholds and ADX override correctly classify
strong bearish trends (like the 36% BTC drop from 98k to 63k).
"""

import logging
from coding.core.analytics.market_regime_detector import MarketRegimeDetector

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def test_user_scenario_before_fix():
    """
    Test with user's actual data.

    Scenario: BTC dropped 36% (98k to 63k) in 3 days
    - Death Cross structure (50 SMA < 200 SMA)
    - ADX 37.5 (strong trend, NOT sideways!)
    - RSI 16 (extreme oversold)
    - ATR 88.8 percentile (extreme volatility)
    - Fear & Greed 12 (Extreme Fear)
    - Funding 0.000%, P/C Ratio 0.75

    BEFORE FIX: Would classify as "Sideways" (score -28.5)
    AFTER FIX: Should classify as "Weak Bearish" or "Strong Bearish"
    """
    detector = MarketRegimeDetector()

    # User's data
    technical_indicators = {
        "sma_50": 75000,  # Below 200 SMA
        "sma_200": 80000,  # Death cross
        "adx": 37.5,  # Strong trend
        "rsi": 16.0,  # Extreme oversold
        "atr_percentile": 88.8,  # Extreme volatility
        "macd": -1000,
        "macd_signal": -500,
        "macd_histogram": -500
    }

    onchain_metrics = {
        "funding_rate": 0.0000,  # Neutral
        "put_call_ratio": 0.75,  # Slight call bias
    }

    external_metrics = {
        "fear_greed": {
            "value": 12,
            "classification": "Extreme Fear"
        },
        "btc_dominance": 55
    }

    current_price = 63000  # After the drop

    result = detector.detect_regime(
        technical_indicators=technical_indicators,
        onchain_metrics=onchain_metrics,
        external_metrics=external_metrics,
        current_price=current_price
    )

    print("\n" + "="*80)
    print("TEST: User's Actual Scenario (BTC 98k -> 63k drop)")
    print("="*80)
    print(f"Regime: {result['regime']}")
    print(f"Composite Score: {result['composite_score']}")
    print(f"Confidence: {result['confidence']}%")
    print(f"\nComponent Scores:")
    print(f"  Trend: {result['trend_score']}")
    print(f"  Volatility: {result['volatility_score']}")
    print(f"  Momentum: {result['momentum_score']}")
    print(f"  On-Chain: {result['onchain_score']}")
    print(f"  Sentiment: {result['sentiment_score']}")
    print(f"\nReasoning: {result['reasoning']}")

    # Assertions
    assert result['regime'] in ['Weak Bearish', 'Strong Bearish'], \
        f"Expected Weak/Strong Bearish, got {result['regime']}"
    assert result['composite_score'] < 0, \
        f"Expected negative score, got {result['composite_score']}"

    print("\n[PASS] PASS: Correctly classified as bearish trend (not sideways)")
    return result


def test_true_sideways_market():
    """
    Test a truly sideways market.

    Characteristics:
    - Low ADX (< 20) = no trend
    - Price near moving averages
    - Normal RSI (45-55)
    - Normal volatility
    - Neutral sentiment
    """
    detector = MarketRegimeDetector()

    technical_indicators = {
        "sma_50": 65000,
        "sma_200": 64000,
        "adx": 18.0,  # Low ADX = no trend
        "rsi": 50.0,  # Neutral momentum
        "atr_percentile": 45.0,  # Normal volatility
        "macd": 50,
        "macd_signal": 45,
        "macd_histogram": 5
    }

    onchain_metrics = {
        "funding_rate": 0.0001,  # Near zero
        "put_call_ratio": 0.85,  # Balanced
    }

    external_metrics = {
        "fear_greed": {
            "value": 50,
            "classification": "Neutral"
        },
        "btc_dominance": 48
    }

    current_price = 64500  # Between SMAs

    result = detector.detect_regime(
        technical_indicators=technical_indicators,
        onchain_metrics=onchain_metrics,
        external_metrics=external_metrics,
        current_price=current_price
    )

    print("\n" + "="*80)
    print("TEST: True Sideways Market")
    print("="*80)
    print(f"Regime: {result['regime']}")
    print(f"Composite Score: {result['composite_score']}")
    print(f"ADX: {technical_indicators['adx']} (low = no trend)")
    print(f"\nComponent Scores:")
    print(f"  Trend: {result['trend_score']}")
    print(f"  Volatility: {result['volatility_score']}")
    print(f"  Momentum: {result['momentum_score']}")
    print(f"  On-Chain: {result['onchain_score']}")
    print(f"  Sentiment: {result['sentiment_score']}")

    # For true sideways, should classify as sideways (low ADX allows it)
    assert result['regime'] == 'Sideways', \
        f"Expected Sideways for low ADX market, got {result['regime']}"

    print("\n[PASS] PASS: Correctly classified true sideways market")
    return result


def test_strong_bullish_trend():
    """
    Test strong bullish trend.

    Characteristics:
    - Golden Cross
    - High ADX (> 30)
    - RSI > 60
    - Positive funding
    - High Fear & Greed
    """
    detector = MarketRegimeDetector()

    technical_indicators = {
        "sma_50": 70000,
        "sma_200": 65000,  # Golden cross
        "adx": 35.0,  # Strong trend
        "rsi": 65.0,  # Bullish momentum
        "atr_percentile": 55.0,
        "macd": 1000,
        "macd_signal": 500,
        "macd_histogram": 500
    }

    onchain_metrics = {
        "funding_rate": 0.008,  # Positive (longs paying shorts)
        "put_call_ratio": 0.65,  # Call bias
    }

    external_metrics = {
        "fear_greed": {
            "value": 70,
            "classification": "Greed"
        },
        "btc_dominance": 52
    }

    current_price = 72000  # Above both SMAs

    result = detector.detect_regime(
        technical_indicators=technical_indicators,
        onchain_metrics=onchain_metrics,
        external_metrics=external_metrics,
        current_price=current_price
    )

    print("\n" + "="*80)
    print("TEST: Strong Bullish Trend")
    print("="*80)
    print(f"Regime: {result['regime']}")
    print(f"Composite Score: {result['composite_score']}")
    print(f"\nComponent Scores:")
    print(f"  Trend: {result['trend_score']}")
    print(f"  Volatility: {result['volatility_score']}")
    print(f"  Momentum: {result['momentum_score']}")
    print(f"  On-Chain: {result['onchain_score']}")
    print(f"  Sentiment: {result['sentiment_score']}")

    assert result['regime'] in ['Weak Bullish', 'Strong Bullish'], \
        f"Expected bullish regime, got {result['regime']}"
    assert result['composite_score'] > 0, \
        f"Expected positive score, got {result['composite_score']}"

    print("\n[PASS] PASS: Correctly classified strong bullish trend")
    return result


def test_adx_override():
    """
    Test ADX override prevents sideways classification.

    Even if composite score is in "Sideways" range (-15 to +15),
    if ADX > 25, should force trend classification.
    """
    detector = MarketRegimeDetector()

    # Construct scenario where composite would be ~0 (sideways range)
    # but ADX is high (strong trend)
    technical_indicators = {
        "sma_50": 65000,
        "sma_200": 66000,  # Death cross
        "adx": 32.0,  # STRONG TREND (should override)
        "rsi": 48.0,  # Neutral
        "atr_percentile": 50.0,
        "macd": -100,
        "macd_signal": -50,
        "macd_histogram": -50
    }

    onchain_metrics = {
        "funding_rate": 0.0001,
        "put_call_ratio": 0.85,
    }

    external_metrics = {
        "fear_greed": {
            "value": 52,
            "classification": "Neutral"
        },
        "btc_dominance": 50
    }

    current_price = 64500

    result = detector.detect_regime(
        technical_indicators=technical_indicators,
        onchain_metrics=onchain_metrics,
        external_metrics=external_metrics,
        current_price=current_price
    )

    print("\n" + "="*80)
    print("TEST: ADX Override (High ADX prevents Sideways)")
    print("="*80)
    print(f"Regime: {result['regime']}")
    print(f"Composite Score: {result['composite_score']}")
    print(f"ADX: {technical_indicators['adx']} (> 25 = trending)")
    print(f"\nComponent Scores:")
    print(f"  Trend: {result['trend_score']}")
    print(f"  Volatility: {result['volatility_score']}")
    print(f"  Momentum: {result['momentum_score']}")
    print(f"  On-Chain: {result['onchain_score']}")
    print(f"  Sentiment: {result['sentiment_score']}")

    # Should NOT be sideways despite score in sideways range
    assert result['regime'] != 'Sideways', \
        f"ADX override failed: classified as Sideways with ADX={technical_indicators['adx']}"

    print("\n[PASS] PASS: ADX override correctly prevented sideways classification")
    return result


def test_sentiment_context_awareness():
    """
    Test that extreme fear is interpreted correctly based on trend context.

    - During strong bearish trend: Extreme Fear = confirmation (bearish)
    - During weak/no trend: Extreme Fear = contrarian (bullish)
    """
    detector = MarketRegimeDetector()

    # Scenario 1: Extreme fear during strong bearish trend
    print("\n" + "="*80)
    print("TEST: Sentiment Context Awareness")
    print("="*80)
    print("\nScenario 1: Extreme Fear DURING Strong Bearish Trend")
    print("-" * 80)

    tech_indicators_bearish = {
        "sma_50": 60000,
        "sma_200": 70000,  # Death cross
        "adx": 40.0,  # Very strong trend
        "rsi": 25.0,  # Oversold
        "atr_percentile": 80.0,
        "macd": -1000,
        "macd_signal": -500,
        "macd_histogram": -500
    }

    result1 = detector.detect_regime(
        technical_indicators=tech_indicators_bearish,
        onchain_metrics={"funding_rate": -0.002, "put_call_ratio": 1.1},
        external_metrics={"fear_greed": {"value": 15, "classification": "Extreme Fear"}, "btc_dominance": 55},
        current_price=58000
    )

    print(f"Sentiment Score: {result1['sentiment_score']}")
    print(f"(Should be negative - fear confirms bearish trend)")

    # Scenario 2: Extreme fear during weak trend (contrarian)
    print("\nScenario 2: Extreme Fear DURING Weak/No Trend")
    print("-" * 80)

    tech_indicators_weak = {
        "sma_50": 65000,
        "sma_200": 64000,
        "adx": 18.0,  # Weak trend
        "rsi": 45.0,
        "atr_percentile": 40.0,
        "macd": 50,
        "macd_signal": 45,
        "macd_histogram": 5
    }

    result2 = detector.detect_regime(
        technical_indicators=tech_indicators_weak,
        onchain_metrics={"funding_rate": 0.0001, "put_call_ratio": 0.9},
        external_metrics={"fear_greed": {"value": 15, "classification": "Extreme Fear"}, "btc_dominance": 48},
        current_price=64500
    )

    print(f"Sentiment Score: {result2['sentiment_score']}")
    print(f"(Should be positive - contrarian buy signal)")

    # Assertions
    assert result1['sentiment_score'] < 0, \
        f"Expected negative sentiment during bearish trend, got {result1['sentiment_score']}"
    assert result2['sentiment_score'] > 0, \
        f"Expected positive sentiment (contrarian) during weak trend, got {result2['sentiment_score']}"

    print("\n[PASS] PASS: Sentiment correctly interprets context")


if __name__ == "__main__":
    print("\n" + "="*80)
    print("MARKET REGIME DETECTION - FIX VERIFICATION")
    print("="*80)
    print("\nFixes implemented:")
    print("1. Narrowed 'Sideways' threshold from ±30 to ±15")
    print("2. Added ADX override (ADX > 25 = never sideways)")
    print("3. Context-aware sentiment (extreme fear during strong trends = confirmation)")
    print("4. More conservative on-chain scoring (wider neutral zones)")

    try:
        # Run all tests
        test_user_scenario_before_fix()
        test_true_sideways_market()
        test_strong_bullish_trend()
        test_adx_override()
        test_sentiment_context_awareness()

        print("\n" + "="*80)
        print("ALL TESTS PASSED [PASS]")
        print("="*80)
        print("\nThe fixes correctly address:")
        print("[PASS] Strong trends are no longer misclassified as 'Sideways'")
        print("[PASS] ADX override prevents sideways classification when ADX > 25")
        print("[PASS] Sentiment scoring adapts to trend context")
        print("[PASS] True sideways markets (low ADX) still classified correctly")

    except AssertionError as e:
        print(f"\n[FAIL] TEST FAILED: {e}")
        raise
    except Exception as e:
        logger.error(f"Test error: {e}", exc_info=True)
        raise
