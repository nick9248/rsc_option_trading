"""
Tests for TechnicalIndicatorCalculator.get_velocity_indicators()
"""
import pandas as pd
import pytest
from coding.core.analytics.technical_indicator_calculator import TechnicalIndicatorCalculator


def _make_df(ema_50_vals, rsi_vals, hist_vals):
    """Build a minimal indicators DataFrame with required columns."""
    n = len(ema_50_vals)
    assert len(rsi_vals) == n and len(hist_vals) == n
    return pd.DataFrame(
        {"ema_50": ema_50_vals, "rsi": rsi_vals, "macd_histogram": hist_vals},
        index=pd.date_range("2025-01-01", periods=n, freq="1D"),
    )


# ── EMA velocity ──────────────────────────────────────────────────────────────

def test_ema_velocity_rising():
    """EMA rose 0.5% yesterday → ema_50_velocity = 0.5."""
    calc = TechnicalIndicatorCalculator()
    df = _make_df([100.0, 100.5], [50.0, 50.0], [1.0, 1.0])
    result = calc.get_velocity_indicators(df)
    assert result["ema_50_velocity"] == pytest.approx(0.5, rel=0.01)


def test_ema_velocity_falling():
    """EMA fell 1% → ema_50_velocity = -1.0."""
    calc = TechnicalIndicatorCalculator()
    df = _make_df([100.0, 99.0], [50.0, 50.0], [1.0, 1.0])
    result = calc.get_velocity_indicators(df)
    assert result["ema_50_velocity"] == pytest.approx(-1.0, rel=0.01)


def test_ema_velocity_flat():
    """No EMA change → ema_50_velocity = 0.0."""
    calc = TechnicalIndicatorCalculator()
    df = _make_df([100.0, 100.0], [50.0, 50.0], [1.0, 1.0])
    result = calc.get_velocity_indicators(df)
    assert result["ema_50_velocity"] == pytest.approx(0.0, abs=1e-9)


# ── RSI velocity ──────────────────────────────────────────────────────────────

def test_rsi_velocity_rising():
    """RSI rose 10 pts over 5 days → rsi_velocity = 10."""
    calc = TechnicalIndicatorCalculator()
    rsi_vals = [40.0, 42.0, 44.0, 46.0, 48.0, 50.0]  # 6 rows, lookback=5
    df = _make_df([100.0] * 6, rsi_vals, [1.0] * 6)
    result = calc.get_velocity_indicators(df, lookback=5)
    # iloc[-1]=50, iloc[-6]=40 → +10
    assert result["rsi_velocity"] == pytest.approx(10.0, rel=0.01)


def test_rsi_velocity_falling():
    """RSI fell 12 pts over 5 days → rsi_velocity = -12."""
    calc = TechnicalIndicatorCalculator()
    rsi_vals = [65.0, 63.0, 61.0, 59.0, 57.0, 53.0]
    df = _make_df([100.0] * 6, rsi_vals, [1.0] * 6)
    result = calc.get_velocity_indicators(df, lookback=5)
    assert result["rsi_velocity"] == pytest.approx(-12.0, rel=0.01)


def test_rsi_velocity_insufficient_rows():
    """Fewer than lookback+1 rows → rsi_velocity is None."""
    calc = TechnicalIndicatorCalculator()
    df = _make_df([100.0] * 3, [50.0] * 3, [1.0] * 3)
    result = calc.get_velocity_indicators(df, lookback=5)
    assert result["rsi_velocity"] is None


# ── MACD histogram magnitude velocity ────────────────────────────────────────

def test_histogram_velocity_building():
    """Histogram magnitude growing → positive velocity."""
    calc = TechnicalIndicatorCalculator()
    # yesterday abs=10, today abs=15 → velocity = +5
    df = _make_df([100.0, 100.0], [50.0, 50.0], [10.0, 15.0])
    result = calc.get_velocity_indicators(df)
    assert result["macd_histogram_velocity"] == pytest.approx(5.0, rel=0.01)


def test_histogram_velocity_fading():
    """Histogram magnitude shrinking → negative velocity."""
    calc = TechnicalIndicatorCalculator()
    # yesterday abs=20, today abs=8 → velocity = -12
    df = _make_df([100.0, 100.0], [50.0, 50.0], [20.0, 8.0])
    result = calc.get_velocity_indicators(df)
    assert result["macd_histogram_velocity"] == pytest.approx(-12.0, rel=0.01)


def test_histogram_velocity_negative_bars():
    """Magnitude comparison works across sign change direction."""
    calc = TechnicalIndicatorCalculator()
    # yesterday hist=-10 (abs=10), today hist=-5 (abs=5) → velocity=-5 (fading)
    df = _make_df([100.0, 100.0], [50.0, 50.0], [-10.0, -5.0])
    result = calc.get_velocity_indicators(df)
    assert result["macd_histogram_velocity"] == pytest.approx(-5.0, rel=0.01)


def test_histogram_independent_of_crossover_direction():
    """
    Key fix: histogram velocity is independent of MACD crossover.
    MACD above signal (positive hist) but magnitude shrinking → negative velocity.
    """
    calc = TechnicalIndicatorCalculator()
    # Both positive (MACD > signal), but shrinking
    df = _make_df([100.0, 100.0], [50.0, 50.0], [30.0, 10.0])
    result = calc.get_velocity_indicators(df)
    assert result["macd_histogram_velocity"] < 0


# ── Edge cases ────────────────────────────────────────────────────────────────

def test_empty_dataframe():
    """Empty df returns all None."""
    calc = TechnicalIndicatorCalculator()
    result = calc.get_velocity_indicators(pd.DataFrame())
    assert result["ema_50_velocity"] is None
    assert result["rsi_velocity"] is None
    assert result["macd_histogram_velocity"] is None


def test_single_row_dataframe():
    """Single row: no previous bar → ema and hist velocity None."""
    calc = TechnicalIndicatorCalculator()
    df = _make_df([100.0], [50.0], [5.0])
    result = calc.get_velocity_indicators(df)
    assert result["ema_50_velocity"] is None
    assert result["macd_histogram_velocity"] is None


def test_nan_values_handled():
    """NaN in EMA → ema_50_velocity is None, others still computed."""
    calc = TechnicalIndicatorCalculator()
    import numpy as np
    df = _make_df([np.nan, 100.0], [50.0, 50.0], [10.0, 12.0])
    result = calc.get_velocity_indicators(df)
    assert result["ema_50_velocity"] is None
    assert result["macd_histogram_velocity"] == pytest.approx(2.0, rel=0.01)
