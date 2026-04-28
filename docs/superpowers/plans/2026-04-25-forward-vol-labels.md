# Forward Vol Labels Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the vol regressor to predict *future* 24h realized volatility (T → T+24h) instead of *past* vol (T-24h → T), and remove the resulting label-leakage feature from the training data.

**Architecture:** Two-file change. `LabelGenerator` gains a `_get_forward_prices` method and uses it for `realized_vol_24h`; the past-price history still feeds trend/drawdown labels. `MLDataLoader._align_data` gains one extra exclusion line to prevent the label (now forward vol) from silently appearing in the feature set. Retrain all 4 models after both fixes.

**Tech Stack:** Python 3.13, LightGBM, pandas, psycopg2, pytest

---

## File Map

| Action | File | Change |
|--------|------|--------|
| Modify | `coding/core/ml/label_generator.py` | Add `_get_forward_prices`; wire it into `generate_labels` for `realized_vol_24h` |
| Modify | `coding/core/ml/data/data_loader.py` | `_align_data`: exclude `*_label` shadow columns from feature set |
| Create | `tests/unit/ml/__init__.py` | Empty init for new package |
| Create | `tests/unit/ml/test_forward_vol_labels.py` | Tests for forward-looking label generation |
| Create | `tests/unit/ml/test_data_loader_no_leakage.py` | Tests that `_align_data` strips leaky columns |
| Run | `scripts/train_ml_models.py` | Retrain all 4 models |

---

## Background (read before coding)

### Why the old labels were wrong

`LabelGenerator._get_price_history` returns prices from `[T-30d, T]`. `_calculate_realized_vol` takes the **last 24h** of those prices → computes vol([T-24h, T]). So the training target for the vol regressor was *current realized vol*, not *future* vol.

`MLDataLoader._align_data` joins features and labels with `rsuffix='_label'`. Because both the feature DataFrame and the label DataFrame have a column named `realized_vol_24h`, after the join the label's column becomes `realized_vol_24h_label` — and since `_align_data` only excludes `label_columns` (which contains `realized_vol_24h`, not `realized_vol_24h_label`), the forward target leaked into the feature set. The model trivially learned `predicted_vol ≈ input_vol`.

### After the fix

- `realized_vol_24h` **label** = `std(log_returns) * sqrt(24*365) * 100` from prices `[T, T+24h]` — forward-looking.
- `realized_vol_24h` **feature** (computed in `_compute_derived_features`) = rolling 24h std from hourly snapshots, past-facing — kept, it's a valid vol-clustering signal.
- `realized_vol_24h_label` column — explicitly excluded from features in `_align_data`.

### Inference staleness (acceptable trade-off)

At prediction time, forward labels for the most recent ~24h don't exist yet (future trades not in DB). `_align_data` uses an inner join, so those rows are dropped. The predictor uses `features.iloc[-1:]` — the latest row with a valid forward label will be ~24h old. The signals that matter most (DVOL, avg_iv) change slowly; 24h staleness is acceptable.

---

## Task 1: Add `_get_forward_prices` to LabelGenerator

**Files:**
- Modify: `coding/core/ml/label_generator.py`
- Create: `tests/unit/ml/__init__.py`
- Create: `tests/unit/ml/test_forward_vol_labels.py`

- [ ] **Step 1: Create test package init**

```python
# tests/unit/ml/__init__.py
# (empty file)
```

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/ml/test_forward_vol_labels.py
"""Tests that LabelGenerator computes realized_vol_24h from FORWARD prices."""
import math
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, call
import numpy as np
import pytest

from coding.core.ml.label_generator import LabelGenerator


def _make_hourly_prices(base_price: float, n: int, step: float = 0.0):
    """Return list of price dicts with a known vol profile."""
    return [{"timestamp": datetime(2026, 4, 10, h), "price": base_price * (1 + step * h)}
            for h in range(n)]


def _annualized_vol(prices: list) -> float:
    arr = np.array([p["price"] for p in prices])
    log_ret = np.diff(np.log(arr))
    return float(np.std(log_ret) * np.sqrt(24 * 365) * 100)


class TestForwardVolLabels:

    def _make_generator(self):
        gen = LabelGenerator.__new__(LabelGenerator)
        gen.repo = MagicMock()
        return gen

    def test_forward_prices_query_covers_24h_window(self):
        """_get_forward_prices should query [timestamp, timestamp+24h]."""
        gen = self._make_generator()
        gen.repo._get_connection.return_value.__enter__ = MagicMock()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        conn.cursor.return_value = cursor
        gen.repo._get_connection.return_value = conn

        ts = datetime(2026, 4, 10, 12, 0)
        gen._get_forward_prices("BTC", ts, forward_hours=24)

        cursor.execute.assert_called_once()
        sql, params = cursor.execute.call_args[0]
        assert params[1] == ts                          # start = timestamp
        assert params[2] == ts + timedelta(hours=24)    # end = timestamp + 24h

    def test_generate_labels_rv24h_uses_forward_prices(self):
        """realized_vol_24h in labels must equal vol computed from FORWARD prices."""
        gen = self._make_generator()
        ts = datetime(2026, 4, 10, 12, 0)

        # Past prices: calm (near-zero returns)
        calm = _make_hourly_prices(80000, 720, step=0.00001)
        # Forward prices: volatile (1% step each hour)
        volatile = _make_hourly_prices(80000, 25, step=0.01)

        with patch.object(gen, '_get_price_history', return_value=calm), \
             patch.object(gen, '_get_forward_prices', return_value=volatile), \
             patch.object(gen, '_calculate_iv_metrics', return_value=(50.0, 'contango')):

            labels = gen.generate_labels("BTC", ts)

        expected_vol = _annualized_vol(volatile[-24:])
        assert labels is not None
        assert abs(labels.realized_vol_24h - expected_vol) < 0.1

    def test_generate_labels_rv24h_is_none_when_insufficient_forward_data(self):
        """If fewer than 3 forward price points exist, realized_vol_24h should be None."""
        gen = self._make_generator()
        ts = datetime(2026, 4, 10, 12, 0)

        calm = _make_hourly_prices(80000, 720, step=0.00001)
        sparse_forward = _make_hourly_prices(80000, 2, step=0.01)  # only 2 points

        with patch.object(gen, '_get_price_history', return_value=calm), \
             patch.object(gen, '_get_forward_prices', return_value=sparse_forward), \
             patch.object(gen, '_calculate_iv_metrics', return_value=(50.0, 'contango')):

            labels = gen.generate_labels("BTC", ts)

        # labels can be None (if other required data is also missing) or have None vol
        assert labels is None or labels.realized_vol_24h is None

    def test_forward_prices_returns_correct_format(self):
        """_get_forward_prices must return list of dicts with 'price' key."""
        gen = self._make_generator()
        conn = MagicMock()
        cursor = MagicMock()
        ts = datetime(2026, 4, 10, 12, 0)
        cursor.fetchall.return_value = [
            (datetime(2026, 4, 10, 12), 80000.0),
            (datetime(2026, 4, 10, 13), 80100.0),
        ]
        conn.cursor.return_value = cursor
        gen.repo._get_connection.return_value = conn

        result = gen._get_forward_prices("BTC", ts, forward_hours=24)

        assert len(result) == 2
        assert result[0]["price"] == 80000.0
        assert result[1]["price"] == 80100.0
```

- [ ] **Step 3: Run tests to confirm they fail**

```
pytest tests/unit/ml/test_forward_vol_labels.py -v
```

Expected: `AttributeError: 'LabelGenerator' object has no attribute '_get_forward_prices'`

- [ ] **Step 4: Add `_get_forward_prices` and wire it into `generate_labels`**

In `coding/core/ml/label_generator.py`, add this method after `_get_price_history` (around line 210):

```python
def _get_forward_prices(
    self,
    currency: str,
    timestamp: datetime,
    forward_hours: int = 24
) -> list:
    """
    Fetch hourly average prices AFTER timestamp for forward vol calculation.

    Args:
        currency: Currency symbol.
        timestamp: Start of the forward window (inclusive).
        forward_hours: Number of hours to look forward.

    Returns:
        List of {'timestamp': datetime, 'price': float} sorted ascending.
    """
    connection = self.repo._get_connection()
    try:
        cursor = connection.cursor()
        end_time = timestamp + timedelta(hours=forward_hours)
        cursor.execute(
            """
            SELECT
                DATE_TRUNC('hour', TO_TIMESTAMP(trade_timestamp / 1000.0)) AS hour,
                AVG(index_price) AS avg_price
            FROM historical_trades
            WHERE currency = %s
              AND TO_TIMESTAMP(trade_timestamp / 1000.0) >= %s
              AND TO_TIMESTAMP(trade_timestamp / 1000.0) <= %s
              AND index_price IS NOT NULL
            GROUP BY hour
            ORDER BY hour
            """,
            (currency, timestamp, end_time)
        )
        rows = cursor.fetchall()
        cursor.close()
        return [{"timestamp": row[0], "price": float(row[1])} for row in rows]
    finally:
        self.repo._return_connection(connection)
```

In `generate_labels` (around line 113), replace:

```python
# Old — uses backward-looking prices
rv_24h = self._calculate_realized_vol(prices, window_hours=24)
```

with:

```python
# Forward-looking: compute vol from [timestamp, timestamp+24h]
forward_prices = self._get_forward_prices(currency, timestamp, forward_hours=24)
rv_24h = self._calculate_realized_vol(forward_prices, window_hours=24)
```

- [ ] **Step 5: Run tests to verify they pass**

```
pytest tests/unit/ml/test_forward_vol_labels.py -v
```

Expected: 4 PASSED

- [ ] **Step 6: Commit**

```bash
git add coding/core/ml/label_generator.py tests/unit/ml/__init__.py tests/unit/ml/test_forward_vol_labels.py
git commit -m "feat: compute realized_vol_24h label from forward prices [T, T+24h]"
```

---

## Task 2: Remove label leakage from MLDataLoader

**Files:**
- Modify: `coding/core/ml/data/data_loader.py`
- Create: `tests/unit/ml/test_data_loader_no_leakage.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/ml/test_data_loader_no_leakage.py
"""Tests that _align_data does not leak label columns into features."""
import pandas as pd
import numpy as np
import pytest
from datetime import datetime

from coding.core.ml.data.data_loader import MLDataLoader


def _make_loader():
    loader = MLDataLoader.__new__(MLDataLoader)
    return loader


class TestAlignDataNoLeakage:

    def test_realized_vol_label_not_in_features(self):
        """After _align_data, realized_vol_24h_label must NOT appear in features."""
        loader = _make_loader()

        idx = pd.date_range("2026-04-01", periods=10, freq="h")
        # Features have 'realized_vol_24h' (past vol computed from hourly snapshots)
        features = pd.DataFrame({
            "avg_iv": np.random.uniform(30, 60, 10),
            "realized_vol_24h": np.random.uniform(15, 45, 10),
        }, index=idx)
        # Labels also have 'realized_vol_24h' (forward vol — the target)
        labels = pd.DataFrame({
            "realized_vol_24h": np.random.uniform(20, 70, 10),
            "market_regime": ["sideways"] * 10,
        }, index=idx)

        aligned_features, aligned_labels = loader._align_data(features, labels)

        assert "realized_vol_24h_label" not in aligned_features.columns, (
            "realized_vol_24h_label must not appear in features — it is the forward vol target "
            "and unavailable at inference time"
        )

    def test_original_realized_vol_feature_retained(self):
        """The past-vol feature 'realized_vol_24h' (from hourly snapshots) must stay in features."""
        loader = _make_loader()

        idx = pd.date_range("2026-04-01", periods=10, freq="h")
        features = pd.DataFrame({
            "avg_iv": np.random.uniform(30, 60, 10),
            "realized_vol_24h": np.random.uniform(15, 45, 10),
        }, index=idx)
        labels = pd.DataFrame({
            "realized_vol_24h": np.random.uniform(20, 70, 10),
            "market_regime": ["sideways"] * 10,
        }, index=idx)

        aligned_features, aligned_labels = loader._align_data(features, labels)

        assert "realized_vol_24h" in aligned_features.columns, (
            "Past-vol feature must remain in features as a vol-clustering signal"
        )

    def test_label_columns_not_in_features(self):
        """No label column (raw or suffixed) should appear in the returned features."""
        loader = _make_loader()

        idx = pd.date_range("2026-04-01", periods=5, freq="h")
        features = pd.DataFrame({
            "avg_iv": [40.0] * 5,
            "realized_vol_24h": [20.0] * 5,
            "market_regime": ["sideways"] * 5,   # same name as a label column
        }, index=idx)
        labels = pd.DataFrame({
            "realized_vol_24h": [35.0] * 5,
            "market_regime": ["sideways"] * 5,
        }, index=idx)

        aligned_features, aligned_labels = loader._align_data(features, labels)

        for col in ["realized_vol_24h_label", "market_regime_label"]:
            assert col not in aligned_features.columns, f"{col} must not be in features"
```

- [ ] **Step 2: Run test to confirm it fails**

```
pytest tests/unit/ml/test_data_loader_no_leakage.py -v
```

Expected: `FAILED — assert 'realized_vol_24h_label' not in aligned_features.columns`

- [ ] **Step 3: Fix `_align_data` in `data_loader.py`**

In `coding/core/ml/data/data_loader.py`, find `_align_data` (around line 551). Change the feature column selection from:

```python
# Old
label_columns = labels.columns.tolist()
feature_columns = [col for col in aligned.columns if col not in label_columns]
```

to:

```python
label_columns = labels.columns.tolist()
# Exclude both the raw label columns AND any *_label-suffixed shadow columns
# that appear when features and labels share a column name (rsuffix='_label' join).
label_suffixed = [f"{col}_label" for col in label_columns]
all_excluded = set(label_columns) | set(label_suffixed)
feature_columns = [col for col in aligned.columns if col not in all_excluded]
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/unit/ml/test_data_loader_no_leakage.py -v
```

Expected: 3 PASSED

- [ ] **Step 5: Run full test suite to check no regressions**

```
pytest tests/unit/ -v --tb=short
```

Expected: all previously passing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add coding/core/ml/data/data_loader.py tests/unit/ml/test_data_loader_no_leakage.py
git commit -m "fix: exclude *_label shadow columns from ML feature set to prevent label leakage"
```

---

## Task 3: Retrain and verify

**Files:**
- Run: `scripts/train_ml_models.py`

- [ ] **Step 1: Retrain all 4 models**

```
python -m scripts.train_ml_models
```

Expected changes vs previous run (Apr 25):
- Vol regressor R² will **drop** (forward vol is harder to predict than past vol — this is correct)
- Vol regressor predictions should now be in the 30–55% range (closer to DVOL and avg_iv), not 8–15%
- The feature `realized_vol_24h_label` will no longer appear in model metadata `feature_names`
- Training data will have ~24 fewer rows per currency (the last 24h have no forward labels yet)

- [ ] **Step 2: Verify prediction output is now plausible**

```python
python -c "
import logging, sys
logging.basicConfig(level=logging.WARNING, stream=sys.stdout)
from coding.core.ml.inference.predictor import MLPredictor
p = MLPredictor()
for ccy in ['BTC', 'ETH']:
    r = p.predict_volatility(ccy)
    print(f'{ccy} predicted vol: {r.get(\"predicted_vol_24h\", r.get(\"error\")):.1f}%')
"
```

Expected: predictions near DVOL (~40%) rather than near recent realized vol (~12%). If still near 12%, the label fix did not take effect — check that the retrain used fresh data (not cached models).

- [ ] **Step 3: Check new model metadata has no `realized_vol_24h_label` feature**

```python
python -c "
import json, glob
for f in sorted(glob.glob('models/BTC_realized_vol*20260425*/metadata.json'))[-1:]:
    meta = json.load(open(f))
    features = meta['feature_names']
    assert 'realized_vol_24h_label' not in features, 'Leaky feature still present!'
    print(f'Features ({len(features)}): OK — no label leakage')
    print('  realized_vol_24h present (past vol feature):', 'realized_vol_24h' in features)
    print('  avg_iv present:', 'avg_iv' in features)
    print('  dvol present:', 'dvol' in features)
"
```

Expected: no `realized_vol_24h_label`, `realized_vol_24h` present (past vol clustering feature), `avg_iv` and `dvol` present.

- [ ] **Step 4: Commit**

```bash
git add models/model_registry.json
git commit -m "retrain: vol regressor now predicts forward vol, no label leakage"
```

---

## Self-Review

**Spec coverage:**
- ✅ Forward-looking realized_vol_24h label: Task 1
- ✅ Label leakage removed from features: Task 2
- ✅ Models retrained with corrected data: Task 3
- ✅ Tests cover both new method and leakage prevention: Tasks 1–2

**Placeholder scan:** None found.

**Type consistency:**
- `_get_forward_prices` returns `list[dict]` matching `_get_price_history` format — `_calculate_realized_vol` accepts both.
- `_align_data` returns same signature `(pd.DataFrame, pd.DataFrame)` — no callers need updating.

**Edge case checked:** rows with insufficient forward data (last 24h of training window) return `None` for `realized_vol_24h`, which `y_vol = y_labels[target].dropna()` in `ml_training_service.py:182` cleanly removes before training.
