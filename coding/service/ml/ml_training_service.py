"""
ML Training Service.

Orchestrates the full ML training pipeline:
1. Load data from DB
2. Walk-forward validate
3. Train final models on all data
4. Save models
5. Return metrics
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Optional

from coding.core.database.repository import DatabaseRepository
from coding.core.ml.models.ml_config import MLTrainingConfig
from coding.core.ml.data.data_loader import MLDataLoader
from coding.core.ml.training.model_trainer import MLModelTrainer
from coding.core.ml.training.walk_forward import WalkForwardValidator
from coding.core.ml.training.model_store import ModelStore, ModelMetadata
from coding.core.ml.inference.predictor import MLPredictor

logger = logging.getLogger(__name__)


class MLTrainingService:
    """
    Orchestrates the full ML training pipeline.

    Service layer - ties core components together.
    """

    def __init__(
        self,
        repository: Optional[DatabaseRepository] = None,
        config: Optional[MLTrainingConfig] = None
    ):
        """
        Initialize ML training service.

        Args:
            repository: Database repository instance.
            config: ML training configuration.
        """
        self.repo = repository or DatabaseRepository()
        self.config = config or MLTrainingConfig()

        self.data_loader = MLDataLoader(repository=self.repo)
        self.model_trainer = MLModelTrainer(config=self.config)
        self.walk_forward_validator = WalkForwardValidator(config=self.config.walk_forward)
        self.model_store = ModelStore(model_dir=self.config.model_dir)
        self.predictor = MLPredictor(model_store=self.model_store, data_loader=self.data_loader)

        logger.info("MLTrainingService initialized")

    def train_models(
        self,
        currency: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> Dict:
        """
        Full training pipeline:
        1. Load data from DB
        2. Walk-forward validate
        3. Train final models on all data
        4. Save models
        5. Return metrics

        Args:
            currency: Currency symbol (BTC, ETH).
            start_time: Start of training period (default: 60 days ago).
            end_time: End of training period (default: now).

        Returns:
            Dictionary with training results and metrics.
        """
        logger.info("="*60)
        logger.info(f"Starting ML training pipeline for {currency}")
        logger.info("="*60)

        # Default time range
        end_time = end_time or datetime.now()
        start_time = start_time or (end_time - timedelta(days=60))

        logger.info(f"Training period: {start_time} to {end_time}")

        result = {
            "currency": currency,
            "start_time": str(start_time),
            "end_time": str(end_time),
            "classifier": {},
            "regressor": {}
        }

        try:
            # 1. Load data
            logger.info("\n[1/5] Loading training data...")
            X, y_labels = self.data_loader.load_training_data(
                currency=currency,
                start_time=start_time,
                end_time=end_time,
                config=self.config
            )

            if X.empty or y_labels.empty:
                logger.error("No data loaded!")
                result["error"] = "No data available for training"
                return result

            logger.info(f"  Loaded {len(X)} samples with {len(X.columns)} features")

            # Check minimum samples
            if len(X) < self.config.min_samples:
                logger.error(f"Not enough samples: {len(X)} < {self.config.min_samples}")
                result["error"] = f"Insufficient data: {len(X)} samples"
                return result

            # 2. Train classifier (market regime)
            if self.config.classification_target in y_labels.columns:
                logger.info(f"\n[2/5] Training classifier ({self.config.classification_target})...")

                y_regime = y_labels[self.config.classification_target].dropna()

                # Align X and y
                X_regime = X.loc[y_regime.index]

                logger.info(f"  Samples: {len(X_regime)}")
                logger.info(f"  Classes: {y_regime.value_counts().to_dict()}")

                # Walk-forward validation
                logger.info("  Running walk-forward validation...")
                validation_result = self.walk_forward_validator.validate(
                    trainer=self.model_trainer,
                    X=X_regime,
                    y=y_regime,
                    task="classification"
                )

                logger.info(f"  Validation complete: {validation_result.n_folds} folds")

                # Train final model on all data
                logger.info("  Training final model on all data...")
                final_classifier = self.model_trainer.train_classifier(X_regime, y_regime)

                # Save model
                classifier_metadata = ModelMetadata(
                    model_type="classifier",
                    target=self.config.classification_target,
                    currency=currency,
                    training_start=start_time,
                    training_end=end_time,
                    n_samples=len(X_regime),
                    feature_names=X_regime.columns.tolist(),
                    walk_forward_metrics=validation_result.aggregate_metrics,
                    lightgbm_params=self.config.classifier_params.model_dump()
                )

                model_path = self.model_store.save(final_classifier, classifier_metadata)

                result["classifier"] = {
                    "model_id": classifier_metadata.model_id,
                    "model_path": str(model_path),
                    "n_samples": len(X_regime),
                    "n_features": len(X_regime.columns),
                    "validation_metrics": validation_result.aggregate_metrics,
                    "top_features": validation_result.feature_importance
                }

                logger.info(f"  Classifier saved: {classifier_metadata.model_id}")

            # 3. Train regressor (volatility)
            if self.config.regression_targets:
                for target in self.config.regression_targets:
                    if target not in y_labels.columns:
                        logger.warning(f"Target {target} not found in labels")
                        continue

                    logger.info(f"\n[3/5] Training regressor ({target})...")

                    y_vol = y_labels[target].dropna()

                    # Align X and y
                    X_vol = X.loc[y_vol.index]

                    logger.info(f"  Samples: {len(X_vol)}")
                    logger.info(f"  Target range: {y_vol.min():.2f} - {y_vol.max():.2f}")

                    # Walk-forward validation
                    logger.info("  Running walk-forward validation...")
                    validation_result = self.walk_forward_validator.validate(
                        trainer=self.model_trainer,
                        X=X_vol,
                        y=y_vol,
                        task="regression"
                    )

                    logger.info(f"  Validation complete: {validation_result.n_folds} folds")

                    # Train final model on all data
                    logger.info("  Training final model on all data...")
                    final_regressor = self.model_trainer.train_regressor(X_vol, y_vol)

                    # Save model
                    regressor_metadata = ModelMetadata(
                        model_type="regressor",
                        target=target,
                        currency=currency,
                        training_start=start_time,
                        training_end=end_time,
                        n_samples=len(X_vol),
                        feature_names=X_vol.columns.tolist(),
                        walk_forward_metrics=validation_result.aggregate_metrics,
                        lightgbm_params=self.config.regressor_params.model_dump()
                    )

                    model_path = self.model_store.save(final_regressor, regressor_metadata)

                    result["regressor"][target] = {
                        "model_id": regressor_metadata.model_id,
                        "model_path": str(model_path),
                        "n_samples": len(X_vol),
                        "n_features": len(X_vol.columns),
                        "validation_metrics": validation_result.aggregate_metrics,
                        "top_features": validation_result.feature_importance
                    }

                    logger.info(f"  Regressor saved: {regressor_metadata.model_id}")

            logger.info("\n" + "="*60)
            logger.info("ML training pipeline complete!")
            logger.info("="*60)

            return result

        except Exception as e:
            logger.error(f"Training pipeline failed: {e}", exc_info=True)
            result["error"] = str(e)
            return result

    def evaluate_models(
        self,
        currency: str
    ) -> Dict:
        """
        Load latest models and return their walk-forward metrics.

        Args:
            currency: Currency symbol.

        Returns:
            Dictionary with model metadata and metrics.
        """
        result = {
            "currency": currency,
            "classifier": None,
            "regressor": {}
        }

        try:
            # Load classifier
            try:
                _, classifier_metadata = self.model_store.load(
                    currency=currency,
                    target="market_regime",
                    latest=True
                )

                result["classifier"] = {
                    "model_id": classifier_metadata.model_id,
                    "created_at": str(classifier_metadata.created_at),
                    "n_samples": classifier_metadata.n_samples,
                    "metrics": classifier_metadata.walk_forward_metrics
                }

            except FileNotFoundError:
                logger.warning(f"No classifier found for {currency}")

            # Load regressors
            for target in self.config.regression_targets:
                try:
                    _, regressor_metadata = self.model_store.load(
                        currency=currency,
                        target=target,
                        latest=True
                    )

                    result["regressor"][target] = {
                        "model_id": regressor_metadata.model_id,
                        "created_at": str(regressor_metadata.created_at),
                        "n_samples": regressor_metadata.n_samples,
                        "metrics": regressor_metadata.walk_forward_metrics
                    }

                except FileNotFoundError:
                    logger.warning(f"No regressor found for {currency} {target}")

            return result

        except Exception as e:
            logger.error(f"Evaluation failed: {e}", exc_info=True)
            result["error"] = str(e)
            return result

    def predict(
        self,
        currency: str
    ) -> Dict:
        """
        Get current regime + vol prediction.

        This is what the strategy system calls.

        Args:
            currency: Currency symbol.

        Returns:
            Dictionary with predictions.
        """
        try:
            regime_pred = self.predictor.predict_regime(currency)
            vol_pred = self.predictor.predict_volatility(currency)

            return {
                "currency": currency,
                "regime": regime_pred,
                "volatility": vol_pred
            }

        except Exception as e:
            logger.error(f"Prediction failed: {e}", exc_info=True)
            return {
                "currency": currency,
                "error": str(e)
            }
