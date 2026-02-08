"""Debug timestamp alignment issue."""

import logging
from datetime import datetime, timedelta
from coding.core.database.repository import DatabaseRepository
from coding.core.ml.data.data_loader import MLDataLoader
from coding.core.ml.models.ml_config import MLTrainingConfig

logging.basicConfig(level=logging.WARNING)

repo = DatabaseRepository()
loader = MLDataLoader(repository=repo)

# Test period
end_time = datetime.now()
start_time = end_time - timedelta(days=30)

print("Checking timestamp formats...")
print("=" * 60)

# Load features directly
snapshot_features = loader._load_snapshot_features("BTC", start_time, end_time)
market_features = loader._load_market_features("BTC", start_time, end_time)

print(f"\nSnapshot features: {len(snapshot_features)} rows")
if len(snapshot_features) > 0:
    print(f"  Index type: {type(snapshot_features.index[0])}")
    print(f"  First timestamp: {snapshot_features.index[0]}")
    print(f"  Last timestamp: {snapshot_features.index[-1]}")
    print(f"  Sample timestamps:")
    for ts in snapshot_features.index[:5]:
        print(f"    {ts} (type: {type(ts)})")

print(f"\nMarket features: {len(market_features)} rows")
if len(market_features) > 0:
    print(f"  Index type: {type(market_features.index[0])}")
    print(f"  First timestamp: {market_features.index[0]}")
    print(f"  Last timestamp: {market_features.index[-1]}")
    print(f"  Sample timestamps:")
    for ts in market_features.index[:5]:
        print(f"    {ts} (type: {type(ts)})")

# Load labels
labels = loader._load_labels("BTC", start_time, end_time)

print(f"\nLabels: {len(labels)} rows")
if len(labels) > 0:
    print(f"  Index type: {type(labels.index[0])}")
    print(f"  First timestamp: {labels.index[0]}")
    print(f"  Last timestamp: {labels.index[-1]}")
    print(f"  Sample timestamps:")
    for ts in labels.index[:5]:
        print(f"    {ts} (type: {type(ts)})")

# Check overlap
if len(snapshot_features) > 0 and len(labels) > 0:
    print(f"\n--- Overlap Analysis ---")
    feature_timestamps = set(snapshot_features.index)
    label_timestamps = set(labels.index)

    overlap = feature_timestamps & label_timestamps
    print(f"Feature timestamps: {len(feature_timestamps)}")
    print(f"Label timestamps: {len(label_timestamps)}")
    print(f"Overlapping: {len(overlap)}")

    if len(overlap) == 0:
        print("\nNO OVERLAP! Investigating...")

        # Show some example timestamps from each
        print("\nSample feature timestamps (first 5):")
        for ts in list(feature_timestamps)[:5]:
            print(f"  {ts} | {repr(ts)}")

        print("\nSample label timestamps (first 5):")
        for ts in list(label_timestamps)[:5]:
            print(f"  {ts} | {repr(ts)}")

        # Check if they're close but not exact
        print("\nChecking if timestamps are close but not exact...")
        for f_ts in list(feature_timestamps)[:3]:
            for l_ts in list(label_timestamps)[:3]:
                diff = abs((f_ts - l_ts).total_seconds())
                if diff < 3600:  # Within 1 hour
                    print(f"  CLOSE: {f_ts} vs {l_ts} (diff: {diff}s)")
