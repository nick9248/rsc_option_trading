"""
Train ML models for market regime prediction and volatility forecasting.

Usage:
    python -m scripts.train_ml_model --currency BTC
    python -m scripts.train_ml_model --currency ETH --start 2025-01-01
    python -m scripts.train_ml_model --currency BTC --evaluate-only
"""

import argparse
import logging
from datetime import datetime

from coding.core.logging.logging_setup import init_logging
from coding.service.ml.ml_training_service import MLTrainingService
from coding.core.ml.models.ml_config import MLTrainingConfig

logger = logging.getLogger(__name__)


def main():
    """Main entry point for ML model training."""
    parser = argparse.ArgumentParser(
        description="Train ML models for market regime and volatility prediction"
    )

    parser.add_argument(
        "--currency",
        type=str,
        required=True,
        choices=["BTC", "ETH"],
        help="Currency to train models for"
    )

    parser.add_argument(
        "--start",
        type=str,
        help="Training start date (YYYY-MM-DD), default: 60 days ago"
    )

    parser.add_argument(
        "--end",
        type=str,
        help="Training end date (YYYY-MM-DD), default: now"
    )

    parser.add_argument(
        "--evaluate-only",
        action="store_true",
        help="Only evaluate existing models, don't train new ones"
    )

    parser.add_argument(
        "--min-train-hours",
        type=int,
        help="Minimum training hours for walk-forward validation"
    )

    parser.add_argument(
        "--test-hours",
        type=int,
        help="Test hours per fold"
    )

    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level"
    )

    args = parser.parse_args()

    # Initialize logging
    init_logging(level=args.log_level)

    logger.info("="*60)
    logger.info("ML Model Training Script")
    logger.info("="*60)

    # Parse dates
    start_time = None
    end_time = None

    if args.start:
        try:
            start_time = datetime.strptime(args.start, "%Y-%m-%d")
        except ValueError:
            logger.error(f"Invalid start date format: {args.start}. Use YYYY-MM-DD")
            return 1

    if args.end:
        try:
            end_time = datetime.strptime(args.end, "%Y-%m-%d")
        except ValueError:
            logger.error(f"Invalid end date format: {args.end}. Use YYYY-MM-DD")
            return 1

    # Create config
    config = MLTrainingConfig()

    # Override walk-forward settings if provided
    if args.min_train_hours or args.test_hours:
        from coding.core.ml.models.ml_config import WalkForwardConfig

        wf_config = WalkForwardConfig(
            min_train_hours=args.min_train_hours or config.walk_forward.min_train_hours,
            test_hours=args.test_hours or config.walk_forward.test_hours,
            step_hours=args.test_hours or config.walk_forward.step_hours,  # Use test_hours for step
            expanding=True,
            min_folds=3
        )

        # Recreate config with new walk-forward settings
        config = MLTrainingConfig(
            classification_target=config.classification_target,
            regression_targets=config.regression_targets,
            feature_categories=config.feature_categories,
            walk_forward=wf_config
        )

    # Create training service
    service = MLTrainingService(config=config)

    try:
        if args.evaluate_only:
            # Evaluate existing models
            logger.info(f"\nEvaluating existing models for {args.currency}...")

            result = service.evaluate_models(currency=args.currency)

            if "error" in result:
                logger.error(f"Evaluation failed: {result['error']}")
                return 1

            # Display results
            logger.info("\n" + "="*60)
            logger.info("Model Evaluation Results")
            logger.info("="*60)

            if result.get("classifier"):
                clf = result["classifier"]
                logger.info("\nClassifier (Market Regime):")
                logger.info(f"  Model ID: {clf['model_id']}")
                logger.info(f"  Created: {clf['created_at']}")
                logger.info(f"  Samples: {clf['n_samples']}")
                logger.info(f"  Metrics:")
                for metric, value in clf['metrics'].items():
                    logger.info(f"    {metric}: {value:.4f}")

            if result.get("regressor"):
                for target, reg in result["regressor"].items():
                    logger.info(f"\nRegressor ({target}):")
                    logger.info(f"  Model ID: {reg['model_id']}")
                    logger.info(f"  Created: {reg['created_at']}")
                    logger.info(f"  Samples: {reg['n_samples']}")
                    logger.info(f"  Metrics:")
                    for metric, value in reg['metrics'].items():
                        logger.info(f"    {metric}: {value:.4f}")

        else:
            # Train new models
            logger.info(f"\nTraining models for {args.currency}...")

            result = service.train_models(
                currency=args.currency,
                start_time=start_time,
                end_time=end_time
            )

            if "error" in result:
                logger.error(f"Training failed: {result['error']}")
                return 1

            # Display results
            logger.info("\n" + "="*60)
            logger.info("Training Results")
            logger.info("="*60)

            if result.get("classifier"):
                clf = result["classifier"]
                logger.info("\nClassifier (Market Regime):")
                logger.info(f"  Model ID: {clf['model_id']}")
                logger.info(f"  Path: {clf['model_path']}")
                logger.info(f"  Samples: {clf['n_samples']}")
                logger.info(f"  Features: {clf['n_features']}")
                logger.info(f"  Validation Metrics:")
                for metric, value in clf['validation_metrics'].items():
                    logger.info(f"    {metric}: {value:.4f}")

                # Top features
                logger.info(f"  Top 10 Features:")
                top_features = sorted(
                    clf['top_features'].items(),
                    key=lambda x: x[1],
                    reverse=True
                )[:10]
                for feat, importance in top_features:
                    logger.info(f"    {feat}: {importance:.4f}")

            if result.get("regressor"):
                for target, reg in result["regressor"].items():
                    logger.info(f"\nRegressor ({target}):")
                    logger.info(f"  Model ID: {reg['model_id']}")
                    logger.info(f"  Path: {reg['model_path']}")
                    logger.info(f"  Samples: {reg['n_samples']}")
                    logger.info(f"  Features: {reg['n_features']}")
                    logger.info(f"  Validation Metrics:")
                    for metric, value in reg['validation_metrics'].items():
                        logger.info(f"    {metric}: {value:.4f}")

                    # Top features
                    logger.info(f"  Top 10 Features:")
                    top_features = sorted(
                        reg['top_features'].items(),
                        key=lambda x: x[1],
                        reverse=True
                    )[:10]
                    for feat, importance in top_features:
                        logger.info(f"    {feat}: {importance:.4f}")

        logger.info("\n" + "="*60)
        logger.info("Complete!")
        logger.info("="*60)

        return 0

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    exit(main())
