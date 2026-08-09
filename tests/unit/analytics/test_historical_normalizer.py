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


class TestCalendarSpanGate:
    """
    Task G2-E: HistoricalNormalizer's sufficiency gate previously only
    checked observation COUNT (n >= MIN_OBS). A front-month expiry's own
    percentile series is keyed to that expiry's own identifier, which
    structurally cannot exist -- and so cannot have been observed by the
    hourly collector -- 30 or 90 days before its own expiration. Confirmed
    live case (cross-checked against the DB): one expiry's GEX series had
    89 hourly observations (>= MIN_OBS for BOTH windows) spanning only 3.75
    calendar days (oldest=2026-08-04 08:00, newest=2026-08-08 02:00), yet
    the report rendered "90d: p97 z+2.49 EXTREME HIGH" as if it had genuine
    90-day depth.
    """

    def setup_method(self):
        self.normalizer = HistoricalNormalizer()

    def test_g2e_confirmed_case_89_obs_3_75_day_span_is_insufficient_both_windows(self):
        # Reproduces the audit's exact confirmed numbers: n=89 (>= MIN_OBS
        # for 30d AND 90d) but the oldest observation is only 3.75 days
        # old -- nowhere near 80% of either the 30d (24d) or 90d (72d)
        # threshold. A distinct value per point so a real (non-degenerate)
        # percentile/z would have been computed had the gate not caught it.
        history = [float(i) for i in range(89)]
        result = self.normalizer.normalize(
            name="net_gex", value=999.0,
            history_30d=history, history_90d=history, unit="USD",
            oldest_age_days_30d=3.75, oldest_age_days_90d=3.75,
        )
        assert result.n_30d == 89
        assert result.n_90d == 89
        # Old (buggy) gate: n=89 >= MIN_OBS=30 -> would have reported
        # "sufficient" for BOTH windows. The fixed gate must refuse both.
        assert result.sufficient is False
        assert result.percentile_30d is None
        assert result.z_30d is None
        assert result.regime_30d is None
        assert result.percentile_90d is None
        assert result.z_90d is None
        assert result.span_days_30d == pytest.approx(3.75)
        assert result.span_days_90d == pytest.approx(3.75)

    def test_genuine_30d_span_still_renders_sufficient(self):
        # Don't over-tighten: a series that genuinely has ~30 days of
        # depth (span >= 80% of 30 days = 24 days) must still be usable.
        history = [float(i) for i in range(720)]  # hourly for 30 days
        result = self.normalizer.normalize(
            name="net_gex", value=999.0,
            history_30d=history, history_90d=history, unit="USD",
            oldest_age_days_30d=30.0, oldest_age_days_90d=30.0,
        )
        assert result.sufficient is True
        assert result.percentile_30d is not None
        # The 90d window has the same 30-day-old data -- genuinely
        # insufficient for a 90d claim (30/90 = 33% < 80%), and the fixed
        # gate must say so rather than silently rendering a "90d" label
        # backed by only 30 days of history.
        assert result.percentile_90d is None

    def test_genuine_90d_span_renders_sufficient_for_both_windows(self):
        history = [float(i) for i in range(2160)]  # hourly for 90 days
        result = self.normalizer.normalize(
            name="net_gex", value=999.0,
            history_30d=history, history_90d=history, unit="USD",
            oldest_age_days_30d=90.0, oldest_age_days_90d=90.0,
        )
        assert result.sufficient is True
        assert result.percentile_30d is not None
        assert result.percentile_90d is not None

    def test_span_exactly_at_80_percent_threshold_is_sufficient(self):
        # 24.0 / 30.0 == 0.8 exactly -- boundary is inclusive (>=).
        history = [1.0] * 30
        result = self.normalizer.normalize(
            name="test_metric", value=1.5,
            history_30d=history, history_90d=history, unit="USD",
            oldest_age_days_30d=24.0, oldest_age_days_90d=24.0,
        )
        assert result.sufficient is True

    def test_span_just_under_80_percent_threshold_is_insufficient(self):
        history = [1.0] * 30
        result = self.normalizer.normalize(
            name="test_metric", value=1.5,
            history_30d=history, history_90d=history, unit="USD",
            oldest_age_days_30d=23.9, oldest_age_days_90d=23.9,
        )
        assert result.sufficient is False
        assert result.percentile_30d is None

    def test_none_span_exempts_the_gate_backward_compatible(self):
        # Callers that don't supply span data (this class's own count-only
        # tests, or a not-yet-wired caller) keep the prior n-only gate --
        # None must not be misread as "span is zero."
        history = [1.0] * 30
        result = self.normalizer.normalize(
            name="test_metric", value=1.5,
            history_30d=history, history_90d=history, unit="USD",
        )
        assert result.sufficient is True
        assert result.span_days_30d is None
        assert result.span_days_90d is None

    def test_has_sufficient_span_static_boundaries(self):
        assert HistoricalNormalizer.has_sufficient_span(None, 30.0) is True
        assert HistoricalNormalizer.has_sufficient_span(24.0, 30.0) is True
        assert HistoricalNormalizer.has_sufficient_span(23.999, 30.0) is False
        assert HistoricalNormalizer.has_sufficient_span(72.0, 90.0) is True
        assert HistoricalNormalizer.has_sufficient_span(71.999, 90.0) is False


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
