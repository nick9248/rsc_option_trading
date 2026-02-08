"""
ML Predictor.

Generate predictions from trained models for current market state.
"""

import logging
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Optional

from coding.core.ml.training.model_store import ModelStore
from coding.core.ml.data.data_loader import MLDataLoader

logger = logging.getLogger(__name__)


class MLPredictor:
    """Generate predictions from trained models."""

    def __init__(
        self,
        model_store: Optional[ModelStore] = None,
        data_loader: Optional[MLDataLoader] = None
    ):
        """
        Initialize ML predictor.

        Args:
            model_store: Model store instance.
            data_loader: Data loader instance.
        """
        self.model_store = model_store or ModelStore()
        self.data_loader = data_loader or MLDataLoader()

        logger.info("MLPredictor initialized")

    def predict_regime(
        self,
        currency: str,
        timestamp: Optional[datetime] = None
    ) -> Dict:
        """
        Predict market regime for currency.

        Args:
            currency: Currency symbol (BTC, ETH).
            timestamp: Prediction timestamp (default: now).

        Returns:
            Dictionary with:
            - regime: Predicted regime (bullish, bearish, etc.)
            - confidence: Model confidence (0-1)
            - probabilities: Class probabilities
            - model_id: Model ID used
            - timestamp: Prediction timestamp
        """
        timestamp = timestamp or datetime.now()

        try:
            # Load latest regime classifier for currency
            logger.info(f"Loading regime classifier for {currency}...")
            model, metadata = self.model_store.load(
                currency=currency,
                target="market_regime",
                latest=True
            )

            logger.info(f"  Loaded model: {metadata.model_id}")
            logger.info(f"  Trained: {metadata.created_at}")

            # Get current features
            logger.info("Extracting current features...")
            features = self._get_current_features(
                currency=currency,
                timestamp=timestamp,
                feature_names=metadata.feature_names
            )

            if features is None:
                return {
                    "error": "Could not extract features",
                    "timestamp": str(timestamp)
                }

            # Make prediction
            prediction = model.predict(features)[0]
            probabilities = model.predict_proba(features)[0]

            # Get class names
            classes = model.classes_

            # Find confidence (max probability)
            confidence = float(np.max(probabilities))

            # Create probabilities dict
            probs_dict = {str(cls): float(prob) for cls, prob in zip(classes, probabilities)}

            result = {
                "regime": str(prediction),
                "confidence": confidence,
                "probabilities": probs_dict,
                "model_id": metadata.model_id,
                "timestamp": str(timestamp)
            }

            logger.info(f"  Prediction: {prediction} (confidence: {confidence:.2f})")

            return result

        except FileNotFoundError as e:
            logger.error(f"No trained model found for {currency} market_regime: {e}")
            return {
                "error": "No trained model available",
                "timestamp": str(timestamp)
            }

        except Exception as e:
            logger.error(f"Prediction failed: {e}", exc_info=True)
            return {
                "error": str(e),
                "timestamp": str(timestamp)
            }

    def predict_volatility(
        self,
        currency: str,
        timestamp: Optional[datetime] = None
    ) -> Dict:
        """
        Predict realized volatility.

        Args:
            currency: Currency symbol (BTC, ETH).
            timestamp: Prediction timestamp (default: now).

        Returns:
            Dictionary with:
            - predicted_vol_24h: Predicted 24h realized vol (%)
            - model_id: Model ID used
            - timestamp: Prediction timestamp
        """
        timestamp = timestamp or datetime.now()

        try:
            # Load latest volatility regressor for currency
            logger.info(f"Loading volatility regressor for {currency}...")
            model, metadata = self.model_store.load(
                currency=currency,
                target="realized_vol_24h",
                latest=True
            )

            logger.info(f"  Loaded model: {metadata.model_id}")

            # Get current features
            logger.info("Extracting current features...")
            features = self._get_current_features(
                currency=currency,
                timestamp=timestamp,
                feature_names=metadata.feature_names
            )

            if features is None:
                return {
                    "error": "Could not extract features",
                    "timestamp": str(timestamp)
                }

            # Make prediction
            prediction = model.predict(features)[0]

            result = {
                "predicted_vol_24h": float(prediction),
                "model_id": metadata.model_id,
                "timestamp": str(timestamp)
            }

            logger.info(f"  Prediction: {prediction:.2f}% vol")

            return result

        except FileNotFoundError as e:
            logger.error(f"No trained model found for {currency} realized_vol_24h: {e}")
            return {
                "error": "No trained model available",
                "timestamp": str(timestamp)
            }

        except Exception as e:
            logger.error(f"Prediction failed: {e}", exc_info=True)
            return {
                "error": str(e),
                "timestamp": str(timestamp)
            }

    def _get_current_features(
        self,
        currency: str,
        timestamp: datetime,
        feature_names: list
    ) -> Optional[pd.DataFrame]:
        """
        Extract features for current timestamp.

        Uses a small lookback window to get recent data, then extracts
        features for the target timestamp.

        Args:
            currency: Currency symbol.
            timestamp: Target timestamp.
            feature_names: Required feature names.

        Returns:
            DataFrame with one row (current features) or None if failed.
        """
        try:
            # Load recent data (need lookback for rolling features)
            lookback_hours = 200  # Enough for 7-day features
            start_time = timestamp - timedelta(hours=lookback_hours)
            end_time = timestamp

            # Use data loader to get features
            # Note: We don't need labels for prediction
            from coding.core.ml.models.ml_config import MLTrainingConfig
            config = MLTrainingConfig()  # Default config

            features, _ = self.data_loader.load_training_data(
                currency=currency,
                start_time=start_time,
                end_time=end_time,
                config=config
            )

            if features.empty:
                logger.error("No features extracted")
                return None

            # Get the most recent row (closest to timestamp)
            latest_features = features.iloc[-1:]

            # Ensure all required features are present
            missing_features = set(feature_names) - set(latest_features.columns)
            if missing_features:
                logger.warning(f"Missing features: {missing_features}")

                # Add missing features as NaN
                for feat in missing_features:
                    latest_features[feat] = np.nan

            # Select only required features in correct order
            latest_features = latest_features[feature_names]

            return latest_features

        except Exception as e:
            logger.error(f"Failed to extract features: {e}", exc_info=True)
            return None
