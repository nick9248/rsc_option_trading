import pytest
import numpy as np
import pandas as pd
from coding.core.analytics.regime_weight_optimizer import (
    RegimeWeightOptimizer,
    ParameterSet,
    OptimizationResult,
    _classify,
)


# ── Classification ────────────────────────────────────────────────────────────

def test_classify_strong_bullish():
    assert _classify(60.0, sideways=20.0, strong=55.0) == +1

def test_classify_weak_bullish():
    assert _classify(25.0, sideways=20.0, strong=55.0) == +1

def test_classify_sideways():
    assert _classify(0.0, sideways=20.0, strong=55.0) == 0

def test_classify_weak_bearish():
    assert _classify(-30.0, sideways=20.0, strong=55.0) == -1

def test_classify_strong_bearish():
    assert _classify(-60.0, sideways=20.0, strong=55.0) == -1

def test_classify_boundary_sideways_upper():
    """Exactly at +sideways_threshold → Bullish (+1)."""
    assert _classify(20.0, sideways=20.0, strong=55.0) == +1

def test_classify_boundary_sideways_lower():
    """Exactly at -sideways_threshold → Sideways (0)."""
    assert _classify(-20.0, sideways=20.0, strong=55.0) == 0


# ── Fitness A ─────────────────────────────────────────────────────────────────

def test_fitness_a_perfect_accuracy():
    """All bullish predictions with +2.5% returns → 100% accuracy."""
    rows = [{"trend_score": 50.0, "volatility_score": 0.0, "momentum_score": 0.0,
             "onchain_score": 0.0, "sentiment_score": 0.0,
             "return_4h": 2.5, "return_8h": None, "return_12h": None,
             "return_24h": None, "return_48h": None, "return_72h": None,
             "return_7d": None, "return_30d": None}]
    df = pd.DataFrame(rows)
    opt = RegimeWeightOptimizer(df, directional_threshold=1.5)
    x = np.array([1.0, 0.0, 0.0, 0.0, 0.0, 20.0, 55.0])
    score = opt._fitness_a(x)
    assert score == pytest.approx(1.0)

def test_fitness_a_zero_accuracy():
    rows = [{"trend_score": 50.0, "volatility_score": 0.0, "momentum_score": 0.0,
             "onchain_score": 0.0, "sentiment_score": 0.0,
             "return_4h": -2.5, "return_8h": None, "return_12h": None,
             "return_24h": None, "return_48h": None, "return_72h": None,
             "return_7d": None, "return_30d": None}]
    df = pd.DataFrame(rows)
    opt = RegimeWeightOptimizer(df, directional_threshold=1.5)
    x = np.array([1.0, 0.0, 0.0, 0.0, 0.0, 20.0, 55.0])
    score = opt._fitness_a(x)
    assert score == pytest.approx(0.0)

def test_fitness_a_sideways_both_sides():
    rows = [{"trend_score": 0.0, "volatility_score": 0.0, "momentum_score": 0.0,
             "onchain_score": 0.0, "sentiment_score": 0.0,
             "return_4h": 1.0, "return_8h": None, "return_12h": None,
             "return_24h": None, "return_48h": None, "return_72h": None,
             "return_7d": None, "return_30d": None}]
    df = pd.DataFrame(rows)
    opt = RegimeWeightOptimizer(df, directional_threshold=1.5)
    x = np.array([1.0, 0.0, 0.0, 0.0, 0.0, 20.0, 55.0])
    score = opt._fitness_a(x)
    assert score == pytest.approx(1.0)

def test_fitness_a_multi_row_multi_horizon():
    """Accuracy computed correctly across multiple rows and multiple horizons."""
    rows = [
        {"trend_score": 50.0, "volatility_score": 0.0, "momentum_score": 0.0,
         "onchain_score": 0.0, "sentiment_score": 0.0,
         "return_4h": 2.0, "return_8h": -2.0, "return_12h": None,
         "return_24h": None, "return_48h": None, "return_72h": None,
         "return_7d": None, "return_30d": None},
        {"trend_score": -50.0, "volatility_score": 0.0, "momentum_score": 0.0,
         "onchain_score": 0.0, "sentiment_score": 0.0,
         "return_4h": -2.0, "return_8h": 2.0, "return_12h": None,
         "return_24h": None, "return_48h": None, "return_72h": None,
         "return_7d": None, "return_30d": None},
    ]
    df = pd.DataFrame(rows)
    opt = RegimeWeightOptimizer(df, directional_threshold=1.5)
    x = np.array([1.0, 0.0, 0.0, 0.0, 0.0, 20.0, 55.0])
    score = opt._fitness_a(x)
    assert score == pytest.approx(0.5)


# ── Fitness B ─────────────────────────────────────────────────────────────────

def test_fitness_b_std_zero_returns_zero():
    rows = [
        {"trend_score": 50.0, "volatility_score": 0.0, "momentum_score": 0.0,
         "onchain_score": 0.0, "sentiment_score": 0.0,
         "return_4h": 2.0, "return_8h": None, "return_12h": None,
         "return_24h": None, "return_48h": None, "return_72h": None,
         "return_7d": None, "return_30d": None},
        {"trend_score": 50.0, "volatility_score": 0.0, "momentum_score": 0.0,
         "onchain_score": 0.0, "sentiment_score": 0.0,
         "return_4h": 2.0, "return_8h": None, "return_12h": None,
         "return_24h": None, "return_48h": None, "return_72h": None,
         "return_7d": None, "return_30d": None},
    ]
    df = pd.DataFrame(rows)
    opt = RegimeWeightOptimizer(df, directional_threshold=1.5)
    x = np.array([1.0, 0.0, 0.0, 0.0, 0.0, 20.0, 55.0])
    score = opt._fitness_b(x)
    assert score == pytest.approx(0.0)

def test_fitness_b_single_row_returns_zero():
    rows = [{"trend_score": 50.0, "volatility_score": 0.0, "momentum_score": 0.0,
             "onchain_score": 0.0, "sentiment_score": 0.0,
             "return_4h": 3.0, "return_8h": None, "return_12h": None,
             "return_24h": None, "return_48h": None, "return_72h": None,
             "return_7d": None, "return_30d": None}]
    df = pd.DataFrame(rows)
    opt = RegimeWeightOptimizer(df, directional_threshold=1.5)
    x = np.array([1.0, 0.0, 0.0, 0.0, 0.0, 20.0, 55.0])
    score = opt._fitness_b(x)
    assert score == pytest.approx(0.0)

def test_fitness_b_per_horizon_averaging():
    """
    Verify per-horizon Sharpe is averaged over AVAILABLE horizons only.
    Zero-row horizons must NOT be included as 0.0 in the average.

    Setup:
      x = [w_trend=1, rest=0, sideways=20, strong=55]
      Row 0: trend=+50 → predicted=+1; Row 1: trend=-50 → predicted=-1

      4h horizon (both rows have data):
        pnl = [+1*3.0=3.0,  -1*(-3.0)=3.0] → mean=3.0, std=0 → sharpe_4h=0.0
      8h horizon (both rows have data):
        pnl = [+1*2.0=2.0,  -1*1.0=-1.0] → mean=0.5, std=1.5 → sharpe_8h≠0
      12h–30d: no data → excluded from average entirely
    """
    rows = [
        {"trend_score": 50.0, "volatility_score": 0.0, "momentum_score": 0.0,
         "onchain_score": 0.0, "sentiment_score": 0.0,
         "return_4h": 3.0, "return_8h": 2.0, "return_12h": None,
         "return_24h": None, "return_48h": None, "return_72h": None,
         "return_7d": None, "return_30d": None},
        {"trend_score": -50.0, "volatility_score": 0.0, "momentum_score": 0.0,
         "onchain_score": 0.0, "sentiment_score": 0.0,
         "return_4h": -3.0, "return_8h": 1.0, "return_12h": None,
         "return_24h": None, "return_48h": None, "return_72h": None,
         "return_7d": None, "return_30d": None},
    ]
    df = pd.DataFrame(rows)
    opt = RegimeWeightOptimizer(df, directional_threshold=1.5)
    x = np.array([1.0, 0.0, 0.0, 0.0, 0.0, 20.0, 55.0])

    pnl_8h = np.array([2.0, -1.0])
    sharpe_8h = float(np.mean(pnl_8h) / np.std(pnl_8h, ddof=0))
    expected = np.mean([0.0, sharpe_8h])  # only 4h and 8h have data

    score = opt._fitness_b(x)
    assert score == pytest.approx(expected, rel=1e-4)


# ── OptimizationResult structure ─────────────────────────────────────────────

def test_optimize_returns_four_parameter_sets():
    rows = []
    np.random.seed(42)
    for i in range(40):
        trend = np.random.uniform(-50, 50)
        ret_4h = trend * 0.04 + np.random.normal(0, 1)
        rows.append({
            "trend_score": trend, "volatility_score": 0.0, "momentum_score": 0.0,
            "onchain_score": 0.0, "sentiment_score": 0.0,
            "return_4h": ret_4h, "return_8h": None, "return_12h": None,
            "return_24h": None, "return_48h": None, "return_72h": None,
            "return_7d": None, "return_30d": None,
        })
    df = pd.DataFrame(rows)
    result = RegimeWeightOptimizer(df, directional_threshold=1.5).optimize()

    assert isinstance(result, OptimizationResult)
    assert isinstance(result.current, ParameterSet)
    assert isinstance(result.accuracy_optimal, ParameterSet)
    assert isinstance(result.sharpe_optimal, ParameterSet)
    assert isinstance(result.blended_optimal, ParameterSet)

def test_all_parameter_sets_have_both_fitness_values():
    rows = []
    np.random.seed(0)
    for i in range(40):
        trend = np.random.uniform(-50, 50)
        rows.append({
            "trend_score": trend, "volatility_score": 0.0, "momentum_score": 0.0,
            "onchain_score": 0.0, "sentiment_score": 0.0,
            "return_4h": trend * 0.03 + np.random.normal(0, 1),
            "return_8h": None, "return_12h": None, "return_24h": None,
            "return_48h": None, "return_72h": None, "return_7d": None, "return_30d": None,
        })
    df = pd.DataFrame(rows)
    result = RegimeWeightOptimizer(df).optimize()

    for ps_name in ("current", "accuracy_optimal", "sharpe_optimal", "blended_optimal"):
        ps = getattr(result, ps_name)
        assert ps.fitness_a is not None, f"{ps_name}.fitness_a is None"
        assert ps.fitness_b is not None, f"{ps_name}.fitness_b is None"
        assert isinstance(ps.fitness_a, float)
        assert isinstance(ps.fitness_b, float)

def test_weights_sum_to_one():
    rows = []
    np.random.seed(1)
    for i in range(40):
        trend = np.random.uniform(-50, 50)
        rows.append({
            "trend_score": trend, "volatility_score": 0.0, "momentum_score": 0.0,
            "onchain_score": 0.0, "sentiment_score": 0.0,
            "return_4h": trend * 0.03 + np.random.normal(0, 1),
            "return_8h": None, "return_12h": None, "return_24h": None,
            "return_48h": None, "return_72h": None, "return_7d": None, "return_30d": None,
        })
    df = pd.DataFrame(rows)
    result = RegimeWeightOptimizer(df).optimize()

    for ps_name in ("accuracy_optimal", "sharpe_optimal", "blended_optimal"):
        ps = getattr(result, ps_name)
        weight_sum = sum(ps.weights.values())
        assert weight_sum == pytest.approx(1.0, abs=1e-4), f"{ps_name} weights sum to {weight_sum}"

def test_strong_threshold_greater_than_sideways():
    rows = []
    np.random.seed(2)
    for i in range(40):
        trend = np.random.uniform(-50, 50)
        rows.append({
            "trend_score": trend, "volatility_score": 0.0, "momentum_score": 0.0,
            "onchain_score": 0.0, "sentiment_score": 0.0,
            "return_4h": trend * 0.03 + np.random.normal(0, 1),
            "return_8h": None, "return_12h": None, "return_24h": None,
            "return_48h": None, "return_72h": None, "return_7d": None, "return_30d": None,
        })
    df = pd.DataFrame(rows)
    result = RegimeWeightOptimizer(df).optimize()

    for ps_name in ("accuracy_optimal", "sharpe_optimal", "blended_optimal"):
        ps = getattr(result, ps_name)
        assert ps.strong_threshold > ps.sideways_threshold, \
            f"{ps_name}: strong={ps.strong_threshold} not > sideways={ps.sideways_threshold}"

def test_horizon_coverage_in_result():
    rows = []
    np.random.seed(3)
    for i in range(40):
        rows.append({
            "trend_score": 10.0, "volatility_score": 0.0, "momentum_score": 0.0,
            "onchain_score": 0.0, "sentiment_score": 0.0,
            "return_4h": 2.0, "return_8h": None, "return_12h": None,
            "return_24h": None, "return_48h": None, "return_72h": None,
            "return_7d": None, "return_30d": None,
        })
    df = pd.DataFrame(rows)
    result = RegimeWeightOptimizer(df).optimize()
    assert "4h" in result.horizon_coverage
    assert result.horizon_coverage["4h"] == 40
    assert result.horizon_coverage.get("8h", 0) == 0
    assert result.dataset_rows == 40

def test_fitness_a_multi_row_multi_horizon_already_above():
    # Already defined above as test_fitness_a_multi_row_multi_horizon — skip duplicate
    pass

def test_optimizer_improves_on_known_signal():
    """SLSQP should find trend weight > 0.4 when trend_score is a strong predictor."""
    np.random.seed(99)
    rows = []
    for _ in range(60):
        trend = np.random.choice([-60.0, -40.0, 40.0, 60.0])
        ret_4h = np.sign(trend) * 3.0 + np.random.normal(0, 0.1)
        rows.append({
            "trend_score": trend, "volatility_score": np.random.uniform(-10, 10),
            "momentum_score": np.random.uniform(-10, 10),
            "onchain_score": np.random.uniform(-10, 10),
            "sentiment_score": np.random.uniform(-10, 10),
            "return_4h": ret_4h, "return_8h": None, "return_12h": None,
            "return_24h": None, "return_48h": None, "return_72h": None,
            "return_7d": None, "return_30d": None,
        })
    df = pd.DataFrame(rows)
    result = RegimeWeightOptimizer(df, directional_threshold=1.5).optimize()
    assert result.accuracy_optimal.weights["trend"] > 0.4, \
        f"Expected trend weight > 0.4, got {result.accuracy_optimal.weights['trend']:.3f}"
    assert result.accuracy_optimal.fitness_a >= result.current.fitness_a - 0.01
