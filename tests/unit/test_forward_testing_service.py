"""Unit tests for ForwardTestingService."""
import math
from datetime import datetime, timedelta, timezone
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
    base = datetime(2026, 4, 5, 0, 0)
    base_price = 80000.0
    prices = [
        {"hour": base + timedelta(hours=h), "price": base_price * (1 + 0.001 * h)}
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
        "predicted_vol_24h": 2.0 * math.sqrt(365),
        "predicted_daily_move": predicted_daily_move,
    }
    # Prices with tiny movement (~0.5% total change)
    base = datetime(2026, 4, 5, 0, 0)
    prices = [
        {"hour": base + timedelta(hours=h), "price": 80000.0 + h * 0.1}
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
    assert abs(scorecard["hit_rate"] - 66.67) < 0.1          # 2/3
    assert abs(scorecard["mean_error"] - (2.0 + 5.0 + 1.0) / 3) < 0.001  # mean of abs values
    assert abs(scorecard["bias"] - (2.0 - 5.0 + 1.0) / 3) < 0.001        # signed mean
