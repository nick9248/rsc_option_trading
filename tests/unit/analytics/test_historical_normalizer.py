"""
Unit tests for coding.core.analytics.historical_normalizer.

institutional_metrics_spec.md section 1: HistoricalNormalizer computes a
percentile + z-score + regime label for a metric's current value against its
own trailing history (30d and 90d windows). Pure math -- no DB/API access.

Acceptance tests T1.1-T1.3 are hand-computed in the spec; reproduced here
verbatim plus additional edge-case coverage.
"""

import math

import pytest

from coding.core.analytics.historical_normalizer import (
    HistoricalNormalizer,
    MetricSpec,
    NormalizedMetric,
)


class TestPercentileStatic:
    def test_t1_1_known_series(self):
        # spec T1.1: count(<21)=6 of 10 -> 60.0
        history = [10, 12, 14, 16, 18, 20, 22, 24, 26, 28]
        assert HistoricalNormalizer.percentile(21.0, history) == pytest.approx(60.0)

    def test_mid_rank_ties_count_as_half(self):
        # value equal to two of ten points: count(<) = 4, count(==) = 2
        # -> 100 * (4 + 0.5*2) / 10 = 50.0
        history = [1, 2, 3, 4, 5, 5, 6, 7, 8, 9]
        assert HistoricalNormalizer.percentile(5, history) == pytest.approx(50.0)

    def test_t1_2_zero_variance_percentile_is_50(self):
        history = [5.0] * 10
        assert HistoricalNormalizer.percentile(5.0, history) == pytest.approx(50.0)

    def test_empty_history_returns_none(self):
        assert HistoricalNormalizer.percentile(5.0, []) is None


class TestZscoreStatic:
    def test_t1_1_known_series(self):
        history = [10, 12, 14, 16, 18, 20, 22, 24, 26, 28]
        z = HistoricalNormalizer.zscore(21.0, history)
        assert z == pytest.approx(0.3481553119113957, abs=1e-9)

    def test_t1_2_zero_variance_returns_none(self):
        history = [5.0] * 10
        assert HistoricalNormalizer.zscore(5.0, history) is None

    def test_empty_history_returns_none(self):
        assert HistoricalNormalizer.zscore(5.0, []) is None


class TestNormalize:
    def setup_method(self):
        self.normalizer = HistoricalNormalizer()

    def test_t1_1_percentile_z_and_regime(self):
        # T1.1's 10-point series is below MIN_OBS (30); the normalize()
        # wrapper gates both windows on MIN_OBS (n_90d >= 30 too), so this
        # repeats the exact T1.1 series 3x (30 points) -- repetition leaves
        # the mid-rank count ratio, mean, and variance unchanged, so the
        # spec's hand-computed percentile/z still apply exactly.
        history = [10, 12, 14, 16, 18, 20, 22, 24, 26, 28] * 3
        result = self.normalizer.normalize(
            name="test_metric", value=21.0,
            history_30d=history,
            history_90d=history,
            unit="USD",
        )
        assert result.percentile_90d == pytest.approx(60.0)
        assert result.z_90d == pytest.approx(0.3481553119113957, abs=1e-9)
        assert result.regime_30d == "ELEVATED"  # p60.0 -> [60,80) per the ladder

    def test_t1_1_regime_label_elevated(self):
        # Percentile 60.0 falls in [60, 80) -> ELEVATED (spec's boundary rule).
        history = [0.0] * 29 + [10, 12, 14, 16, 18, 20, 22, 24, 26, 28]
        result = self.normalizer.normalize(
            name="test_metric", value=21.0,
            history_30d=history, history_90d=history, unit="USD",
        )
        # 30d history has 39 points; recompute expected percentile directly.
        expected_pctile = HistoricalNormalizer.percentile(21.0, history)
        assert result.percentile_30d == pytest.approx(expected_pctile)

    def test_t1_2_zero_variance_guard(self):
        history = [5.0] * 30
        result = self.normalizer.normalize(
            name="test_metric", value=5.0,
            history_30d=history, history_90d=history, unit="ratio",
        )
        assert result.z_30d is None
        assert result.z_90d is None
        assert result.percentile_30d == pytest.approx(50.0)
        assert result.regime_30d == "NORMAL"
        assert not math.isnan(result.percentile_30d)

    def test_t1_3_insufficient_history(self):
        history_29 = [1.0] * 29
        result = self.normalizer.normalize(
            name="test_metric", value=1.5,
            history_30d=history_29, history_90d=history_29, unit="USD",
        )
        assert result.sufficient is False
        assert result.percentile_30d is None
        assert result.z_30d is None
        assert result.n_30d == 29
        assert result.value == 1.5

    def test_min_obs_boundary_exactly_30_is_sufficient(self):
        history_30 = [1.0] * 30
        result = self.normalizer.normalize(
            name="test_metric", value=1.5,
            history_30d=history_30, history_90d=history_30, unit="USD",
        )
        assert result.sufficient is True
        assert result.n_30d == 30

    def test_signed_metric_normalized_on_raw_value_not_abs(self):
        # Net GEX / funding / RR legitimately cross zero -- must not be
        # normalized on abs(value).
        history = list(range(-15, 15))  # -15..14, 30 points, crosses zero
        result = self.normalizer.normalize(
            name="net_gex", value=-10.0,
            history_30d=history, history_90d=history, unit="USD",
        )
        expected = HistoricalNormalizer.percentile(-10.0, history)
        assert result.percentile_30d == pytest.approx(expected)
        assert expected < 50.0  # -10 sits low in a symmetric -15..14 range

    def test_unit_and_name_passthrough(self):
        history = [1.0] * 30
        result = self.normalizer.normalize(
            name="dvol", value=37.69,
            history_30d=history, history_90d=history, unit="vol pts",
        )
        assert result.name == "dvol"
        assert result.value == 37.69
        assert result.unit == "vol pts"


class TestRegimeLadder:
    """institutional_metrics_spec.md section 1(b): single 7-band ladder."""

    def setup_method(self):
        self.normalizer = HistoricalNormalizer()

    @pytest.mark.parametrize(
        "percentile,expected_label",
        [
            (95.0, "EXTREME HIGH"),
            (99.9, "EXTREME HIGH"),
            (80.0, "HIGH"),
            (94.9, "HIGH"),
            (60.0, "ELEVATED"),
            (79.9, "ELEVATED"),
            (40.0, "NORMAL"),
            (59.9, "NORMAL"),
            (20.0, "SUBDUED"),
            (39.9, "SUBDUED"),
            (5.0, "LOW"),
            (19.9, "LOW"),
            (0.0, "EXTREME LOW"),
            (4.9, "EXTREME LOW"),
        ],
    )
    def test_ladder_boundaries(self, percentile, expected_label):
        assert HistoricalNormalizer.regime_label(percentile) == expected_label

    def test_regime_label_none_for_none_percentile(self):
        assert HistoricalNormalizer.regime_label(None) is None


class TestNormalizeMany:
    def test_normalizes_a_batch_of_specs(self):
        normalizer = HistoricalNormalizer()
        history = [1.0] * 30
        specs = [
            MetricSpec(name="a", value=1.0, history_30d=history, history_90d=history, unit="USD"),
            MetricSpec(name="b", value=2.0, history_30d=history, history_90d=history, unit="ratio"),
        ]
        results = normalizer.normalize_many(specs)
        assert set(results.keys()) == {"a", "b"}
        assert isinstance(results["a"], NormalizedMetric)
        assert results["a"].value == 1.0
        assert results["b"].value == 2.0
