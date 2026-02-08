"""
ML Model Trainer.

Trains LightGBM models for regime classification and volatility regression.
"""

import logging
import pandas as pd
import lightgbm as lgb
from typing import Optional, Union, List

from coding.core.ml.models.ml_config import MLTrainingConfig

logger = logging.getLogger(__name__)


class MLModelTrainer:
    """Trains LightGBM models for regime classification and vol regression."""

    def __init__(self, config: MLTrainingConfig):
        """
        Initialize model trainer.

        Args:
            config: ML training configuration.
        """
        self.config = config
        logger.info("MLModelTrainer initialized")

    def train_classifier(
        self,
        X_train: pd.DataFrame,
        y_train: pd.Series,
        X_val: Optional[pd.DataFrame] = None,
        y_val: Optional[pd.Series] = None
    ) -> lgb.LGBMClassifier:
        """
        Train regime classification model.

        Args:
            X_train: Training features.
            y_train: Training labels (regime classes).
            X_val: Validation features (optional).
            y_val: Validation labels (optional).

        Returns:
            Trained LGBMClassifier.
        """
        logger.info("Training classification model...")
        logger.info(f"  Training samples: {len(X_train)}")
        logger.info(f"  Features: {len(X_train.columns)}")
        logger.info(f"  Classes: {y_train.nunique()}")

        # Convert Pydantic config to dict for LightGBM
        params = self.config.classifier_params.model_dump()

        # Create model
        model = lgb.LGBMClassifier(**params)

        # Prepare validation set if provided
        eval_set = None
        if X_val is not None and y_val is not None:
            eval_set = [(X_val, y_val)]
            logger.info(f"  Validation samples: {len(X_val)}")

        # Train
        model.fit(
            X_train,
            y_train,
            eval_set=eval_set,
            callbacks=[lgb.early_stopping(stopping_rounds=50, verbose=False)] if eval_set else None
        )

        logger.info("  Training complete")
        logger.info(f"  Best iteration: {model.best_iteration_ if hasattr(model, 'best_iteration_') else 'N/A'}")

        return model

    def train_regressor(
        self,
        X_train: pd.DataFrame,
        y_train: pd.Series,
        X_val: Optional[pd.DataFrame] = None,
        y_val: Optional[pd.Series] = None
    ) -> lgb.LGBMRegressor:
        """
        Train volatility regression model.

        Args:
            X_train: Training features.
            y_train: Training labels (realized vol values).
            X_val: Validation features (optional).
            y_val: Validation labels (optional).

        Returns:
            Trained LGBMRegressor.
        """
        logger.info("Training regression model...")
        logger.info(f"  Training samples: {len(X_train)}")
        logger.info(f"  Features: {len(X_train.columns)}")

        # Convert Pydantic config to dict for LightGBM
        params = self.config.regressor_params.model_dump()

        # Create model
        model = lgb.LGBMRegressor(**params)

        # Prepare validation set if provided
        eval_set = None
        if X_val is not None and y_val is not None:
            eval_set = [(X_val, y_val)]
            logger.info(f"  Validation samples: {len(X_val)}")

        # Train
        model.fit(
            X_train,
            y_train,
            eval_set=eval_set,
            callbacks=[lgb.early_stopping(stopping_rounds=50, verbose=False)] if eval_set else None
        )

        logger.info("  Training complete")
        logger.info(f"  Best iteration: {model.best_iteration_ if hasattr(model, 'best_iteration_') else 'N/A'}")

        return model

    def get_feature_importance(
        self,
        model: Union[lgb.LGBMClassifier, lgb.LGBMRegressor],
        feature_names: List[str]
    ) -> pd.DataFrame:
        """
        Get sorted feature importance (gain-based).

        Args:
            model: Trained LightGBM model.
            feature_names: List of feature names.

        Returns:
            DataFrame with columns ['feature', 'importance'] sorted by importance.
        """
        importances = model.feature_importances_

        df = pd.DataFrame({
            'feature': feature_names,
            'importance': importances
        })

        df = df.sort_values('importance', ascending=False).reset_index(drop=True)

        return df
