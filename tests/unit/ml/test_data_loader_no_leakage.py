"""Tests that _align_data does not leak label columns into features."""
import pandas as pd
import numpy as np
import pytest
from datetime import datetime

from coding.core.ml.data.data_loader import MLDataLoader


def _make_loader():
    loader = MLDataLoader.__new__(MLDataLoader)
    return loader


class TestAlignDataNoLeakage:

    def test_realized_vol_label_not_in_features(self):
        """After _align_data, realized_vol_24h_label must NOT appear in features."""
        loader = _make_loader()

        idx = pd.date_range("2026-04-01", periods=10, freq="h")
        features = pd.DataFrame({
            "avg_iv": np.random.uniform(30, 60, 10),
            "realized_vol_24h": np.random.uniform(15, 45, 10),
        }, index=idx)
        labels = pd.DataFrame({
            "realized_vol_24h": np.random.uniform(20, 70, 10),
            "market_regime": ["sideways"] * 10,
        }, index=idx)

        aligned_features, aligned_labels = loader._align_data(features, labels)

        assert "realized_vol_24h_label" not in aligned_features.columns, (
            "realized_vol_24h_label must not appear in features — it is the forward vol target "
            "and unavailable at inference time"
        )

    def test_original_realized_vol_feature_retained(self):
        """The past-vol feature 'realized_vol_24h' (from hourly snapshots) must stay in features."""
        loader = _make_loader()

        idx = pd.date_range("2026-04-01", periods=10, freq="h")
        features = pd.DataFrame({
            "avg_iv": np.random.uniform(30, 60, 10),
            "realized_vol_24h": np.random.uniform(15, 45, 10),
        }, index=idx)
        labels = pd.DataFrame({
            "realized_vol_24h": np.random.uniform(20, 70, 10),
            "market_regime": ["sideways"] * 10,
        }, index=idx)

        aligned_features, aligned_labels = loader._align_data(features, labels)

        assert "realized_vol_24h" in aligned_features.columns, (
            "Past-vol feature must remain in features as a vol-clustering signal"
        )

    def test_label_columns_not_in_features(self):
        """No label column (raw or suffixed) should appear in the returned features."""
        loader = _make_loader()

        idx = pd.date_range("2026-04-01", periods=5, freq="h")
        features = pd.DataFrame({
            "avg_iv": [40.0] * 5,
            "realized_vol_24h": [20.0] * 5,
            "market_regime": ["sideways"] * 5,
        }, index=idx)
        labels = pd.DataFrame({
            "realized_vol_24h": [35.0] * 5,
            "market_regime": ["sideways"] * 5,
        }, index=idx)

        aligned_features, aligned_labels = loader._align_data(features, labels)

        for col in ["realized_vol_24h_label", "market_regime_label"]:
            assert col not in aligned_features.columns, f"{col} must not be in features"

    def test_label_only_column_not_in_features(self):
        """A column that exists only in labels must not appear in features."""
        loader = _make_loader()
        idx = pd.date_range("2026-04-01", periods=5, freq="h")
        # market_regime deliberately absent from features — it is label-only in production
        features = pd.DataFrame({
            "avg_iv": [40.0] * 5,
            "realized_vol_24h": [20.0] * 5,
        }, index=idx)
        labels = pd.DataFrame({
            "realized_vol_24h": [35.0] * 5,
            "market_regime": ["sideways"] * 5,
        }, index=idx)

        aligned_features, aligned_labels = loader._align_data(features, labels)

        assert "market_regime" not in aligned_features.columns, (
            "market_regime is a label-only column; it must not appear in features"
        )

    def test_aligned_labels_contain_label_values_not_feature_values(self):
        """aligned_labels must contain the label-side values, not the feature-side values."""
        loader = _make_loader()
        idx = pd.date_range("2026-04-01", periods=5, freq="h")
        features = pd.DataFrame({
            "avg_iv": [40.0] * 5,
            "realized_vol_24h": [10.0] * 5,   # past vol — deliberately different from label
        }, index=idx)
        labels = pd.DataFrame({
            "realized_vol_24h": [99.0] * 5,   # forward vol — the actual target
            "market_regime": ["sideways"] * 5,
        }, index=idx)

        _, aligned_labels = loader._align_data(features, labels)

        assert list(aligned_labels["realized_vol_24h"]) == [99.0] * 5, (
            "aligned_labels must return forward vol (label values), not past vol (feature values)"
        )
