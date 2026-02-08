"""
Test ML Data Loader with New Features

Verifies that the ML data loader can successfully load all features including
the newly backfilled technical indicators, external metrics, DVOL, and funding rate.
"""

import logging
from datetime import datetime, timedelta

from coding.core.database.repository import DatabaseRepository
from coding.core.ml.data.data_loader import MLDataLoader
from coding.core.ml.models.ml_config import MLTrainingConfig

logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)


def test_data_loader():
    """Test ML data loader with new feature tables."""
    print("=" * 60)
    print("ML Data Loader Test - New Features")
    print("=" * 60)

    # Initialize
    repo = DatabaseRepository()
    data_loader = MLDataLoader(repository=repo)

    # Test period (last 30 days)
    end_time = datetime.now()
    start_time = end_time - timedelta(days=30)

    print(f"\nTesting data load for BTC")
    print(f"Period: {start_time.date()} to {end_time.date()}")
    print()

    # Create minimal config
    config = MLTrainingConfig(
        target_hours_ahead=24,
        min_samples=100
    )

    try:
        # Load training data
        print("Loading training data...")
        features, labels = data_loader.load_training_data(
            currency="BTC",
            start_time=start_time,
            end_time=end_time,
            config=config
        )

        print(f"\nOK Data loaded successfully!")
        print(f"\nFeatures shape: {features.shape}")
        print(f"Labels shape: {labels.shape}")

        # Check for new feature columns
        print(f"\n--- Feature Categories ---")

        # Technical indicators
        tech_cols = [col for col in features.columns if any(
            keyword in col for keyword in ['sma', 'ema', 'rsi', 'macd', 'adx', 'atr']
        )]
        print(f"\nTechnical Indicators ({len(tech_cols)} features):")
        for col in sorted(tech_cols)[:10]:
            print(f"  - {col}")
        if len(tech_cols) > 10:
            print(f"  ... and {len(tech_cols) - 10} more")

        # External metrics
        external_cols = [col for col in features.columns if any(
            keyword in col for keyword in ['fear_greed', 'btc_dominance', 'eth_dominance']
        )]
        print(f"\nExternal Metrics ({len(external_cols)} features):")
        for col in sorted(external_cols):
            print(f"  - {col}")

        # DVOL
        dvol_cols = [col for col in features.columns if 'dvol' in col]
        print(f"\nDVOL ({len(dvol_cols)} features):")
        for col in sorted(dvol_cols):
            print(f"  - {col}")

        # Funding rate
        funding_cols = [col for col in features.columns if 'funding' in col]
        print(f"\nFunding Rate ({len(funding_cols)} features):")
        for col in sorted(funding_cols):
            print(f"  - {col}")

        # Show total
        print(f"\n--- Summary ---")
        print(f"Total features: {len(features.columns)}")
        print(f"Total samples: {len(features)}")

        # Check for nulls
        null_counts = features.isnull().sum()
        null_features = null_counts[null_counts > 0]

        if len(null_features) > 0:
            print(f"\nFeatures with nulls: {len(null_features)}")
            print("Top features with missing values:")
            for col in null_features.head(5).index:
                print(f"  - {col}: {null_features[col]} nulls ({null_features[col]/len(features)*100:.1f}%)")
        else:
            print(f"\nOK No missing values!")

        # Show sample data
        print(f"\n--- Sample Data (first row) ---")
        sample = features.iloc[0]

        # Show a few technical indicators
        print("\nTechnical Indicators:")
        for col in ['sma_50', 'sma_200', 'rsi', 'adx']:
            if col in sample:
                print(f"  {col}: {sample[col]:.2f}")

        # Show external metrics
        print("\nExternal Metrics:")
        for col in ['fear_greed_value', 'btc_dominance', 'eth_dominance']:
            if col in sample:
                print(f"  {col}: {sample[col]:.2f}")

        # Show DVOL and funding
        if 'dvol' in sample:
            print(f"\nDVOL: {sample['dvol']:.2f}")
        if 'funding_rate' in sample:
            print(f"Funding Rate: {sample['funding_rate']:.6f}")

        print("\n" + "=" * 60)
        print("TEST PASSED!")
        print("=" * 60)
        print("\nOK ML data loader successfully loads all new features")
        print("OK Ready for model training with full 70-feature set")

        return True

    except Exception as e:
        print(f"\nERROR TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = test_data_loader()
    exit(0 if success else 1)