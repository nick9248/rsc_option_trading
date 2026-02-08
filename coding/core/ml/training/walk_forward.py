"""
Walk-forward validation for time-series ML.

NO random splitting - always train on past, test on future.
Expanding window: training set grows each fold.
"""

import logging
import numpy as np
import pandas as pd
from typing import List, Tuple, Dict, Any
from sklearn.metrics import (
    accuracy_score, f1_score, confusion_matrix,
    mean_absolute_error, mean_squared_error, r2_score,
    classification_report
)

from coding.core.ml.models.ml_config import WalkForwardConfig
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class WalkForwardResult(BaseModel):
    """Results from walk-forward validation."""
    task: str  # "classification" or "regression"
    n_folds: int
    fold_metrics: List[Dict[str, Any]]  # Per-fold metrics
    aggregate_metrics: Dict[str, float]  # Mean metrics across folds
    feature_importance: Dict[str, float]  # Average feature importance
    timestamps: Dict[str, str]  # First/last timestamps used

    class Config:
        arbitrary_types_allowed = True


class WalkForwardValidator:
    """
    Time-series aware validation with expanding window.

    Example with min_train=720h, test=168h, step=168h:
    Fold 1: Train [0..720h]  -> Test [720..888h]
    Fold 2: Train [0..888h]  -> Test [888..1056h]
    Fold 3: Train [0..1056h] -> Test [1056..1224h]
    ...

    NO random splitting - respects temporal order.
    NO data leakage - training uses only past data.
    """

    def __init__(self, config: WalkForwardConfig):
        """
        Initialize walk-forward validator.

        Args:
            config: Walk-forward configuration.
        """
        self.config = config
        logger.info(f"WalkForwardValidator initialized: {config}")

    def generate_splits(
        self,
        timestamps: pd.DatetimeIndex
    ) -> List[Tuple[np.ndarray, np.ndarray]]:
        """
        Generate (train_indices, test_indices) for each fold.

        Args:
            timestamps: Sorted datetime index from data.

        Returns:
            List of (train_idx, test_idx) tuples.
        """
        if not timestamps.is_monotonic_increasing:
            raise ValueError("Timestamps must be sorted!")

        n_samples = len(timestamps)
        min_train = self.config.min_train_hours
        test_size = self.config.test_hours
        step = self.config.step_hours

        # Convert hours to number of samples (assuming hourly data)
        # This assumes timestamps are hourly - adjust if different frequency
        time_diffs = timestamps[1:] - timestamps[:-1]
        median_diff = time_diffs.median()
        samples_per_hour = pd.Timedelta(hours=1) / median_diff

        min_train_samples = int(min_train * samples_per_hour)
        test_samples = int(test_size * samples_per_hour)
        step_samples = int(step * samples_per_hour)

        logger.info(f"Walk-forward split generation:")
        logger.info(f"  Total samples: {n_samples}")
        logger.info(f"  Min train samples: {min_train_samples}")
        logger.info(f"  Test samples: {test_samples}")
        logger.info(f"  Step samples: {step_samples}")

        splits = []
        current_train_end = min_train_samples

        while current_train_end + test_samples <= n_samples:
            # Training set: from start to current_train_end (expanding window)
            if self.config.expanding:
                train_idx = np.arange(0, current_train_end)
            else:
                # Sliding window (fixed size)
                train_start = max(0, current_train_end - min_train_samples)
                train_idx = np.arange(train_start, current_train_end)

            # Test set: from current_train_end to current_train_end + test_samples
            test_idx = np.arange(current_train_end, current_train_end + test_samples)

            splits.append((train_idx, test_idx))

            # Advance by step
            current_train_end += step_samples

        logger.info(f"  Generated {len(splits)} folds")

        if len(splits) < self.config.min_folds:
            raise ValueError(
                f"Not enough data for {self.config.min_folds} folds. "
                f"Only {len(splits)} folds possible. "
                f"Try reducing min_train_hours or step_hours."
            )

        # Log fold details
        for i, (train_idx, test_idx) in enumerate(splits):
            train_start = timestamps[train_idx[0]]
            train_end = timestamps[train_idx[-1]]
            test_start = timestamps[test_idx[0]]
            test_end = timestamps[test_idx[-1]]

            logger.info(
                f"  Fold {i+1}: Train [{train_start} .. {train_end}] "
                f"({len(train_idx)} samples) -> "
                f"Test [{test_start} .. {test_end}] ({len(test_idx)} samples)"
            )

        return splits

    def validate(
        self,
        trainer: Any,  # MLModelTrainer instance
        X: pd.DataFrame,
        y: pd.Series,
        task: str = "classification"
    ) -> WalkForwardResult:
        """
        Run walk-forward validation.

        Args:
            trainer: MLModelTrainer instance with train_classifier/train_regressor methods.
            X: Feature matrix (DataFrame with DatetimeIndex).
            y: Target vector (Series with DatetimeIndex).
            task: "classification" or "regression".

        Returns:
            WalkForwardResult with per-fold and aggregate metrics.
        """
        logger.info(f"Starting walk-forward validation ({task})...")

        # Generate splits
        splits = self.generate_splits(X.index)

        fold_metrics = []
        all_feature_importances = []

        for fold_num, (train_idx, test_idx) in enumerate(splits, 1):
            logger.info(f"\nFold {fold_num}/{len(splits)}...")

            # Split data
            X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
            y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]

            logger.info(f"  Train: {len(X_train)} samples")
            logger.info(f"  Test: {len(X_test)} samples")

            # Train model
            if task == "classification":
                model = trainer.train_classifier(X_train, y_train)
                predictions = model.predict(X_test)
                probabilities = model.predict_proba(X_test) if hasattr(model, 'predict_proba') else None

                # Classification metrics
                metrics = {
                    'fold': fold_num,
                    'accuracy': accuracy_score(y_test, predictions),
                    'f1_weighted': f1_score(y_test, predictions, average='weighted', zero_division=0),
                    'confusion_matrix': confusion_matrix(y_test, predictions).tolist(),
                    'train_samples': len(X_train),
                    'test_samples': len(X_test)
                }

                # Per-class metrics
                try:
                    report = classification_report(y_test, predictions, output_dict=True, zero_division=0)
                    metrics['classification_report'] = report
                except Exception as e:
                    logger.warning(f"  Failed to generate classification report: {e}")

                logger.info(f"  Accuracy: {metrics['accuracy']:.4f}")
                logger.info(f"  F1 (weighted): {metrics['f1_weighted']:.4f}")

            else:  # regression
                model = trainer.train_regressor(X_train, y_train)
                predictions = model.predict(X_test)

                # Regression metrics
                mae = mean_absolute_error(y_test, predictions)
                rmse = np.sqrt(mean_squared_error(y_test, predictions))
                r2 = r2_score(y_test, predictions)

                # Directional accuracy (for volatility: did we predict up/down correctly?)
                y_test_diff = y_test.diff().dropna()
                pred_diff = pd.Series(predictions, index=y_test.index).diff().dropna()
                directional_accuracy = (
                    (y_test_diff > 0) == (pred_diff > 0)
                ).mean() if len(y_test_diff) > 0 else 0

                metrics = {
                    'fold': fold_num,
                    'mae': mae,
                    'rmse': rmse,
                    'r2': r2,
                    'directional_accuracy': directional_accuracy,
                    'train_samples': len(X_train),
                    'test_samples': len(X_test)
                }

                logger.info(f"  MAE: {mae:.4f}")
                logger.info(f"  RMSE: {rmse:.4f}")
                logger.info(f"  R²: {r2:.4f}")
                logger.info(f"  Directional Acc: {directional_accuracy:.4f}")

            # Feature importance
            feature_importance = trainer.get_feature_importance(model, X.columns.tolist())
            all_feature_importances.append(feature_importance)

            fold_metrics.append(metrics)

        # Aggregate metrics across folds
        logger.info("\nAggregating metrics across folds...")

        if task == "classification":
            aggregate = {
                'mean_accuracy': np.mean([m['accuracy'] for m in fold_metrics]),
                'std_accuracy': np.std([m['accuracy'] for m in fold_metrics]),
                'mean_f1_weighted': np.mean([m['f1_weighted'] for m in fold_metrics]),
                'std_f1_weighted': np.std([m['f1_weighted'] for m in fold_metrics])
            }
            logger.info(f"  Mean Accuracy: {aggregate['mean_accuracy']:.4f} ± {aggregate['std_accuracy']:.4f}")
            logger.info(f"  Mean F1: {aggregate['mean_f1_weighted']:.4f} ± {aggregate['std_f1_weighted']:.4f}")
        else:
            aggregate = {
                'mean_mae': np.mean([m['mae'] for m in fold_metrics]),
                'std_mae': np.std([m['mae'] for m in fold_metrics]),
                'mean_rmse': np.mean([m['rmse'] for m in fold_metrics]),
                'std_rmse': np.std([m['rmse'] for m in fold_metrics]),
                'mean_r2': np.mean([m['r2'] for m in fold_metrics]),
                'std_r2': np.std([m['r2'] for m in fold_metrics]),
                'mean_directional_accuracy': np.mean([m['directional_accuracy'] for m in fold_metrics])
            }
            logger.info(f"  Mean MAE: {aggregate['mean_mae']:.4f} ± {aggregate['std_mae']:.4f}")
            logger.info(f"  Mean RMSE: {aggregate['mean_rmse']:.4f} ± {aggregate['std_rmse']:.4f}")
            logger.info(f"  Mean R²: {aggregate['mean_r2']:.4f} ± {aggregate['std_r2']:.4f}")

        # Average feature importance
        if all_feature_importances:
            # Concatenate all importance DataFrames
            importance_df = pd.concat(all_feature_importances)
            avg_importance = importance_df.groupby('feature')['importance'].mean().to_dict()
        else:
            avg_importance = {}

        # Timestamps
        timestamps_info = {
            'first': str(X.index[0]),
            'last': str(X.index[-1]),
            'total_samples': str(len(X))
        }

        result = WalkForwardResult(
            task=task,
            n_folds=len(splits),
            fold_metrics=fold_metrics,
            aggregate_metrics=aggregate,
            feature_importance=avg_importance,
            timestamps=timestamps_info
        )

        logger.info(f"\nWalk-forward validation complete: {len(splits)} folds")

        return result
