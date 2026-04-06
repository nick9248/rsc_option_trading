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
            f"daily_move=+-{predicted_daily_move:.2f}% (id={row_id})"
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
                    f"Window: {predicted_at} to {window_end}"
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
