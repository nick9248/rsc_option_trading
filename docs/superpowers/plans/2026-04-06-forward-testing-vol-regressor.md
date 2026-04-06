# Forward Testing — Vol Regressor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Forward Testing tab to the GUI where the user manually records volatility predictions and verifies them 24h later to build a calibration track record.

**Architecture:** New `ForwardTestingService` in the service/ml layer handles prediction storage and verification logic. A new `ForwardTestingTab` + `VolRegressorTile` in the GUI calls the service via QThread workers. A new DB migration adds the `vol_predictions` table.

**Tech Stack:** PostgreSQL (psycopg2), PySide6, numpy, scikit-learn (MLPredictor already trained), pytest + unittest.mock

---

## File Map

| File | Action |
|---|---|
| `migrations/012_add_vol_predictions.sql` | Create |
| `coding/core/database/repository.py` | Edit — add 5 methods |
| `coding/service/ml/forward_testing_service.py` | Create |
| `coding/gui/forward_testing/__init__.py` | Create (empty) |
| `coding/gui/forward_testing/forward_testing_tab.py` | Create |
| `coding/gui/forward_testing/vol_regressor_tile.py` | Create |
| `coding/gui/main_window.py` | Edit — wire index 9, update nav constants |
| `tests/unit/test_forward_testing_service.py` | Create |

---

## Task 1: DB Migration

**Files:**
- Create: `migrations/012_add_vol_predictions.sql`

- [ ] **Step 1: Write migration file**

```sql
-- Migration 012: Forward testing — vol predictions table

CREATE TABLE IF NOT EXISTS vol_predictions (
    id                   SERIAL PRIMARY KEY,
    predicted_at         TIMESTAMP NOT NULL,
    currency             VARCHAR(10) NOT NULL,
    model_id             VARCHAR(100) NOT NULL,
    predicted_vol_24h    DECIMAL(8,4) NOT NULL,
    predicted_daily_move DECIMAL(8,4) NOT NULL,
    verified_at          TIMESTAMP,
    actual_vol_24h       DECIMAL(8,4),
    actual_price_change  DECIMAL(8,4),
    within_1sigma        BOOLEAN,
    error_pct            DECIMAL(8,4),
    UNIQUE(predicted_at, currency)
);

CREATE INDEX IF NOT EXISTS idx_vol_predictions_currency ON vol_predictions(currency);
CREATE INDEX IF NOT EXISTS idx_vol_predictions_predicted_at ON vol_predictions(predicted_at DESC);
```

- [ ] **Step 2: Run migration on local DB**

```bash
cd C:/Users/Nick/PycharmProjects/option_trading
.venv/Scripts/python scripts/run_migration.py migrations/012_add_vol_predictions.sql
```

Expected: `Migration applied successfully` or `already exists`

- [ ] **Step 3: Verify table exists**

```bash
.venv/Scripts/python -c "
import psycopg2
conn = psycopg2.connect(host='localhost', port=5433, user='postgres', password='DB_PASSWORD_REDACTED', dbname='option_trading')
cur = conn.cursor()
cur.execute(\"SELECT column_name FROM information_schema.columns WHERE table_name='vol_predictions' ORDER BY ordinal_position\")
print([r[0] for r in cur.fetchall()])
conn.close()
"
```

Expected: `['id', 'predicted_at', 'currency', 'model_id', 'predicted_vol_24h', 'predicted_daily_move', 'verified_at', 'actual_vol_24h', 'actual_price_change', 'within_1sigma', 'error_pct']`

- [ ] **Step 4: Commit**

```bash
git add migrations/012_add_vol_predictions.sql
git commit -m "feat: add vol_predictions table for forward testing"
```

---

## Task 2: Repository Methods

**Files:**
- Modify: `coding/core/database/repository.py` — append 5 methods at end of class

- [ ] **Step 1: Add `get_hourly_prices` method**

Append to the `DatabaseRepository` class (after the last method, before end of class):

```python
def get_hourly_prices(
    self,
    currency: str,
    start_time: datetime,
    end_time: datetime
) -> List[Dict]:
    """
    Get hourly average index prices from historical_trades.

    Args:
        currency: Currency (BTC, ETH)
        start_time: Start of window (inclusive)
        end_time: End of window (inclusive)

    Returns:
        List of {'hour': datetime, 'price': float} sorted by hour ascending.
    """
    with self._db_cursor() as cursor:
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
            (currency, start_time, end_time)
        )
        rows = cursor.fetchall()

    return [{"hour": row[0], "price": float(row[1])} for row in rows]
```

- [ ] **Step 2: Add `save_vol_prediction` method**

```python
def save_vol_prediction(
    self,
    predicted_at: datetime,
    currency: str,
    model_id: str,
    predicted_vol_24h: float,
    predicted_daily_move: float
) -> int:
    """
    Insert or overwrite a vol prediction row.

    ON CONFLICT on (predicted_at, currency) resets verification columns
    so re-predicting the same hour starts fresh.

    Returns:
        The inserted/updated row id.
    """
    with self._db_cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO vol_predictions
                (predicted_at, currency, model_id, predicted_vol_24h, predicted_daily_move)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (predicted_at, currency) DO UPDATE SET
                model_id             = EXCLUDED.model_id,
                predicted_vol_24h    = EXCLUDED.predicted_vol_24h,
                predicted_daily_move = EXCLUDED.predicted_daily_move,
                verified_at          = NULL,
                actual_vol_24h       = NULL,
                actual_price_change  = NULL,
                within_1sigma        = NULL,
                error_pct            = NULL
            RETURNING id
            """,
            (predicted_at, currency, model_id, predicted_vol_24h, predicted_daily_move)
        )
        row = cursor.fetchone()

    return row[0]
```

- [ ] **Step 3: Add `get_latest_unverified_prediction` method**

```python
def get_latest_unverified_prediction(self, currency: str) -> Optional[Dict]:
    """
    Return the most recent unverified prediction for a currency, or None.

    Returns dict with keys: id, predicted_at, currency, model_id,
    predicted_vol_24h, predicted_daily_move.
    """
    with self._db_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, predicted_at, currency, model_id,
                   predicted_vol_24h, predicted_daily_move
            FROM vol_predictions
            WHERE currency = %s AND verified_at IS NULL
            ORDER BY predicted_at DESC
            LIMIT 1
            """,
            (currency,)
        )
        row = cursor.fetchone()

    if row is None:
        return None

    return {
        "id": row[0],
        "predicted_at": row[1],
        "currency": row[2],
        "model_id": row[3],
        "predicted_vol_24h": float(row[4]),
        "predicted_daily_move": float(row[5]),
    }
```

- [ ] **Step 4: Add `update_vol_prediction_verified` method**

```python
def update_vol_prediction_verified(
    self,
    prediction_id: int,
    actual_vol_24h: float,
    actual_price_change: float,
    within_1sigma: bool,
    error_pct: float
) -> None:
    """
    Write verification results into an existing prediction row.

    Args:
        prediction_id: Row id to update.
        actual_vol_24h: Actual annualized realized vol (%).
        actual_price_change: Absolute price change over the 24h window (%).
        within_1sigma: Whether actual_price_change <= predicted_daily_move.
        error_pct: predicted_vol_24h - actual_vol_24h (signed).
    """
    with self._db_cursor() as cursor:
        cursor.execute(
            """
            UPDATE vol_predictions
            SET verified_at        = NOW(),
                actual_vol_24h     = %s,
                actual_price_change = %s,
                within_1sigma      = %s,
                error_pct          = %s
            WHERE id = %s
            """,
            (actual_vol_24h, actual_price_change, within_1sigma, error_pct, prediction_id)
        )
```

- [ ] **Step 5: Add `get_vol_prediction_history` method**

```python
def get_vol_prediction_history(self, limit: int = 14) -> List[Dict]:
    """
    Return recent vol predictions (all currencies), newest first.

    Returns list of dicts with all columns. Unverified rows have
    actual_vol_24h, actual_price_change, within_1sigma, error_pct as None.
    """
    with self._db_cursor() as cursor:
        cursor.execute(
            """
            SELECT id, predicted_at, currency, model_id,
                   predicted_vol_24h, predicted_daily_move,
                   verified_at, actual_vol_24h, actual_price_change,
                   within_1sigma, error_pct
            FROM vol_predictions
            ORDER BY predicted_at DESC
            LIMIT %s
            """,
            (limit,)
        )
        rows = cursor.fetchall()

    return [
        {
            "id": r[0],
            "predicted_at": r[1],
            "currency": r[2],
            "model_id": r[3],
            "predicted_vol_24h": float(r[4]),
            "predicted_daily_move": float(r[5]),
            "verified_at": r[6],
            "actual_vol_24h": float(r[7]) if r[7] is not None else None,
            "actual_price_change": float(r[8]) if r[8] is not None else None,
            "within_1sigma": r[9],
            "error_pct": float(r[10]) if r[10] is not None else None,
        }
        for r in rows
    ]
```

- [ ] **Step 6: Commit**

```bash
git add coding/core/database/repository.py
git commit -m "feat: add vol_predictions repository methods"
```

---

## Task 3: ForwardTestingService + Unit Tests

**Files:**
- Create: `coding/service/ml/forward_testing_service.py`
- Create: `tests/unit/test_forward_testing_service.py`

- [ ] **Step 1: Write failing tests first**

Create `tests/unit/test_forward_testing_service.py`:

```python
"""Unit tests for ForwardTestingService."""
import math
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from coding.service.ml.forward_testing_service import ForwardTestingService


def _make_service():
    """Create service with mocked dependencies."""
    service = ForwardTestingService.__new__(ForwardTestingService)
    service.repository = MagicMock()
    service.predictor = MagicMock()
    return service


# ── make_prediction ──────────────────────────────────────────────────────────

def test_make_prediction_stores_row():
    service = _make_service()
    service.predictor.predict_volatility.return_value = {
        "predicted_vol_24h": 42.0,
        "model_id": "BTC_realized_vol_24h_20260406_v1",
    }
    service.repository.save_vol_prediction.return_value = 1

    result = service.make_prediction("BTC")

    service.repository.save_vol_prediction.assert_called_once()
    call_kwargs = service.repository.save_vol_prediction.call_args[1]
    assert call_kwargs["currency"] == "BTC"
    assert call_kwargs["predicted_vol_24h"] == 42.0
    assert abs(call_kwargs["predicted_daily_move"] - 42.0 / math.sqrt(365)) < 0.001


def test_make_prediction_returns_dict_with_expected_keys():
    service = _make_service()
    service.predictor.predict_volatility.return_value = {
        "predicted_vol_24h": 55.0,
        "model_id": "model_xyz",
    }
    service.repository.save_vol_prediction.return_value = 5

    result = service.make_prediction("ETH")

    assert result["currency"] == "ETH"
    assert result["predicted_vol_24h"] == 55.0
    assert "predicted_daily_move" in result
    assert result["row_id"] == 5


def test_make_prediction_returns_error_when_predictor_fails():
    service = _make_service()
    service.predictor.predict_volatility.return_value = {
        "error": "No trained model available"
    }

    result = service.make_prediction("BTC")

    assert "error" in result
    service.repository.save_vol_prediction.assert_not_called()


# ── verify_prediction ────────────────────────────────────────────────────────

def test_verify_prediction_returns_error_when_no_unverified():
    service = _make_service()
    service.repository.get_latest_unverified_prediction.return_value = None

    result = service.verify_prediction("BTC")

    assert "error" in result
    assert "no unverified" in result["error"].lower()


def test_verify_prediction_returns_error_when_insufficient_price_data():
    service = _make_service()
    service.repository.get_latest_unverified_prediction.return_value = {
        "id": 1,
        "predicted_at": datetime(2026, 4, 5, 12, 0),
        "currency": "BTC",
        "model_id": "m1",
        "predicted_vol_24h": 40.0,
        "predicted_daily_move": 40.0 / math.sqrt(365),
    }
    service.repository.get_hourly_prices.return_value = [
        {"hour": datetime(2026, 4, 5, 12), "price": 80000.0},
        {"hour": datetime(2026, 4, 5, 13), "price": 80100.0},
    ]  # Only 2 rows — below the 20-row minimum

    result = service.verify_prediction("BTC")

    assert "error" in result
    assert "insufficient" in result["error"].lower()


def test_verify_prediction_computes_vol_correctly():
    service = _make_service()
    predicted_daily_move = 40.0 / math.sqrt(365)
    service.repository.get_latest_unverified_prediction.return_value = {
        "id": 7,
        "predicted_at": datetime(2026, 4, 5, 0, 0),
        "currency": "BTC",
        "model_id": "m1",
        "predicted_vol_24h": 40.0,
        "predicted_daily_move": predicted_daily_move,
    }

    # Construct 25 hourly prices with known, small returns
    base_price = 80000.0
    prices = [
        {"hour": datetime(2026, 4, 5, h), "price": base_price * (1 + 0.001 * h)}
        for h in range(25)
    ]
    service.repository.get_hourly_prices.return_value = prices

    # Compute expected values manually
    price_array = np.array([p["price"] for p in prices])
    log_returns = np.diff(np.log(price_array))
    expected_vol = float(np.std(log_returns) * np.sqrt(24 * 365) * 100)
    expected_price_change = abs(price_array[-1] - price_array[0]) / price_array[0] * 100

    result = service.verify_prediction("BTC")

    assert abs(result["actual_vol_24h"] - expected_vol) < 0.001
    assert abs(result["actual_price_change"] - expected_price_change) < 0.001
    service.repository.update_vol_prediction_verified.assert_called_once_with(
        prediction_id=7,
        actual_vol_24h=pytest.approx(expected_vol, abs=0.001),
        actual_price_change=pytest.approx(expected_price_change, abs=0.001),
        within_1sigma=result["within_1sigma"],
        error_pct=pytest.approx(40.0 - expected_vol, abs=0.001),
    )


def test_verify_prediction_within_1sigma_true_when_small_move():
    service = _make_service()
    predicted_daily_move = 2.0  # 2% expected daily move
    service.repository.get_latest_unverified_prediction.return_value = {
        "id": 3,
        "predicted_at": datetime(2026, 4, 5, 0, 0),
        "currency": "BTC",
        "model_id": "m1",
        "predicted_vol_24h": 2.0 * math.sqrt(365),  # back-calculate annualized
        "predicted_daily_move": predicted_daily_move,
    }
    # Prices with tiny movement (~0.5% total change)
    prices = [
        {"hour": datetime(2026, 4, 5, h), "price": 80000.0 + h * 0.1}
        for h in range(25)
    ]
    service.repository.get_hourly_prices.return_value = prices

    result = service.verify_prediction("BTC")

    assert result["within_1sigma"] is True


# ── get_scorecard ────────────────────────────────────────────────────────────

def test_get_scorecard_returns_zeros_when_no_verified_rows():
    service = _make_service()
    service.repository.get_vol_prediction_history.return_value = []

    scorecard = service.get_scorecard()

    assert scorecard["n_verified"] == 0
    assert scorecard["hit_rate"] == 0.0
    assert scorecard["mean_error"] == 0.0
    assert scorecard["bias"] == 0.0


def test_get_scorecard_computes_hit_rate():
    service = _make_service()
    service.repository.get_vol_prediction_history.return_value = [
        {"verified_at": datetime(2026, 4, 4), "within_1sigma": True,  "error_pct": 2.0,  "actual_vol_24h": 38.0},
        {"verified_at": datetime(2026, 4, 3), "within_1sigma": False, "error_pct": -5.0, "actual_vol_24h": 45.0},
        {"verified_at": datetime(2026, 4, 2), "within_1sigma": True,  "error_pct": 1.0,  "actual_vol_24h": 39.0},
        {"verified_at": None, "within_1sigma": None, "error_pct": None, "actual_vol_24h": None},  # unverified
    ]

    scorecard = service.get_scorecard()

    assert scorecard["n_verified"] == 3
    assert abs(scorecard["hit_rate"] - 66.67) < 0.1   # 2/3
    assert abs(scorecard["mean_error"] - (2.0 + 5.0 + 1.0) / 3) < 0.001  # mean of abs values
    assert abs(scorecard["bias"] - (2.0 - 5.0 + 1.0) / 3) < 0.001        # signed mean
```

- [ ] **Step 2: Run tests — confirm they fail**

```bash
cd C:/Users/Nick/PycharmProjects/option_trading
.venv/Scripts/python -m pytest tests/unit/test_forward_testing_service.py -v 2>&1 | head -30
```

Expected: `ImportError` or `ModuleNotFoundError` — service doesn't exist yet.

- [ ] **Step 3: Create ForwardTestingService**

Create `coding/service/ml/forward_testing_service.py`:

```python
"""
Forward Testing Service.

Records ML model predictions and verifies them against actual outcomes.
Used to build a calibration track record for the volatility regressor.
"""

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import numpy as np

from coding.core.database.repository import DatabaseRepository
from coding.core.ml.inference.predictor import MLPredictor

logger = logging.getLogger(__name__)


class ForwardTestingService:
    """
    Manage forward testing predictions and verifications.

    Flow:
        1. make_prediction(currency) → store predicted vol to DB
        2. verify_prediction(currency) → 24h later, compute actual vol and close the loop
        3. get_history() / get_scorecard() → track record for display
    """

    def __init__(
        self,
        repository: Optional[DatabaseRepository] = None,
        predictor: Optional[MLPredictor] = None,
    ):
        self.repository = repository or DatabaseRepository()
        self.predictor = predictor or MLPredictor()
        logger.info("ForwardTestingService initialized")

    def make_prediction(self, currency: str) -> Dict:
        """
        Run the vol regressor and store the prediction.

        predicted_at is truncated to the current UTC hour so re-running
        within the same hour overwrites the previous prediction.

        Args:
            currency: "BTC" or "ETH"

        Returns:
            Dict with keys: currency, predicted_vol_24h, predicted_daily_move,
            predicted_at, model_id, row_id — or {"error": "..."} on failure.
        """
        result = self.predictor.predict_volatility(currency)

        if "error" in result:
            logger.error(f"Predictor failed for {currency}: {result['error']}")
            return {"error": result["error"]}

        predicted_vol_24h = result["predicted_vol_24h"]
        model_id = result["model_id"]
        predicted_daily_move = predicted_vol_24h / math.sqrt(365)

        # Truncate to current UTC hour for stable upsert key
        now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        # Store as naive timestamp (consistent with rest of DB)
        predicted_at = now.replace(tzinfo=None)

        row_id = self.repository.save_vol_prediction(
            predicted_at=predicted_at,
            currency=currency,
            model_id=model_id,
            predicted_vol_24h=predicted_vol_24h,
            predicted_daily_move=predicted_daily_move,
        )

        logger.info(
            f"Prediction stored for {currency}: vol={predicted_vol_24h:.2f}% "
            f"daily_move=±{predicted_daily_move:.2f}% (id={row_id})"
        )

        return {
            "currency": currency,
            "predicted_vol_24h": predicted_vol_24h,
            "predicted_daily_move": predicted_daily_move,
            "predicted_at": predicted_at,
            "model_id": model_id,
            "row_id": row_id,
        }

    def verify_prediction(self, currency: str) -> Dict:
        """
        Find the latest unverified prediction and compute actual realized vol.

        Fetches 24h of hourly prices from historical_trades starting at
        predicted_at and computes realized vol using the same formula as
        the label generator: std(log_returns) * sqrt(24 * 365) * 100.

        Requires at least 20 hourly price points in the window.

        Args:
            currency: "BTC" or "ETH"

        Returns:
            Dict with verification results — or {"error": "..."} on failure.
        """
        prediction = self.repository.get_latest_unverified_prediction(currency)

        if prediction is None:
            return {"error": f"No unverified prediction found for {currency}"}

        predicted_at = prediction["predicted_at"]
        window_end = predicted_at + timedelta(hours=24)

        prices = self.repository.get_hourly_prices(
            currency=currency,
            start_time=predicted_at,
            end_time=window_end,
        )

        if len(prices) < 20:
            return {
                "error": (
                    f"Insufficient price data for {currency}: "
                    f"need 20+ hourly points, got {len(prices)}. "
                    f"Window: {predicted_at} → {window_end}"
                )
            }

        price_array = np.array([p["price"] for p in prices])
        log_returns = np.diff(np.log(price_array))
        actual_vol_24h = float(np.std(log_returns) * np.sqrt(24 * 365) * 100)
        actual_price_change = float(
            abs(price_array[-1] - price_array[0]) / price_array[0] * 100
        )

        within_1sigma = actual_price_change <= prediction["predicted_daily_move"]
        error_pct = prediction["predicted_vol_24h"] - actual_vol_24h

        self.repository.update_vol_prediction_verified(
            prediction_id=prediction["id"],
            actual_vol_24h=actual_vol_24h,
            actual_price_change=actual_price_change,
            within_1sigma=within_1sigma,
            error_pct=error_pct,
        )

        logger.info(
            f"Verified {currency}: predicted={prediction['predicted_vol_24h']:.2f}% "
            f"actual={actual_vol_24h:.2f}% within_1sigma={within_1sigma}"
        )

        return {
            "currency": currency,
            "predicted_vol_24h": prediction["predicted_vol_24h"],
            "predicted_daily_move": prediction["predicted_daily_move"],
            "actual_vol_24h": actual_vol_24h,
            "actual_price_change": actual_price_change,
            "within_1sigma": within_1sigma,
            "error_pct": error_pct,
            "predicted_at": predicted_at,
        }

    def get_history(self, limit: int = 14) -> List[Dict]:
        """
        Return recent predictions (all currencies), newest first.

        Args:
            limit: Maximum rows to return.

        Returns:
            List of prediction dicts. Unverified rows have None for actual_* fields.
        """
        return self.repository.get_vol_prediction_history(limit=limit)

    def get_scorecard(self) -> Dict:
        """
        Compute calibration statistics across all verified predictions.

        Returns:
            Dict with: n_verified, hit_rate (%), mean_error (abs), bias (signed).
            All values are 0.0 if no verified predictions exist.
        """
        rows = self.repository.get_vol_prediction_history(limit=1000)
        verified = [r for r in rows if r["verified_at"] is not None]

        if not verified:
            return {"n_verified": 0, "hit_rate": 0.0, "mean_error": 0.0, "bias": 0.0}

        hit_count = sum(1 for r in verified if r["within_1sigma"])
        errors = [r["error_pct"] for r in verified if r["error_pct"] is not None]

        hit_rate = hit_count / len(verified) * 100
        mean_error = float(np.mean(np.abs(errors))) if errors else 0.0
        bias = float(np.mean(errors)) if errors else 0.0

        return {
            "n_verified": len(verified),
            "hit_rate": hit_rate,
            "mean_error": mean_error,
            "bias": bias,
        }
```

- [ ] **Step 4: Run tests — confirm they pass**

```bash
.venv/Scripts/python -m pytest tests/unit/test_forward_testing_service.py -v
```

Expected: All 8 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add coding/service/ml/forward_testing_service.py tests/unit/test_forward_testing_service.py
git commit -m "feat: add ForwardTestingService with unit tests"
```

---

## Task 4: GUI — VolRegressorTile + ForwardTestingTab

**Files:**
- Create: `coding/gui/forward_testing/__init__.py`
- Create: `coding/gui/forward_testing/vol_regressor_tile.py`
- Create: `coding/gui/forward_testing/forward_testing_tab.py`

- [ ] **Step 1: Create empty `__init__.py`**

```python
```

(Empty file.)

- [ ] **Step 2: Create `vol_regressor_tile.py`**

Create `coding/gui/forward_testing/vol_regressor_tile.py`:

```python
"""
Vol Regressor Forward Testing Tile.

Displays: scorecard (hit rate, mean error, bias, n tests) + history table
+ action buttons (Predict BTC/ETH, Verify BTC/ETH).
"""

import logging
from typing import Optional

from PySide6.QtWidgets import (
    QFrame,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
)
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont

from coding.gui.theme.colors import Colors
from coding.service.ml.forward_testing_service import ForwardTestingService

logger = logging.getLogger(__name__)


# ── Workers ───────────────────────────────────────────────────────────────────

class PredictWorker(QThread):
    """Run ForwardTestingService.make_prediction in a background thread."""

    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, currency: str, parent=None):
        super().__init__(parent)
        self.currency = currency

    def run(self) -> None:
        try:
            service = ForwardTestingService()
            result = service.make_prediction(self.currency)
            if "error" in result:
                self.error.emit(result["error"])
            else:
                self.finished.emit(result)
        except Exception as exc:
            logger.error(f"PredictWorker failed: {exc}", exc_info=True)
            self.error.emit(str(exc))


class VerifyWorker(QThread):
    """Run ForwardTestingService.verify_prediction in a background thread."""

    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, currency: str, parent=None):
        super().__init__(parent)
        self.currency = currency

    def run(self) -> None:
        try:
            service = ForwardTestingService()
            result = service.verify_prediction(self.currency)
            if "error" in result:
                self.error.emit(result["error"])
            else:
                self.finished.emit(result)
        except Exception as exc:
            logger.error(f"VerifyWorker failed: {exc}", exc_info=True)
            self.error.emit(str(exc))


# ── Tile ──────────────────────────────────────────────────────────────────────

class VolRegressorTile(QFrame):
    """
    Tile for Vol Regressor forward testing.

    Layout (top → bottom):
        Title bar
        Scorecard row (4 stats)
        History table (last 14 rows, all currencies)
        Button row + status label
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._workers: list = []  # keep workers alive while running
        self._init_ui()
        self._refresh_data()

    # ── UI init ───────────────────────────────────────────────────────────────

    def _init_ui(self) -> None:
        self.setFrameStyle(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            f"background-color: {Colors.SURFACE}; "
            f"border: 1px solid {Colors.BORDER}; "
            f"border-radius: 6px;"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        layout.addWidget(self._build_title())
        layout.addWidget(self._build_scorecard())
        layout.addWidget(self._build_table())
        layout.addWidget(self._build_buttons())

    def _build_title(self) -> QLabel:
        label = QLabel("Vol Regressor — 24h Realized Volatility")
        font = QFont()
        font.setPointSize(12)
        font.setBold(True)
        label.setFont(font)
        label.setStyleSheet(f"color: {Colors.ACCENT};")
        return label

    def _build_scorecard(self) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(
            f"background-color: {Colors.BACKGROUND_ELEVATED}; "
            f"border: 1px solid {Colors.BORDER}; "
            f"border-radius: 4px;"
        )

        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(0)

        self._stat_labels = {}
        stats = [
            ("hit_rate",    "Hit Rate",   "—"),
            ("mean_error",  "Mean Error", "—"),
            ("bias",        "Bias",       "—"),
            ("n_verified",  "N Tests",    "0"),
        ]

        for key, title, default in stats:
            col = QFrame()
            col_layout = QVBoxLayout(col)
            col_layout.setContentsMargins(0, 0, 0, 0)
            col_layout.setSpacing(2)
            col_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

            title_label = QLabel(title)
            title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            title_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 11px;")

            value_label = QLabel(default)
            value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            font = QFont()
            font.setPointSize(14)
            font.setBold(True)
            value_label.setFont(font)
            value_label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")

            col_layout.addWidget(title_label)
            col_layout.addWidget(value_label)

            self._stat_labels[key] = value_label
            layout.addWidget(col, stretch=1)

        return frame

    def _build_table(self) -> QTableWidget:
        self._table = QTableWidget()
        self._table.setColumnCount(6)
        self._table.setHorizontalHeaderLabels(
            ["Date", "CCY", "Pred Vol", "Daily ±1σ", "Actual", "Result"]
        )
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)

        header = self._table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        header.setStyleSheet(
            f"background-color: {Colors.BACKGROUND_ELEVATED}; "
            f"color: {Colors.TEXT_SECONDARY}; "
            f"font-weight: bold;"
        )
        self._table.setStyleSheet(
            f"background-color: {Colors.INPUT_BACKGROUND}; "
            f"color: {Colors.TEXT_PRIMARY}; "
            f"border: 1px solid {Colors.BORDER}; "
            f"gridline-color: {Colors.BORDER}; "
            f"alternate-background-color: {Colors.BACKGROUND_ELEVATED};"
        )
        self._table.setMinimumHeight(220)
        return self._table

    def _build_buttons(self) -> QFrame:
        frame = QFrame()
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        btn_style = (
            f"background-color: {Colors.BUTTON_PRIMARY}; "
            f"color: #080D18; "
            f"border: none; "
            f"padding: 7px 16px; "
            f"border-radius: 4px; "
            f"font-weight: bold;"
        )
        btn_secondary_style = (
            f"background-color: {Colors.BUTTON_SECONDARY}; "
            f"color: {Colors.TEXT_PRIMARY}; "
            f"border: 1px solid {Colors.BORDER}; "
            f"padding: 7px 16px; "
            f"border-radius: 4px;"
        )

        self._btn_predict_btc = QPushButton("Predict BTC")
        self._btn_predict_btc.setStyleSheet(btn_style)
        self._btn_predict_btc.clicked.connect(lambda: self._on_predict("BTC"))

        self._btn_predict_eth = QPushButton("Predict ETH")
        self._btn_predict_eth.setStyleSheet(btn_style)
        self._btn_predict_eth.clicked.connect(lambda: self._on_predict("ETH"))

        self._btn_verify_btc = QPushButton("Verify BTC")
        self._btn_verify_btc.setStyleSheet(btn_secondary_style)
        self._btn_verify_btc.clicked.connect(lambda: self._on_verify("BTC"))

        self._btn_verify_eth = QPushButton("Verify ETH")
        self._btn_verify_eth.setStyleSheet(btn_secondary_style)
        self._btn_verify_eth.clicked.connect(lambda: self._on_verify("ETH"))

        btn_row.addWidget(self._btn_predict_btc)
        btn_row.addWidget(self._btn_predict_eth)
        btn_row.addWidget(self._btn_verify_btc)
        btn_row.addWidget(self._btn_verify_eth)
        btn_row.addStretch()

        self._status_label = QLabel("Ready")
        self._status_label.setStyleSheet(f"color: {Colors.TEXT_SECONDARY}; font-size: 11px;")

        layout.addLayout(btn_row)
        layout.addWidget(self._status_label)
        return frame

    # ── Data loading ──────────────────────────────────────────────────────────

    def _refresh_data(self) -> None:
        """Reload scorecard and history from DB and update UI."""
        try:
            service = ForwardTestingService()
            scorecard = service.get_scorecard()
            history = service.get_history(limit=14)
            self._update_scorecard(scorecard)
            self._update_table(history)
        except Exception as exc:
            logger.error(f"Failed to refresh forward testing data: {exc}")
            self._set_status(f"Error loading data: {exc}", error=True)

    def _update_scorecard(self, scorecard: dict) -> None:
        n = scorecard["n_verified"]
        self._stat_labels["n_verified"].setText(str(n))

        if n == 0:
            self._stat_labels["hit_rate"].setText("—")
            self._stat_labels["mean_error"].setText("—")
            self._stat_labels["bias"].setText("—")
        else:
            self._stat_labels["hit_rate"].setText(f"{scorecard['hit_rate']:.1f}%")
            self._stat_labels["mean_error"].setText(f"{scorecard['mean_error']:.1f}%")
            bias = scorecard["bias"]
            sign = "+" if bias >= 0 else ""
            self._stat_labels["bias"].setText(f"{sign}{bias:.1f}%")

    def _update_table(self, rows: list) -> None:
        self._table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            date_str = row["predicted_at"].strftime("%b %d %H:%M") if row["predicted_at"] else "—"
            actual_str = f"{row['actual_vol_24h']:.1f}%" if row["actual_vol_24h"] is not None else "—"
            result_str = "PASS" if row["within_1sigma"] else ("FAIL" if row["within_1sigma"] is False else "pending")

            self._table.setItem(i, 0, QTableWidgetItem(date_str))
            self._table.setItem(i, 1, QTableWidgetItem(row["currency"]))
            self._table.setItem(i, 2, QTableWidgetItem(f"{row['predicted_vol_24h']:.1f}%"))
            self._table.setItem(i, 3, QTableWidgetItem(f"±{row['predicted_daily_move']:.2f}%"))
            self._table.setItem(i, 4, QTableWidgetItem(actual_str))

            result_item = QTableWidgetItem(result_str)
            if row["within_1sigma"] is True:
                result_item.setForeground(Qt.GlobalColor.green)
            elif row["within_1sigma"] is False:
                result_item.setForeground(Qt.GlobalColor.red)
            else:
                result_item.setForeground(Qt.GlobalColor.gray)
            self._table.setItem(i, 5, result_item)

    # ── Button handlers ───────────────────────────────────────────────────────

    def _on_predict(self, currency: str) -> None:
        self._set_all_buttons_enabled(False)
        self._set_status(f"Running vol regressor for {currency}... (~90s)")
        worker = PredictWorker(currency, parent=self)
        worker.finished.connect(self._on_predict_done)
        worker.error.connect(self._on_worker_error)
        worker.finished.connect(lambda _: self._workers.remove(worker))
        worker.error.connect(lambda _: self._workers.remove(worker))
        self._workers.append(worker)
        worker.start()

    def _on_verify(self, currency: str) -> None:
        self._set_all_buttons_enabled(False)
        self._set_status(f"Verifying latest {currency} prediction...")
        worker = VerifyWorker(currency, parent=self)
        worker.finished.connect(self._on_verify_done)
        worker.error.connect(self._on_worker_error)
        worker.finished.connect(lambda _: self._workers.remove(worker))
        worker.error.connect(lambda _: self._workers.remove(worker))
        self._workers.append(worker)
        worker.start()

    def _on_predict_done(self, result: dict) -> None:
        self._set_all_buttons_enabled(True)
        self._set_status(
            f"Predicted {result['currency']}: "
            f"{result['predicted_vol_24h']:.1f}% vol, "
            f"±{result['predicted_daily_move']:.2f}% daily move"
        )
        self._refresh_data()

    def _on_verify_done(self, result: dict) -> None:
        self._set_all_buttons_enabled(True)
        outcome = "PASS" if result["within_1sigma"] else "FAIL"
        self._set_status(
            f"Verified {result['currency']}: "
            f"predicted {result['predicted_vol_24h']:.1f}% vs actual {result['actual_vol_24h']:.1f}% "
            f"— {outcome}",
            error=(not result["within_1sigma"])
        )
        self._refresh_data()

    def _on_worker_error(self, message: str) -> None:
        self._set_all_buttons_enabled(True)
        self._set_status(f"Error: {message}", error=True)

    def _set_all_buttons_enabled(self, enabled: bool) -> None:
        for btn in (self._btn_predict_btc, self._btn_predict_eth,
                    self._btn_verify_btc, self._btn_verify_eth):
            btn.setEnabled(enabled)

    def _set_status(self, message: str, error: bool = False) -> None:
        color = Colors.ERROR if error else Colors.TEXT_SECONDARY
        self._status_label.setStyleSheet(f"color: {color}; font-size: 11px;")
        self._status_label.setText(message)
```

- [ ] **Step 3: Create `forward_testing_tab.py`**

Create `coding/gui/forward_testing/forward_testing_tab.py`:

```python
"""
Forward Testing Tab.

Container for all forward testing experiment tiles.
Add new tiles here as more experiments are built.
"""

import logging
from typing import Optional

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QScrollArea,
    QLabel,
)
from PySide6.QtGui import QFont

from coding.gui.theme.colors import Colors
from coding.gui.forward_testing.vol_regressor_tile import VolRegressorTile

logger = logging.getLogger(__name__)


class ForwardTestingTab(QWidget):
    """Tab hosting all forward testing tiles."""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 10)
        outer.setSpacing(10)

        # Header
        header = QLabel("Forward Testing")
        font = QFont()
        font.setPointSize(14)
        font.setBold(True)
        header.setFont(font)
        header.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        outer.addWidget(header)

        # Scrollable tile area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            f"background-color: {Colors.BACKGROUND_PRIMARY}; "
            f"border: none;"
        )

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)

        # Tile 1: Vol Regressor
        content_layout.addWidget(VolRegressorTile())
        content_layout.addStretch()

        scroll.setWidget(content)
        outer.addWidget(scroll, stretch=1)
```

- [ ] **Step 4: Commit**

```bash
git add coding/gui/forward_testing/
git commit -m "feat: add ForwardTestingTab and VolRegressorTile"
```

---

## Task 5: Wire Into MainWindow

**Files:**
- Modify: `coding/gui/main_window.py`

- [ ] **Step 1: Add import at top of file**

In `coding/gui/main_window.py`, add after the existing tab imports (after `from coding.gui.tabs.system_validation_tab import SystemValidationTab`):

```python
from coding.gui.forward_testing.forward_testing_tab import ForwardTestingTab
```

- [ ] **Step 2: Update MODULE_DEFS entry for index 9**

In `MODULE_DEFS`, change the index-9 entry from:
```python
{"index": 9,  "icon": "📈", "name": "Market Data",       "subtitle": "Coming soon"},
```
to:
```python
{"index": 9,  "icon": "🧪", "name": "Forward Testing",   "subtitle": "Vol · Regime · Strategy"},
```

- [ ] **Step 3: Update `_LAST_ACTIVE` and `_PLACEHOLDER_INDICES`**

Change:
```python
_LAST_ACTIVE = 8
_PLACEHOLDER_INDICES = {9, 10, 11}
```
to:
```python
_LAST_ACTIVE = 9
_PLACEHOLDER_INDICES = {10, 11}
```

- [ ] **Step 4: Replace placeholder at index 9 in `_build_stack`**

Change:
```python
        # Indices 9–11: Future placeholders
        self.stack.addWidget(self._placeholder_widget("Market data visualization coming soon…"))
        self.stack.addWidget(self._placeholder_widget("Trading interface coming soon…"))
        self.stack.addWidget(self._placeholder_widget("Analytics dashboard coming soon…"))
```
to:
```python
        # Index 9: Forward Testing
        try:
            self.stack.addWidget(ForwardTestingTab())
        except Exception as exc:
            logger.error("Failed to initialize Forward Testing tab: %s", exc)
            self.stack.addWidget(self._placeholder_widget("Forward testing unavailable"))
            failed_indices.add(9)

        # Indices 10–11: Future placeholders
        self.stack.addWidget(self._placeholder_widget("Trading interface coming soon…"))
        self.stack.addWidget(self._placeholder_widget("Analytics dashboard coming soon…"))
```

- [ ] **Step 5: Update `_sync_nav_state` position label**

The position label currently hardcodes `_LAST_ACTIVE`. Since `_LAST_ACTIVE` is a module-level constant it's already referenced correctly — no change needed. Verify the line reads:

```python
self.position_label.setText(f"{index} / {_LAST_ACTIVE}")
```

- [ ] **Step 6: Start the app and verify the tab loads**

```bash
cd C:/Users/Nick/PycharmProjects/option_trading
.venv/Scripts/python -m coding.gui.app
```

Expected:
- Navigation page shows "Forward Testing" tile at position 9
- Clicking the tile navigates to the tab
- Scorecard shows "—" / "0" (no data yet)
- Table is empty
- All 4 buttons are enabled

- [ ] **Step 7: Commit**

```bash
git add coding/gui/main_window.py
git commit -m "feat: wire ForwardTestingTab into main window at index 9"
```

---

## Self-Review Checklist

- [x] **Spec coverage:** DB table ✓, make_prediction ✓, verify_prediction ✓, get_history ✓, get_scorecard ✓, GUI tile layout ✓, scorecard + table + buttons ✓, workers ✓, navigation wiring ✓
- [x] **No placeholders:** All steps have complete code
- [x] **Type consistency:** `save_vol_prediction` uses keyword args matching exactly what `make_prediction` passes. `update_vol_prediction_verified` parameter names match between service and repository. `get_vol_prediction_history` returns list of dicts with consistent key names used in `_update_table` and `get_scorecard`.
- [x] **Edge cases covered:** Predictor failure, no unverified prediction, insufficient price data — all return `{"error": ...}` and tested
