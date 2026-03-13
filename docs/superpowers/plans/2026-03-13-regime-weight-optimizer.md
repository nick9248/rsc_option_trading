# Regime Weight Optimizer Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a pipeline that reads historical regime detection snapshots from the DB and runs SLSQP optimization to find weights and thresholds that maximize directional accuracy and simulated Sharpe across multi-horizon forward returns.

**Architecture:** Three new files + one read-only method on `DatabaseRepository`. `RegimeDatasetBuilder` queries the DB and builds a raw DataFrame. `RegimeWeightOptimizer` runs a Dirichlet grid warm-start followed by three SLSQP runs (accuracy, Sharpe, blended). A CLI script ties them together and prints a formatted comparison report.

**Tech Stack:** Python 3.13, pandas, numpy, scipy.optimize.minimize (SLSQP), psycopg2 (existing), pytest

---

## Chunk 1: `get_ohlcv_by_date_range` in DatabaseRepository

### Files
- Modify: `coding/core/database/repository.py` (append after `get_regime_detections`)
- Test: `tests/unit/test_repository_aggregated_flow.py` (or new `tests/unit/test_repository_ohlcv.py`)

### Context you need to read before starting
- `coding/core/database/repository.py` lines 1711–1754 — `get_regime_detections` is the pattern to follow
- `migrations/003_add_regime_detection_tables.sql` lines 4–22 — `ohlcv_history` schema: columns `currency`, `instrument_name`, `date TIMESTAMP`, `close DECIMAL(20,8)`

### What the method does

Queries `ohlcv_history` for rows matching:
- `instrument_name = '{currency}-PERPETUAL'` (e.g., `BTC-PERPETUAL`)
- `date BETWEEN start AND end`

Returns `List[Dict]` where each dict has `{"date": datetime, "close": float}`. The `date` column is `TIMESTAMP WITHOUT TIME ZONE` — psycopg2 returns it as a timezone-naive `datetime`. The `start`/`end` parameters are also timezone-naive datetimes.

---

- [ ] **Step 1.1 — Write the failing test**

Create `tests/unit/test_repository_ohlcv.py`:

```python
from unittest.mock import MagicMock, patch
from datetime import datetime
from coding.core.database.repository import DatabaseRepository


def _make_repo():
    repo = DatabaseRepository.__new__(DatabaseRepository)
    repo.logger = MagicMock()
    return repo


def test_get_ohlcv_by_date_range_returns_list_of_dicts():
    repo = _make_repo()
    start = datetime(2026, 1, 1)
    end = datetime(2026, 1, 8)

    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [
        (datetime(2026, 1, 5), 95000.0),
        (datetime(2026, 1, 6), 96000.0),
    ]

    with patch.object(repo, '_db_cursor') as mock_ctx:
        mock_ctx.return_value.__enter__ = lambda s: mock_cursor
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        result = repo.get_ohlcv_by_date_range("BTC", start, end)

    assert len(result) == 2
    assert result[0] == {"date": datetime(2026, 1, 5), "close": 95000.0}
    assert result[1] == {"date": datetime(2026, 1, 6), "close": 96000.0}


def test_get_ohlcv_by_date_range_uses_perpetual_instrument():
    repo = _make_repo()
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = []

    with patch.object(repo, '_db_cursor') as mock_ctx:
        mock_ctx.return_value.__enter__ = lambda s: mock_cursor
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        repo.get_ohlcv_by_date_range("ETH", datetime(2026, 1, 1), datetime(2026, 1, 8))

    call_args = mock_cursor.execute.call_args
    sql = call_args[0][0]
    params = call_args[0][1]
    assert "ETH-PERPETUAL" in params
    assert "ohlcv_history" in sql


def test_get_ohlcv_by_date_range_empty_result():
    repo = _make_repo()
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = []

    with patch.object(repo, '_db_cursor') as mock_ctx:
        mock_ctx.return_value.__enter__ = lambda s: mock_cursor
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        result = repo.get_ohlcv_by_date_range("BTC", datetime(2026, 1, 1), datetime(2026, 1, 8))

    assert result == []
```

- [ ] **Step 1.2 — Run test to verify it fails**

```bash
cd C:/Users/Nick/PycharmProjects/option_trading && python -m pytest tests/unit/test_repository_ohlcv.py -v
```

Expected: `AttributeError: get_ohlcv_by_date_range` or `ImportError`

- [ ] **Step 1.3 — Implement the method in `repository.py`**

Add after the `get_regime_detections` method (around line 1754):

```python
def get_ohlcv_by_date_range(
    self,
    currency: str,
    start: datetime,
    end: datetime
) -> List[Dict[str, Any]]:
    """
    Retrieve OHLCV candles for a currency's perpetual instrument within a date range.

    Queries ohlcv_history filtered by instrument_name = '{currency}-PERPETUAL'
    and date BETWEEN start AND end. Both start and end are timezone-naive UTC datetimes.

    Args:
        currency: Currency symbol (e.g., "BTC", "ETH").
        start: Start of date range (timezone-naive UTC).
        end: End of date range (timezone-naive UTC).

    Returns:
        List of dicts with {"date": datetime, "close": float}, ordered by date ASC.
    """
    instrument_name = f"{currency}-PERPETUAL"
    with self._db_cursor() as cursor:
        cursor.execute("""
            SELECT date, close
            FROM ohlcv_history
            WHERE instrument_name = %s
              AND date BETWEEN %s AND %s
            ORDER BY date ASC
        """, (instrument_name, start, end))
        return [{"date": row[0], "close": float(row[1])} for row in cursor.fetchall()]
```

- [ ] **Step 1.4 — Run tests to verify they pass**

```bash
cd C:/Users/Nick/PycharmProjects/option_trading && python -m pytest tests/unit/test_repository_ohlcv.py -v
```

Expected: 3 PASSED

- [ ] **Step 1.5 — Commit**

```bash
git add coding/core/database/repository.py tests/unit/test_repository_ohlcv.py
git commit -m "feat: add get_ohlcv_by_date_range read-only method to DatabaseRepository"
```

---

## Chunk 2: RegimeDatasetBuilder — core and short-horizon lookup

### Files
- Create: `coding/core/database/regime_dataset_builder.py`
- Create: `tests/unit/test_regime_dataset_builder.py`

### Context you need
- `coding/core/database/repository.py` — `get_regime_detections` returns `List[Dict]` with keys: `detected_at` (datetime), `currency`, `current_price` (Decimal or float), `trend_score`, `volatility_score`, `momentum_score`, `onchain_score`, `sentiment_score`
- `get_ohlcv_by_date_range` returns `List[Dict]` with `{"date": datetime, "close": float}`
- All datetimes are timezone-naive UTC throughout. Do NOT use `timezone.utc`.

### Short-horizon forward price lookup

For each row at time `T` and horizon `H`:
1. Tolerance window: `[T + H*0.9, T + H*1.1]`
2. Scan the sorted DataFrame for rows where `detected_at` is in that window
3. Among matching rows, pick the one with minimum `abs(detected_at - (T + H))`
4. If none found → `None`
5. `return_H = (found_price - current_price) / current_price * 100`

### Long-horizon forward price lookup

For each row at time `T` and horizon `H` (7 days or 30 days as timedelta):
1. Same tolerance window
2. Call `repository.get_ohlcv_by_date_range(currency, start=T+H*0.9, end=T+H*1.1)`
3. Among returned rows, pick the one with minimum `abs(row["date"] - (T + H))`
4. If empty → `None`
5. `return_H = (found_close - current_price) / current_price * 100`

---

- [ ] **Step 2.1 — Write the failing tests**

Create `tests/unit/test_regime_dataset_builder.py`:

```python
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
    """Row at T0+5h is outside ±10% of 4h window [3.6h, 4.4h] — should not match."""
    t_far = T0 + timedelta(hours=5)
    detections = [
        _make_detection(T0, 50000.0),
        _make_detection(t_far, 51000.0),
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
```

- [ ] **Step 2.2 — Run tests to verify they fail**

```bash
cd C:/Users/Nick/PycharmProjects/option_trading && python -m pytest tests/unit/test_regime_dataset_builder.py -v
```

Expected: `ModuleNotFoundError: coding.core.database.regime_dataset_builder`

- [ ] **Step 2.3 — Implement `RegimeDatasetBuilder`**

Create `coding/core/database/regime_dataset_builder.py`:

```python
import logging
import pandas as pd
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

SHORT_HORIZONS = {
    "return_4h":  timedelta(hours=4),
    "return_8h":  timedelta(hours=8),
    "return_12h": timedelta(hours=12),
    "return_24h": timedelta(hours=24),
    "return_48h": timedelta(hours=48),
    "return_72h": timedelta(hours=72),
}

LONG_HORIZONS = {
    "return_7d":  timedelta(days=7),
    "return_30d": timedelta(days=30),
}

ALL_HORIZONS = {**SHORT_HORIZONS, **LONG_HORIZONS}
COVERAGE_WARN_THRESHOLD = 20
DATASET_MIN_ROWS = 30


class RegimeDatasetBuilder:
    """
    Queries the DB and produces a raw dataset DataFrame for the regime weight optimizer.
    All DB access is read-only. All datetimes are timezone-naive UTC.
    """

    def __init__(self, repository):
        self._repo = repository

    def build(self, currency: str = "BTC") -> pd.DataFrame:
        """
        Build the dataset DataFrame for the given currency.

        Fetches all regime_detections, resolves forward prices for 8 horizons,
        and returns a DataFrame with one row per detection.

        Drops rows where current_price is None or 0.
        Drops rows where all 8 horizons are None.
        Logs a warning if fewer than DATASET_MIN_ROWS rows remain.
        """
        raw = self._repo.get_regime_detections(
            currency,
            start_time=datetime(2020, 1, 1),
            end_time=datetime.utcnow(),
        )
        if not raw:
            logger.warning(f"No regime detections found for {currency}")
            return pd.DataFrame()

        # Sort ascending by detected_at (DB returns DESC)
        raw.sort(key=lambda r: r["detected_at"])

        # Clean: drop rows with invalid current_price
        valid = [r for r in raw if r.get("current_price") and float(r["current_price"]) != 0.0]

        rows = []
        for rec in valid:
            T = rec["detected_at"]
            price = float(rec["current_price"])

            row = {
                "detected_at":       T,
                "currency":          rec["currency"],
                "current_price":     price,
                "trend_score":       float(rec["trend_score"]) if rec["trend_score"] is not None else 0.0,
                "volatility_score":  float(rec["volatility_score"]) if rec["volatility_score"] is not None else 0.0,
                "momentum_score":    float(rec["momentum_score"]) if rec["momentum_score"] is not None else 0.0,
                "onchain_score":     float(rec["onchain_score"]) if rec["onchain_score"] is not None else 0.0,
                "sentiment_score":   float(rec["sentiment_score"]) if rec["sentiment_score"] is not None else 0.0,
            }

            # Short horizons — scan the in-memory sorted list
            for col, H in SHORT_HORIZONS.items():
                row[col] = self._lookup_short(valid, currency, T, price, H)

            # Long horizons — query ohlcv_history
            for col, H in LONG_HORIZONS.items():
                row[col] = self._lookup_long(currency, T, price, H)

            rows.append(row)

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)

        # Drop rows where all 8 horizons are None/NaN
        horizon_cols = list(ALL_HORIZONS.keys())
        all_none_mask = df[horizon_cols].isna().all(axis=1)
        df = df[~all_none_mask].reset_index(drop=True)

        if len(df) < DATASET_MIN_ROWS:
            logger.warning(
                f"Dataset too small for reliable optimization — "
                f"{len(df)} detections available, minimum recommended is {DATASET_MIN_ROWS}."
            )

        return df

    def _lookup_short(
        self,
        sorted_records: list,
        currency: str,
        T: datetime,
        price: float,
        H: timedelta,
    ) -> Optional[float]:
        """Find forward price in regime_detections within ±10% of H after T."""
        target = T + H
        window_start = T + H * 0.9
        window_end = T + H * 1.1

        candidates = [
            r for r in sorted_records
            if r["currency"] == currency
            and r["detected_at"] != T
            and window_start <= r["detected_at"] <= window_end
            and r.get("current_price")
            and float(r["current_price"]) != 0.0
        ]

        if not candidates:
            return None

        best = min(candidates, key=lambda r: abs(r["detected_at"] - target))
        found_price = float(best["current_price"])
        return (found_price - price) / price * 100.0

    def _lookup_long(
        self,
        currency: str,
        T: datetime,
        price: float,
        H: timedelta,
    ) -> Optional[float]:
        """Find forward price in ohlcv_history within ±10% of H after T."""
        target = T + H
        window_start = T + H * 0.9
        window_end = T + H * 1.1

        candles = self._repo.get_ohlcv_by_date_range(currency, window_start, window_end)
        if not candles:
            return None

        best = min(candles, key=lambda r: abs(r["date"] - target))
        found_price = float(best["close"])
        return (found_price - price) / price * 100.0

    def summary(self, df: pd.DataFrame) -> str:
        """
        Returns a formatted string showing horizon coverage statistics.
        Caller is responsible for printing it.
        """
        if df.empty:
            return "Dataset is empty — no coverage statistics available."

        n = len(df)
        lines = []
        horizon_cols = list(ALL_HORIZONS.keys())
        for col in horizon_cols:
            label = col.replace("return_", "")
            count = int(df[col].notna().sum())
            pct = count / n * 100 if n > 0 else 0.0
            if count < COVERAGE_WARN_THRESHOLD:
                logger.warning(
                    f"Horizon {label} has only {count} matched rows "
                    f"(< {COVERAGE_WARN_THRESHOLD} threshold)"
                )
            lines.append(f"  {label:>4}: {count:>4}/{n} ({pct:.0f}%)")

        return "\n".join(lines)
```

- [ ] **Step 2.4 — Run tests to verify they pass**

```bash
cd C:/Users/Nick/PycharmProjects/option_trading && python -m pytest tests/unit/test_regime_dataset_builder.py -v
```

Expected: All tests PASSED.

- [ ] **Step 2.5 — Commit**

```bash
git add coding/core/database/regime_dataset_builder.py tests/unit/test_regime_dataset_builder.py
git commit -m "feat: add RegimeDatasetBuilder — DB → raw dataset DataFrame"
```

---

## Chunk 3: RegimeWeightOptimizer — dataclasses, fitness functions, SLSQP

### Files
- Create: `coding/core/analytics/regime_weight_optimizer.py`
- Create: `tests/unit/analytics/test_regime_weight_optimizer.py`

### Context you need
- `coding/core/analytics/market_regime_detector.py` — `MarketRegimeDetector.WEIGHTS` (dict) and `MarketRegimeDetector.REGIME_THRESHOLDS` (dict). Read the constants at the top of that file.
- The spec's PARAM_ORDER: `[w_trend, w_vol, w_mom, w_onchain, w_sent, sideways_threshold, strong_threshold]`
- Classification is a symmetric elif chain using positive threshold magnitudes (see spec)
- Fitness A: mean accuracy across all (row, horizon) pairs where horizon is not None
- Fitness B: per-horizon Sharpe averaged across horizons; `std` uses `ddof=0`; guard for `len < 2`
- SLSQP minimizes, so pass negated objective; bounds and constraints are specified in spec exactly

---

- [ ] **Step 3.1 — Write the failing tests**

Create `tests/unit/analytics/test_regime_weight_optimizer.py`:

```python
import pytest
import numpy as np
import pandas as pd
from coding.core.analytics.regime_weight_optimizer import (
    RegimeWeightOptimizer,
    ParameterSet,
    OptimizationResult,
)


def _make_df(rows):
    """Build a minimal DataFrame from a list of row dicts."""
    cols = [
        "trend_score", "volatility_score", "momentum_score",
        "onchain_score", "sentiment_score",
        "return_4h", "return_8h", "return_12h", "return_24h",
        "return_48h", "return_72h", "return_7d", "return_30d",
    ]
    return pd.DataFrame(rows, columns=cols)


# ── Classification ────────────────────────────────────────────────────────────

def test_classify_strong_bullish():
    from coding.core.analytics.regime_weight_optimizer import _classify
    assert _classify(60.0, sideways=20.0, strong=55.0) == +1


def test_classify_weak_bullish():
    from coding.core.analytics.regime_weight_optimizer import _classify
    assert _classify(25.0, sideways=20.0, strong=55.0) == +1


def test_classify_sideways():
    from coding.core.analytics.regime_weight_optimizer import _classify
    assert _classify(0.0, sideways=20.0, strong=55.0) == 0


def test_classify_weak_bearish():
    from coding.core.analytics.regime_weight_optimizer import _classify
    assert _classify(-30.0, sideways=20.0, strong=55.0) == -1


def test_classify_strong_bearish():
    from coding.core.analytics.regime_weight_optimizer import _classify
    assert _classify(-60.0, sideways=20.0, strong=55.0) == -1


def test_classify_boundary_sideways_upper():
    """Exactly at +sideways_threshold → Bullish (+1)."""
    from coding.core.analytics.regime_weight_optimizer import _classify
    assert _classify(20.0, sideways=20.0, strong=55.0) == +1


def test_classify_boundary_sideways_lower():
    """Exactly at -sideways_threshold → Sideways (0)."""
    from coding.core.analytics.regime_weight_optimizer import _classify
    assert _classify(-20.0, sideways=20.0, strong=55.0) == 0


# ── Fitness A ─────────────────────────────────────────────────────────────────

def test_fitness_a_perfect_accuracy():
    """All bullish predictions with +2% returns → 100% accuracy."""
    weights = np.array([1.0, 0.0, 0.0, 0.0, 0.0])
    # trend_score=50 → composite=50 → classified as +1
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
    """Bullish prediction but market drops → 0% accuracy."""
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
    """Return within ±1.5% → actual=0; Sideways prediction → correct."""
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


# ── Fitness B ─────────────────────────────────────────────────────────────────

def test_fitness_b_std_zero_returns_zero():
    """All same pnl in a horizon → std=0 → sharpe_H=0.0."""
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
    # pnl for both rows = +1 * 2.0 = 2.0; std=0
    score = opt._fitness_b(x)
    assert score == pytest.approx(0.0)


def test_fitness_b_single_row_returns_zero():
    """Single row per horizon → len < 2 → sharpe_H=0.0."""
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
        pnl = [+1*2.0=2.0,  -1*1.0=-1.0] → mean=0.5, std≈1.5 → sharpe_8h≠0
      12h–30d: no data → excluded from average entirely

    If pooled incorrectly: np.mean([sharpe_4h=0, sharpe_8h=X, 0,0,0,0,0,0]) / 8
    If averaged correctly over available [4h, 8h]: np.mean([0.0, sharpe_8h])
    These differ. Verify by checking sharpe_8h directly.
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

    # Manually compute expected:
    # 4h: predicted=[+1,-1], pnl=[+1*3=3, -1*(-3)=3], mean=3, std=0 → sharpe_4h=0.0
    # 8h: predicted=[+1,-1], pnl=[+1*2=2, -1*1=-1], mean=0.5, std=np.std([2,-1],ddof=0)
    pnl_8h = np.array([2.0, -1.0])
    sharpe_8h = float(np.mean(pnl_8h) / np.std(pnl_8h, ddof=0))
    expected = np.mean([0.0, sharpe_8h])  # only 4h and 8h have data

    score = opt._fitness_b(x)
    assert score == pytest.approx(expected, rel=1e-4)


# ── OptimizationResult structure ─────────────────────────────────────────────

def test_optimize_returns_four_parameter_sets():
    """optimize() returns an OptimizationResult with current + 3 optimal sets."""
    # Build a small but valid dataset with known structure
    rows = []
    np.random.seed(42)
    for i in range(40):
        trend = np.random.uniform(-50, 50)
        ret_4h = trend * 0.04 + np.random.normal(0, 1)  # weakly correlated to trend
        rows.append({
            "trend_score": trend, "volatility_score": 0.0, "momentum_score": 0.0,
            "onchain_score": 0.0, "sentiment_score": 0.0,
            "return_4h": ret_4h, "return_8h": None, "return_12h": None,
            "return_24h": None, "return_48h": None, "return_72h": None,
            "return_7d": None, "return_30d": None,
        })
    df = pd.DataFrame(rows)
    opt = RegimeWeightOptimizer(df, directional_threshold=1.5)
    result = opt.optimize()

    assert isinstance(result, OptimizationResult)
    assert isinstance(result.current, ParameterSet)
    assert isinstance(result.accuracy_optimal, ParameterSet)
    assert isinstance(result.sharpe_optimal, ParameterSet)
    assert isinstance(result.blended_optimal, ParameterSet)


def test_all_parameter_sets_have_both_fitness_values():
    """Every ParameterSet must have fitness_a and fitness_b populated."""
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
    """All optimal weight sets must sum to 1.0."""
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
        assert weight_sum == pytest.approx(1.0, abs=1e-4), \
            f"{ps_name} weights sum to {weight_sum}"


def test_strong_threshold_greater_than_sideways():
    """strong_threshold must be > sideways_threshold in all results."""
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


def test_fitness_a_multi_row_multi_horizon():
    """Accuracy computed correctly across multiple rows and multiple horizons."""
    # 2 rows × 2 non-None horizons = 4 (row, horizon) pairs
    # Row 0: trend=+50 → predicted=+1
    #   return_4h=+2.0 → actual=+1 → correct
    #   return_8h=-2.0 → actual=-1 → wrong
    # Row 1: trend=-50 → predicted=-1
    #   return_4h=-2.0 → actual=-1 → correct
    #   return_8h=+2.0 → actual=+1 → wrong
    # Accuracy = 2/4 = 0.5
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


def test_optimizer_improves_on_known_signal():
    """
    SLSQP should find trend weight > 0.5 when trend_score is a strong predictor.
    Build a dataset where return_4h = sign(trend_score) * 3% reliably.
    The current hand-tuned weights give trend=0.30 — optimizer should push it higher.
    """
    np.random.seed(99)
    rows = []
    for _ in range(60):
        trend = np.random.choice([-60.0, -40.0, 40.0, 60.0])
        # Pure signal: trend direction predicts 4h direction perfectly
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
    # Optimizer should recognize trend as the dominant signal
    assert result.accuracy_optimal.weights["trend"] > 0.4, \
        f"Expected trend weight > 0.4, got {result.accuracy_optimal.weights['trend']:.3f}"
    # And it should improve (or at least match) the current baseline
    assert result.accuracy_optimal.fitness_a >= result.current.fitness_a - 0.01


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
```

- [ ] **Step 3.2 — Run tests to verify they fail**

```bash
cd C:/Users/Nick/PycharmProjects/option_trading && python -m pytest tests/unit/analytics/test_regime_weight_optimizer.py -v
```

Expected: `ModuleNotFoundError: coding.core.analytics.regime_weight_optimizer`

- [ ] **Step 3.3 — Implement `RegimeWeightOptimizer`**

Create `coding/core/analytics/regime_weight_optimizer.py`:

```python
import logging
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Optional
from scipy.optimize import minimize

from coding.core.analytics.market_regime_detector import MarketRegimeDetector

logger = logging.getLogger(__name__)

PARAM_ORDER = ["trend", "volatility", "momentum", "onchain", "sentiment",
               "sideways_threshold", "strong_threshold"]

HORIZON_COLS = ["return_4h", "return_8h", "return_12h", "return_24h",
                "return_48h", "return_72h", "return_7d", "return_30d"]

HORIZON_LABELS = ["4h", "8h", "12h", "24h", "48h", "72h", "7d", "30d"]

WEIGHT_BOUNDS = (0.05, 0.60)
SIDEWAYS_BOUNDS = (10.0, 30.0)
STRONG_BOUNDS = (40.0, 70.0)
GRID_SAMPLES = 500


def _classify(composite: float, sideways: float, strong: float) -> int:
    """Classify composite score into +1 (Bullish), 0 (Sideways), -1 (Bearish)."""
    if composite >= strong:
        return +1
    elif composite >= sideways:
        return +1
    elif composite >= -sideways:
        return 0
    elif composite >= -strong:
        return -1
    else:
        return -1


@dataclass
class ParameterSet:
    weights: dict          # keys: trend, volatility, momentum, onchain, sentiment
    sideways_threshold: float
    strong_threshold: float
    fitness_a: float       # directional accuracy (0–1)
    fitness_b: float       # averaged per-horizon Sharpe


@dataclass
class OptimizationResult:
    current: ParameterSet
    accuracy_optimal: ParameterSet
    sharpe_optimal: ParameterSet
    blended_optimal: ParameterSet
    dataset_rows: int
    horizon_coverage: dict   # {"4h": count, ...} — non-None row count per horizon


class RegimeWeightOptimizer:
    """
    Finds optimal weights and thresholds for MarketRegimeDetector via SLSQP.
    Operates on a DataFrame produced by RegimeDatasetBuilder.
    """

    def __init__(self, df: pd.DataFrame, directional_threshold: float = 1.5):
        self._df = df
        self._dir_thresh = directional_threshold
        # Precompute score matrix and return matrix for speed
        self._scores = df[["trend_score", "volatility_score", "momentum_score",
                           "onchain_score", "sentiment_score"]].to_numpy(dtype=float)
        self._returns = df[HORIZON_COLS].to_numpy(dtype=float)  # NaN for missing

    def _fitness_a(self, x: np.ndarray) -> float:
        """Directional accuracy across all (row, horizon) pairs with data."""
        weights = x[:5]
        sideways = x[5]
        strong = x[6]

        composites = self._scores @ weights
        correct_count = 0
        total = 0

        for i, ret_row in enumerate(self._returns):
            composite = composites[i]
            predicted = _classify(composite, sideways, strong)
            for ret in ret_row:
                if np.isnan(ret):
                    continue
                if ret > self._dir_thresh:
                    actual = +1
                elif ret < -self._dir_thresh:
                    actual = -1
                else:
                    actual = 0
                correct_count += (predicted == actual)
                total += 1

        return correct_count / total if total > 0 else 0.0

    def _fitness_b(self, x: np.ndarray) -> float:
        """Per-horizon Sharpe averaged across available horizons only.

        Horizons with zero rows are excluded from the average entirely.
        Horizons with exactly one row contribute 0.0 (can't compute Sharpe).
        """
        weights = x[:5]
        sideways = x[5]
        strong = x[6]

        composites = self._scores @ weights
        predictions = np.array([_classify(c, sideways, strong) for c in composites])

        sharpes = []
        for h_idx in range(len(HORIZON_COLS)):
            ret_col = self._returns[:, h_idx]
            mask = ~np.isnan(ret_col)
            if mask.sum() == 0:
                continue              # no data for this horizon — exclude from average
            if mask.sum() < 2:
                sharpes.append(0.0)   # single point — guard, contributes 0
                continue
            pnl = predictions[mask] * ret_col[mask]
            std = np.std(pnl, ddof=0)
            sharpes.append(0.0 if std == 0.0 else float(np.mean(pnl) / std))

        return float(np.mean(sharpes)) if sharpes else 0.0

    def _eval_both(self, x: np.ndarray):
        a = self._fitness_a(x)
        b = self._fitness_b(x)
        return a, b

    def _x_to_param_set(self, x: np.ndarray, fa: float, fb: float) -> ParameterSet:
        return ParameterSet(
            weights={k: float(x[i]) for i, k in enumerate(PARAM_ORDER[:5])},
            sideways_threshold=float(x[5]),
            strong_threshold=float(x[6]),
            fitness_a=fa,
            fitness_b=fb,
        )

    def _run_slsqp(self, x0: np.ndarray, objective_fn) -> np.ndarray:
        bounds = [WEIGHT_BOUNDS] * 5 + [SIDEWAYS_BOUNDS, STRONG_BOUNDS]
        constraints = [
            {"type": "eq",   "fun": lambda x: x[0]+x[1]+x[2]+x[3]+x[4] - 1.0},
            {"type": "ineq", "fun": lambda x: x[6] - x[5] - 1.0},
        ]
        result = minimize(
            fun=lambda x: -objective_fn(x),
            x0=x0,
            method="SLSQP",
            bounds=bounds,
            constraints=constraints,
            options={"ftol": 1e-9, "maxiter": 500},
        )
        return result.x

    def optimize(self) -> OptimizationResult:
        # ── Horizon coverage ──────────────────────────────────────────────────
        coverage = {
            label: int(np.sum(~np.isnan(self._returns[:, i])))
            for i, label in enumerate(HORIZON_LABELS)
        }

        # ── Current baseline ──────────────────────────────────────────────────
        cur_w = MarketRegimeDetector.WEIGHTS
        cur_sideways = float(MarketRegimeDetector.REGIME_THRESHOLDS["Weak Bullish"])
        cur_strong = float(MarketRegimeDetector.REGIME_THRESHOLDS["Strong Bullish"])
        x_current = np.array([
            cur_w["trend"], cur_w["volatility"], cur_w["momentum"],
            cur_w["onchain"], cur_w["sentiment"],
            cur_sideways, cur_strong,
        ])
        fa_cur, fb_cur = self._eval_both(x_current)
        current = self._x_to_param_set(x_current, fa_cur, fb_cur)

        # ── Grid warm-start ───────────────────────────────────────────────────
        logger.info(f"Running grid warm-start with {GRID_SAMPLES} samples...")
        grid_points = self._sample_grid(GRID_SAMPLES)

        best_x_a, best_a = x_current.copy(), fa_cur
        best_x_b, best_b = x_current.copy(), fb_cur
        best_x_blend, best_blend = x_current.copy(), 0.5 * fa_cur + 0.5 * fb_cur

        for x in grid_points:
            fa, fb = self._eval_both(x)
            blend = 0.5 * fa + 0.5 * fb
            if fa > best_a:
                best_a, best_x_a = fa, x.copy()
            if fb > best_b:
                best_b, best_x_b = fb, x.copy()
            if blend > best_blend:
                best_blend, best_x_blend = blend, x.copy()

        # ── SLSQP runs ────────────────────────────────────────────────────────
        logger.info("Running SLSQP: accuracy objective...")
        x_acc = self._run_slsqp(best_x_a, self._fitness_a)
        fa_acc, fb_acc = self._eval_both(x_acc)

        logger.info("Running SLSQP: Sharpe objective...")
        x_shr = self._run_slsqp(best_x_b, self._fitness_b)
        fa_shr, fb_shr = self._eval_both(x_shr)

        logger.info("Running SLSQP: blended objective...")
        x_blend = self._run_slsqp(best_x_blend, lambda x: 0.5*self._fitness_a(x) + 0.5*self._fitness_b(x))
        fa_bld, fb_bld = self._eval_both(x_blend)

        return OptimizationResult(
            current=current,
            accuracy_optimal=self._x_to_param_set(x_acc, fa_acc, fb_acc),
            sharpe_optimal=self._x_to_param_set(x_shr, fa_shr, fb_shr),
            blended_optimal=self._x_to_param_set(x_blend, fa_bld, fb_bld),
            dataset_rows=len(self._df),
            horizon_coverage=coverage,
        )

    def _sample_grid(self, n: int) -> list:
        rng = np.random.default_rng(seed=42)
        samples = []
        attempts = 0
        while len(samples) < n and attempts < n * 50:
            attempts += 1
            w = rng.dirichlet([1.0, 1.0, 1.0, 1.0, 1.0])
            if np.any(w < WEIGHT_BOUNDS[0]) or np.any(w > WEIGHT_BOUNDS[1]):
                continue
            sideways = rng.uniform(*SIDEWAYS_BOUNDS)
            strong = rng.uniform(*STRONG_BOUNDS)
            if strong - sideways < 1.0:
                continue
            samples.append(np.array([*w, sideways, strong]))
        if len(samples) < n:
            logger.warning(f"Grid warm-start: only collected {len(samples)}/{n} valid samples")
        return samples
```

- [ ] **Step 3.4 — Run tests to verify they pass**

```bash
cd C:/Users/Nick/PycharmProjects/option_trading && python -m pytest tests/unit/analytics/test_regime_weight_optimizer.py -v
```

Expected: All tests PASSED. (The optimizer tests with 40-row synthetic datasets will run in seconds.)

- [ ] **Step 3.5 — Commit**

```bash
git add coding/core/analytics/regime_weight_optimizer.py tests/unit/analytics/test_regime_weight_optimizer.py
git commit -m "feat: add RegimeWeightOptimizer — SLSQP optimization with per-horizon Sharpe"
```

---

## Chunk 4: CLI Script and full test suite pass

### Files
- Create: `scripts/optimize_regime_weights.py`
- No new test file needed — the CLI is a thin wrapper; verify via manual smoke test

### Context
- The CLI wires together `DatabaseRepository` → `RegimeDatasetBuilder` → `RegimeWeightOptimizer`
- Output format is exactly as shown in the spec
- `--currency` defaults to BTC, `--directional-threshold` defaults to 1.5
- Accuracy displayed as `fitness_a * 100` (percentage)
- Deltas: accuracy shows `(+X.X pp)`, Sharpe shows `(+X.XX)`, blended shows no delta
- Use `coding.core.logging.logging_setup.init_logging` for the script

---

- [ ] **Step 4.1 — Implement the CLI script**

Create `scripts/optimize_regime_weights.py`:

```python
"""
Regime weight optimizer CLI.

Usage:
    python -m scripts.optimize_regime_weights --currency BTC
    python -m scripts.optimize_regime_weights --currency ETH
    python -m scripts.optimize_regime_weights --currency BTC --directional-threshold 2.0
"""
import argparse
import logging

from coding.core.logging.logging_setup import init_logging
from coding.core.database.repository import DatabaseRepository
from coding.core.database.regime_dataset_builder import RegimeDatasetBuilder
from coding.core.analytics.regime_weight_optimizer import RegimeWeightOptimizer, ParameterSet

init_logging(level="INFO")
logger = logging.getLogger(__name__)


def _fmt_param_set(ps: ParameterSet, label: str, current: ParameterSet = None) -> str:
    w = ps.weights
    lines = [f"{label}:"]
    lines.append(
        f"  trend={w['trend']:.2f}  vol={w['volatility']:.2f}  "
        f"momentum={w['momentum']:.2f}  onchain={w['onchain']:.2f}  "
        f"sentiment={w['sentiment']:.2f}"
    )
    lines.append(
        f"  sideways=\u00b1{ps.sideways_threshold:.0f}  "
        f"strong=\u00b1{ps.strong_threshold:.0f}"
    )

    acc_str = f"{ps.fitness_a * 100:.1f}%"
    shr_str = f"{ps.fitness_b:.2f}"

    if current is not None and label == "ACCURACY-OPTIMAL":
        delta_a = (ps.fitness_a - current.fitness_a) * 100
        acc_str += f" ({delta_a:+.1f} pp)"
    elif current is not None and label == "SHARPE-OPTIMAL":
        delta_b = ps.fitness_b - current.fitness_b
        shr_str += f" ({delta_b:+.2f})"

    lines.append(f"  Accuracy: {acc_str}   Sharpe: {shr_str}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Optimize regime detection weights")
    parser.add_argument("--currency", default="BTC", choices=["BTC", "ETH"])
    parser.add_argument("--directional-threshold", type=float, default=1.5,
                        dest="directional_threshold")
    args = parser.parse_args()

    repo = DatabaseRepository()
    builder = RegimeDatasetBuilder(repo)

    logger.info(f"Building dataset for {args.currency}...")
    df = builder.build(args.currency)

    if df.empty:
        print("No data available. Exiting.")
        return

    # Date range from dataset
    date_start = df["detected_at"].min().strftime("%Y-%m-%d")
    date_end = df["detected_at"].max().strftime("%Y-%m-%d")
    n = len(df)

    print()
    print("=== REGIME WEIGHT OPTIMIZER ===")
    print(f"Currency: {args.currency}")
    print(f"Dataset: {n} detections ({date_start} \u2192 {date_end})")
    print()
    print("Horizon coverage:")
    print(builder.summary(df))
    print()

    logger.info("Running optimizer...")
    opt = RegimeWeightOptimizer(df, directional_threshold=args.directional_threshold)
    result = opt.optimize()

    sep = "\u2500" * 60
    print(sep)
    print(_fmt_param_set(result.current, "CURRENT (hand-tuned)"))
    print()
    print(_fmt_param_set(result.accuracy_optimal, "ACCURACY-OPTIMAL", result.current))
    print()
    print(_fmt_param_set(result.sharpe_optimal, "SHARPE-OPTIMAL", result.current))
    print()
    blended_lines = _fmt_param_set(result.blended_optimal, "BLENDED (50/50)")
    print(blended_lines)
    print("  (no delta — blended has no single natural baseline)")
    print(sep)
    print("To apply: update WEIGHTS and REGIME_THRESHOLDS in market_regime_detector.py")
    print()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4.2 — Run the full test suite to confirm nothing is broken**

```bash
cd C:/Users/Nick/PycharmProjects/option_trading && python -m pytest tests/unit/ -v --tb=short 2>&1 | tail -30
```

Expected: All existing tests still pass. New tests pass. Zero failures.

- [ ] **Step 4.3 — Smoke test the CLI (optional, requires DB connection)**

```bash
cd C:/Users/Nick/PycharmProjects/option_trading && python -m scripts.optimize_regime_weights --currency BTC
```

Expected: Prints dataset stats, horizon coverage, and the four parameter set comparisons.

- [ ] **Step 4.4 — Commit**

```bash
git add scripts/optimize_regime_weights.py
git commit -m "feat: add optimize_regime_weights CLI script"
```

---

## Chunk 5: Final integration and branch close

- [ ] **Step 5.1 — Run full test suite one final time**

```bash
cd C:/Users/Nick/PycharmProjects/option_trading && python -m pytest tests/ -v --tb=short 2>&1 | tail -40
```

Expected: Zero failures.

- [ ] **Step 5.2 — Commit any remaining unstaged changes**

```bash
git status
```

If clean, proceed. If not, stage and commit.

- [ ] **Step 5.3 — Push branch and open PR**

```bash
git push -u origin quality-check
```

Then open a PR from `quality-check` → `main` with title:
`feat: regime weight optimizer + MarketRegimeDetector redesign`

Body:
```
## Summary
- Complete redesign of MarketRegimeDetector scoring (trend, volatility, momentum, onchain, sentiment)
- Fixed 3 critical scoring bugs (ADX multiplier path, funding rate double-divide, MACD histogram correlation)
- Fixed non-monotonic funding rate scoring (extreme funding = crowded positioning = contrarian signal)
- Added RegimeDatasetBuilder: DB snapshots → multi-horizon forward-return DataFrame
- Added RegimeWeightOptimizer: Dirichlet grid warm-start + 3 SLSQP runs (accuracy, Sharpe, blended)
- Added CLI: `python -m scripts.optimize_regime_weights --currency BTC`

## How to run
```bash
python -m scripts.optimize_regime_weights --currency BTC
python -m scripts.optimize_regime_weights --currency ETH
```
```
