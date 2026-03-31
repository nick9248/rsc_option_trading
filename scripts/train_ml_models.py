"""
Train ML Models with Full 70-Feature Set

Trains regime detection and volatility forecasting models using the newly
backfilled features (technical indicators, external metrics, DVOL, funding rate).
"""

import logging
from datetime import datetime, timedelta

from coding.service.ml.ml_training_service import MLTrainingService
from coding.core.ml.models.ml_config import MLTrainingConfig, WalkForwardConfig

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def train_models():
    """Train ML models with full feature set."""
    print("=" * 60)
    print("ML Model Training - Full 70-Feature Set")
    print("=" * 60)

    # Walk-forward config for ~800h of available data (55 days)
    # Fold structure: 480h train → 96h test → step 96h → repeat
    # 3 folds need: 480 + 96*3 = 768h < ~795h available ✓
    walk_forward_config = WalkForwardConfig(
        min_train_hours=480,  # 20 days initial training window
        test_hours=96,        # 4 days out-of-sample test
        step_hours=96,        # Advance 4 days between folds
        expanding=True,       # Growing window (more realistic)
        min_folds=3           # Minimum 3 folds for validation
    )

    # Create training configuration
    config = MLTrainingConfig(
        classification_target="market_regime",
        regression_targets=["realized_vol_24h"],
        min_samples=400,

        # Walk-forward validation
        walk_forward=walk_forward_config
    )

    # Initialize training service
    print("\nInitializing ML training service...")
    service = MLTrainingService(config=config)

    # Use all available hourly snapshot data (~55 days)
    end_time = datetime.now()
    start_time = end_time - timedelta(days=55)

    print(f"\nTraining period: {start_time.date()} to {end_time.date()}")
    print("=" * 60)

    # Train models for BTC
    print("\n[1/2] Training BTC models...")
    print("-" * 60)

    try:
        btc_result = service.train_models(
            currency="BTC",
            start_time=start_time,
            end_time=end_time
        )

        print("\n--- BTC Training Results ---")
        if "error" in btc_result:
            print(f"ERROR: {btc_result['error']}")
        else:
            # Classifier results
            if btc_result.get("classifier"):
                clf = btc_result["classifier"]
                print(f"\nClassifier (Market Regime):")
                print(f"  Model saved: {clf.get('model_path', 'N/A')}")
                if "validation" in clf:
                    val = clf["validation"]
                    print(f"  Validation folds: {val.get('n_folds', 0)}")
                    print(f"  Avg accuracy: {val.get('mean_accuracy', 0):.3f}")
                    print(f"  Std accuracy: {val.get('std_accuracy', 0):.3f}")

            # Regressor results
            if btc_result.get("regressor"):
                reg = btc_result["regressor"]
                print(f"\nRegressor (Volatility):")
                print(f"  Model saved: {reg.get('model_path', 'N/A')}")
                if "validation" in reg:
                    val = reg["validation"]
                    print(f"  Validation folds: {val.get('n_folds', 0)}")
                    print(f"  Avg RMSE: {val.get('mean_rmse', 0):.2f}")
                    print(f"  Avg R2: {val.get('mean_r2', 0):.3f}")

    except Exception as e:
        print(f"\nBTC training failed: {e}")
        import traceback
        traceback.print_exc()

    # Train models for ETH
    print("\n\n[2/2] Training ETH models...")
    print("-" * 60)

    try:
        eth_result = service.train_models(
            currency="ETH",
            start_time=start_time,
            end_time=end_time
        )

        print("\n--- ETH Training Results ---")
        if "error" in eth_result:
            print(f"ERROR: {eth_result['error']}")
        else:
            # Classifier results
            if eth_result.get("classifier"):
                clf = eth_result["classifier"]
                print(f"\nClassifier (Market Regime):")
                print(f"  Model saved: {clf.get('model_path', 'N/A')}")
                if "validation" in clf:
                    val = clf["validation"]
                    print(f"  Validation folds: {val.get('n_folds', 0)}")
                    print(f"  Avg accuracy: {val.get('mean_accuracy', 0):.3f}")
                    print(f"  Std accuracy: {val.get('std_accuracy', 0):.3f}")

            # Regressor results
            if eth_result.get("regressor"):
                reg = eth_result["regressor"]
                print(f"\nRegressor (Volatility):")
                print(f"  Model saved: {reg.get('model_path', 'N/A')}")
                if "validation" in reg:
                    val = reg["validation"]
                    print(f"  Validation folds: {val.get('n_folds', 0)}")
                    print(f"  Avg RMSE: {val.get('mean_rmse', 0):.2f}")
                    print(f"  Avg R2: {val.get('mean_r2', 0):.3f}")

    except Exception as e:
        print(f"\nETH training failed: {e}")
        import traceback
        traceback.print_exc()

    print("\n" + "=" * 60)
    print("TRAINING COMPLETE!")
    print("=" * 60)
    print("\nModels trained with full 70-feature set including:")
    print("  - Technical indicators (SMA, EMA, RSI, MACD, ADX, ATR)")
    print("  - External metrics (Fear & Greed, BTC/ETH Dominance)")
    print("  - DVOL (volatility fear signal)")
    print("  - Funding rate (positioning bias)")
    print("\nNext: Use models for regime detection and strategy evaluation!")


if __name__ == "__main__":
    train_models()
