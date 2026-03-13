import pytest
import pandas as pd
from datetime import datetime, timedelta
from unittest.mock import MagicMock
from coding.core.database.regime_dataset_builder import RegimeDatasetBuilder


def _make_detection(detected_at, price, trend=10.0, vol=5.0, mom=8.0, onchain=12.0, sent=3.0):
    return {
        "detected_at": detected_at,
        "currency": "BTC",
        "current_price": price,
        "trend_score": trend,
        "volatility_score": vol,
        "momentum_score": mom,
        "onchain_score": onchain,
        "sentiment_score": sent,
    }


T0 = datetime(2026, 1, 1, 12, 0, 0)


def _make_repo(detections, ohlcv=None):
    repo = MagicMock()
    repo.get_regime_detections.return_value = list(reversed(detections))  # simulate DESC order
    repo.get_ohlcv_by_date_range.return_value = ohlcv or []
    return repo


# ── Test: output schema ──────────────────────────────────────────────────────

def test_output_columns():
    detections = [_make_detection(T0, 50000.0)]
    repo = _make_repo(detections)
    builder = RegimeDatasetBuilder(repo)
    df = builder.build("BTC")
    expected_cols = {
        "detected_at", "currency", "current_price",
        "trend_score", "volatility_score", "momentum_score",
        "onchain_score", "sentiment_score",
        "return_4h", "return_8h", "return_12h", "return_24h",
        "return_48h", "return_72h", "return_7d", "return_30d",
    }
    assert expected_cols.issubset(set(df.columns))


# ── Test: short-horizon forward price lookup ─────────────────────────────────

def test_short_horizon_exact_match():
    """Row at T0+4h is exactly at the 4h horizon — should be found."""
    t4h = T0 + timedelta(hours=4)
    detections = [
        _make_detection(T0, 50000.0),
        _make_detection(t4h, 51000.0),
    ]
    repo = _make_repo(detections)
    builder = RegimeDatasetBuilder(repo)
    df = builder.build("BTC")
    row = df[df["detected_at"] == T0].iloc[0]
    expected = (51000.0 - 50000.0) / 50000.0 * 100  # +2.0%
    assert abs(row["return_4h"] - expected) < 0.001


def test_short_horizon_within_tolerance():
    """Row at T0+4h*1.05 is within ±10% window — should be found."""
    t_close = T0 + timedelta(hours=4.2)
    detections = [
        _make_detection(T0, 50000.0),
        _make_detection(t_close, 51000.0),
    ]
    repo = _make_repo(detections)
    builder = RegimeDatasetBuilder(repo)
    df = builder.build("BTC")
    row = df[df["detected_at"] == T0].iloc[0]
    assert row["return_4h"] is not None and not pd.isna(row["return_4h"])


def test_short_horizon_outside_tolerance_returns_none():
    """Row at T0+5h is outside ±10% of 4h window [3.6h, 4.4h] — should not match.
    A row at T0+8h keeps the T0 row alive (satisfies return_8h) so we can inspect return_4h."""
    t_far = T0 + timedelta(hours=5)
    t_8h = T0 + timedelta(hours=8)
    detections = [
        _make_detection(T0, 50000.0),
        _make_detection(t_far, 51000.0),  # outside 4h window, inside 8h window (7.2h–8.8h)? No: 5h < 7.2h
        _make_detection(t_8h, 52000.0),   # exactly 8h — keeps T0 alive via return_8h
    ]
    repo = _make_repo(detections)
    builder = RegimeDatasetBuilder(repo)
    df = builder.build("BTC")
    row = df[df["detected_at"] == T0].iloc[0]
    assert pd.isna(row["return_4h"])


def test_short_horizon_tiebreaker_picks_closest():
    """Two candidates in window — picks the one closer to T+4h."""
    t_closer = T0 + timedelta(hours=4, minutes=10)   # 10 min off
    t_farther = T0 + timedelta(hours=4, minutes=20)  # 20 min off
    detections = [
        _make_detection(T0, 50000.0),
        _make_detection(t_closer, 51000.0),
        _make_detection(t_farther, 52000.0),
    ]
    repo = _make_repo(detections)
    builder = RegimeDatasetBuilder(repo)
    df = builder.build("BTC")
    row = df[df["detected_at"] == T0].iloc[0]
    expected = (51000.0 - 50000.0) / 50000.0 * 100
    assert abs(row["return_4h"] - expected) < 0.001


# ── Test: data cleaning ──────────────────────────────────────────────────────

def test_null_current_price_row_dropped():
    """Rows with current_price=None are dropped before any horizon lookup."""
    detections = [
        _make_detection(T0, None),
        _make_detection(T0 + timedelta(hours=1), 50000.0),
    ]
    repo = _make_repo(detections)
    builder = RegimeDatasetBuilder(repo)
    df = builder.build("BTC")
    assert T0 not in df["detected_at"].values


def test_zero_current_price_row_dropped():
    detections = [_make_detection(T0, 0.0)]
    repo = _make_repo(detections)
    builder = RegimeDatasetBuilder(repo)
    df = builder.build("BTC")
    assert len(df) == 0


def test_all_horizons_none_row_dropped():
    """If a row has no matching prices for any horizon, it is dropped."""
    detections = [_make_detection(T0, 50000.0)]  # no other rows → all horizons None
    repo = _make_repo(detections)
    builder = RegimeDatasetBuilder(repo)
    df = builder.build("BTC")
    assert len(df) == 0


# ── Test: long-horizon lookup ─────────────────────────────────────────────────

def test_long_horizon_7d_uses_ohlcv():
    """7d return is sourced from ohlcv_history, not regime_detections."""
    ohlcv_row = {"date": T0 + timedelta(days=7), "close": 55000.0}
    detections = [_make_detection(T0, 50000.0)]
    repo = _make_repo(detections, ohlcv=[ohlcv_row])
    builder = RegimeDatasetBuilder(repo)
    df = builder.build("BTC")
    # repo.get_ohlcv_by_date_range must have been called
    assert repo.get_ohlcv_by_date_range.called


def test_long_horizon_ohlcv_tiebreaker():
    """When multiple ohlcv rows returned, picks closest to T+7d."""
    t7d = T0 + timedelta(days=7)
    ohlcv = [
        {"date": t7d + timedelta(hours=2), "close": 55000.0},   # closer
        {"date": t7d + timedelta(hours=10), "close": 60000.0},  # farther
    ]
    detections = [_make_detection(T0, 50000.0)]
    repo = _make_repo(detections, ohlcv=ohlcv)
    builder = RegimeDatasetBuilder(repo)
    df = builder.build("BTC")
    row = df[df["detected_at"] == T0].iloc[0]
    expected = (55000.0 - 50000.0) / 50000.0 * 100
    assert abs(row["return_7d"] - expected) < 0.001


# ── Test: summary ─────────────────────────────────────────────────────────────

def test_summary_returns_string():
    t4h = T0 + timedelta(hours=4)
    detections = [
        _make_detection(T0, 50000.0),
        _make_detection(t4h, 51000.0),
    ]
    repo = _make_repo(detections)
    builder = RegimeDatasetBuilder(repo)
    df = builder.build("BTC")
    result = builder.summary(df)
    assert isinstance(result, str)
    assert "4h" in result


def test_summary_warns_low_coverage(caplog):
    """Horizons with < 20 matched rows emit a logger.WARNING."""
    import logging
    # 5 detection pairs spaced 6h apart → only 4h horizon gets 5 matches; all others get 0
    rows = []
    for i in range(5):
        t = T0 + timedelta(hours=i * 6)
        t4h = t + timedelta(hours=4)
        rows.append(_make_detection(t, 50000.0 + i))
        rows.append(_make_detection(t4h, 50100.0 + i))
    repo = _make_repo(rows)
    builder = RegimeDatasetBuilder(repo)
    with caplog.at_level(logging.WARNING, logger="coding.core.database.regime_dataset_builder"):
        df = builder.build("BTC")
        builder.summary(df)
    # summary() calls logger.warning for each horizon below 20 rows
    warning_messages = [r.message for r in caplog.records]
    assert any("4h" in m or "8h" in m or "threshold" in m.lower() for m in warning_messages), \
        f"Expected coverage warning, got: {warning_messages}"


def test_dataset_too_small_logs_warning(caplog):
    """Fewer than 30 rows triggers a warning."""
    import logging
    t4h = T0 + timedelta(hours=4)
    detections = [
        _make_detection(T0, 50000.0),
        _make_detection(t4h, 51000.0),
    ]
    repo = _make_repo(detections)
    builder = RegimeDatasetBuilder(repo)
    with caplog.at_level(logging.WARNING):
        builder.build("BTC")
    assert any("too small" in r.message.lower() or "small" in r.message.lower()
               for r in caplog.records)
