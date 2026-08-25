"""
Unit tests for OnChainAnalysisService._oldest_observation_age_days and its
wiring into _build_normalized_metrics's MetricSpec construction (Task G2-E).

Confirmed bug this fixes: HistoricalNormalizer's 30d/90d sufficiency gate
previously checked observation COUNT only (n >= MIN_OBS). A per-expiry
percentile series (net GEX/PCR-OI/total OI, keyed to one specific expiry
string like "8AUG26") can satisfy n >= MIN_OBS for BOTH windows while
spanning only a handful of calendar days -- confirmed live case: 89 hourly
observations spanning 3.75 calendar days, rendered as "90d: p97 z+2.49
EXTREME HIGH". These tests exercise the REAL service wiring (repository ->
_oldest_observation_age_days -> MetricSpec -> HistoricalNormalizer.normalize),
not just the pure-math class in isolation, per this task's requirement to
confirm the fix reaches the actual live code path.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from tests.unit.test_on_chain_analysis_service_normalized_metrics import (
    _FakeAnalyzer,
    _history_side_effect,
    _make_repo_mock,
    _make_result,
    _make_service,
)


def _oldest_ts_side_effect(ts_by_key):
    """Mirrors _history_side_effect's keying, for get_metric_history_oldest_timestamp."""
    def _side_effect(table, column, currency, lookback_hours, expiration=None, time_column=None):
        return ts_by_key.get((table, column, lookback_hours))
    return _side_effect


class TestOldestObservationAgeDays:
    """Direct tests of the new helper method."""

    def test_returns_none_when_repository_returns_none(self):
        repo = _make_repo_mock()
        repo.get_metric_history_oldest_timestamp.return_value = None
        service = _make_service(repository=repo)
        age = service._oldest_observation_age_days(
            table="onchain_analysis_snapshots", column="total_net_gex",
            currency="BTC", lookback_hours=720, expiration="8AUG26",
        )
        assert age is None

    def test_computes_age_in_days_from_a_real_timestamp(self):
        repo = _make_repo_mock()
        now = datetime.now(timezone.utc)
        repo.get_metric_history_oldest_timestamp.return_value = now - timedelta(days=3.75)
        service = _make_service(repository=repo)
        age = service._oldest_observation_age_days(
            table="onchain_analysis_snapshots", column="total_net_gex",
            currency="BTC", lookback_hours=720, expiration="8AUG26",
        )
        assert age == pytest.approx(3.75, abs=0.01)

    def test_naive_timestamp_does_not_crash(self):
        """
        UTC/null-safety discipline: the DB driver may hand back a naive
        datetime (no tzinfo) depending on the column type. datetime.now()
        must be called with the SAME tz-awareness (None here), mirroring
        _compute_historical_context_staleness's existing
        datetime.now(most_stale.tzinfo) pattern, or this raises
        "can't subtract offset-naive and offset-aware datetimes."
        """
        repo = _make_repo_mock()
        naive_ts = datetime.now() - timedelta(days=5)  # no tzinfo
        repo.get_metric_history_oldest_timestamp.return_value = naive_ts
        service = _make_service(repository=repo)
        age = service._oldest_observation_age_days(
            table="onchain_analysis_snapshots", column="total_net_gex",
            currency="BTC", lookback_hours=720, expiration="8AUG26",
        )
        assert age == pytest.approx(5.0, abs=0.01)

    def test_value_error_from_repository_returns_none_not_raised(self):
        repo = _make_repo_mock()
        repo.get_metric_history_oldest_timestamp.side_effect = ValueError("not whitelisted")
        service = _make_service(repository=repo)
        age = service._oldest_observation_age_days(
            table="onchain_analysis_snapshots", column="total_net_gex",
            currency="BTC", lookback_hours=720, expiration="8AUG26",
        )
        assert age is None

    def test_generic_exception_from_repository_returns_none_not_raised(self):
        repo = _make_repo_mock()
        repo.get_metric_history_oldest_timestamp.side_effect = RuntimeError("db timeout")
        service = _make_service(repository=repo)
        age = service._oldest_observation_age_days(
            table="onchain_analysis_snapshots", column="total_net_gex",
            currency="BTC", lookback_hours=720, expiration="8AUG26",
        )
        assert age is None


class TestBuildNormalizedMetricsSpanWiring:
    """
    End-to-end through _build_normalized_metrics: repository timestamps ->
    MetricSpec.oldest_age_days_* -> HistoricalNormalizer.normalize's span
    gate -> NormalizedMetric.sufficient.
    """

    def test_g2e_confirmed_case_high_count_short_span_gates_net_gex_insufficient(self):
        """
        Reproduces the confirmed bug end-to-end: net_gex history has 89
        points (>= MIN_OBS for both windows) but the oldest observation is
        only 3.75 days old. Before this fix, the service wiring never
        looked at timestamps at all, so this would have rendered
        sufficient=True for both windows purely off the count.
        """
        repo = _make_repo_mock()
        history_89 = [float(i) for i in range(89)]
        repo.get_metric_history.side_effect = _history_side_effect({
            ("onchain_analysis_snapshots", "total_net_gex", 720): history_89,
            ("onchain_analysis_snapshots", "total_net_gex", 2160): history_89,
        })
        now = datetime.now(timezone.utc)
        oldest = now - timedelta(days=3.75)
        repo.get_metric_history_oldest_timestamp.side_effect = _oldest_ts_side_effect({
            ("onchain_analysis_snapshots", "total_net_gex", 720): oldest,
            ("onchain_analysis_snapshots", "total_net_gex", 2160): oldest,
        })
        service = _make_service(repository=repo)
        analyzer = _FakeAnalyzer(market_metrics={})
        result = _make_result()
        # Give the front-month expiration a real gex_dex.total_net_gex so
        # net_gex actually gets wired -- reuse the default expiration.
        import dataclasses
        bundle = result.expirations[0]
        gex_dex = MagicMock()
        gex_dex.total_net_gex = 5_000_000.0
        result = dataclasses.replace(
            result,
            expirations=(dataclasses.replace(bundle, gex_dex=gex_dex),),
        )

        metrics, _, _ = service._build_normalized_metrics(analyzer, result)

        assert "net_gex" in metrics
        net_gex = metrics["net_gex"]
        assert net_gex.n_30d == 89
        assert net_gex.n_90d == 89
        # The bug: count alone would have said "sufficient" for both.
        # The fix: span (3.75d) fails both the 30d (24d) and 90d (72d)
        # thresholds, so both must be gated as insufficient.
        assert net_gex.sufficient is False
        assert net_gex.percentile_30d is None
        assert net_gex.percentile_90d is None
        assert net_gex.span_days_30d == pytest.approx(3.75, abs=0.01)
        assert net_gex.span_days_90d == pytest.approx(3.75, abs=0.01)

    def test_genuine_span_still_renders_sufficient_end_to_end(self):
        """Don't over-tighten: a genuinely 90-day-old series must still work."""
        repo = _make_repo_mock()
        history_90d = [float(i) for i in range(2160)]
        repo.get_metric_history.side_effect = _history_side_effect({
            ("onchain_analysis_snapshots", "total_net_gex", 720): history_90d,
            ("onchain_analysis_snapshots", "total_net_gex", 2160): history_90d,
        })
        now = datetime.now(timezone.utc)
        oldest = now - timedelta(days=90)
        repo.get_metric_history_oldest_timestamp.side_effect = _oldest_ts_side_effect({
            ("onchain_analysis_snapshots", "total_net_gex", 720): oldest,
            ("onchain_analysis_snapshots", "total_net_gex", 2160): oldest,
        })
        service = _make_service(repository=repo)
        analyzer = _FakeAnalyzer(market_metrics={})
        result = _make_result()
        import dataclasses
        bundle = result.expirations[0]
        gex_dex = MagicMock()
        gex_dex.total_net_gex = 5_000_000.0
        result = dataclasses.replace(
            result,
            expirations=(dataclasses.replace(bundle, gex_dex=gex_dex),),
        )

        metrics, _, _ = service._build_normalized_metrics(analyzer, result)

        net_gex = metrics["net_gex"]
        assert net_gex.sufficient is True
        assert net_gex.percentile_30d is not None
        assert net_gex.percentile_90d is not None

    def test_oldest_timestamp_failure_does_not_suppress_history_fetch_success(self):
        """
        Isolation constraint: a failure in the NEW oldest-timestamp lookup
        must not be attributed to, or suppress, the pre-existing
        get_metric_history call it sits alongside. If they shared a
        try/except, this RuntimeError would trigger the "Failed to fetch
        net_gex history" except-Exception branch and drop net_gex
        entirely -- it must not.
        """
        repo = _make_repo_mock()
        history_30 = [float(i) for i in range(30)]
        history_90 = [float(i) for i in range(90)]
        repo.get_metric_history.side_effect = _history_side_effect({
            ("onchain_analysis_snapshots", "total_net_gex", 720): history_30,
            ("onchain_analysis_snapshots", "total_net_gex", 2160): history_90,
        })
        repo.get_metric_history_oldest_timestamp.side_effect = RuntimeError("db timeout")
        service = _make_service(repository=repo)
        analyzer = _FakeAnalyzer(market_metrics={})
        result = _make_result()
        import dataclasses
        bundle = result.expirations[0]
        gex_dex = MagicMock()
        gex_dex.total_net_gex = 5_000_000.0
        result = dataclasses.replace(
            result,
            expirations=(dataclasses.replace(bundle, gex_dex=gex_dex),),
        )

        metrics, _, _ = service._build_normalized_metrics(analyzer, result)

        # net_gex must still be present -- the history fetch succeeded and
        # must not be discarded just because the (isolated) span lookup
        # failed. It falls back to span-unknown (None), i.e. the prior
        # count-only gate for this metric.
        assert "net_gex" in metrics
        net_gex = metrics["net_gex"]
        assert net_gex.n_30d == 30
        assert net_gex.span_days_30d is None
        assert net_gex.sufficient is True  # count-only fallback: n=30 >= MIN_OBS
