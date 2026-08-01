"""
Unit tests for OnChainAnalysisService._build_delta_flow_summary
(institutional_metrics_spec.md section 6 / task C7).

Verifies the report-time wiring: reads pre-aggregated flow_delta_hourly
rows via DatabaseRepository.get_delta_flow_summary (never recomputes BS
delta at report time), the "no data -> empty tuple" convention (no
repository, no rows in window, or a real DB error), that the returned
buckets round-trip into FlowBucket instances the formatter can consume,
and (review fix, Important #4) the coverage/staleness signal via
DatabaseRepository.get_delta_flow_coverage.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from coding.core.analytics.results.delta_flow_results import FlowBucket
from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService


def _make_service(repository):
    service = OnChainAnalysisService.__new__(OnChainAnalysisService)
    service.api = MagicMock()
    service.repository = repository
    return service


def _make_repo(summary_rows=None, coverage=None):
    """MagicMock repository with sane, non-MagicMock defaults for both
    delta-flow calls -- a bare MagicMock() return for get_delta_flow_coverage
    would blow up the `max_snapshot_hour < threshold` comparison inside
    _build_delta_flow_summary with a TypeError/incomparable-types error."""
    repo = MagicMock()
    repo.get_delta_flow_summary.return_value = summary_rows if summary_rows is not None else []
    repo.get_delta_flow_coverage.return_value = (
        coverage if coverage is not None else {"hours_present": 0, "max_snapshot_hour": None}
    )
    return repo


def _row(expiration, trade_count=5, skipped_count=0):
    return {
        "expiration": expiration, "hiro_usd": 100.0, "premium_usd": 10.0,
        "gross_delta_usd": 200.0, "net_contracts": 1.0, "gross_contracts": 2.0,
        "trade_count": trade_count, "buy_count": 3, "sell_count": 2,
        "skipped_count": skipped_count,
    }


class TestBuildDeltaFlowSummary:
    def test_no_repository_returns_empty_result(self):
        service = _make_service(repository=None)

        buckets, lookback_hours, hours_present, stale_since = service._build_delta_flow_summary("BTC")

        assert buckets == ()
        assert lookback_hours == service._DELTA_FLOW_LOOKBACK_HOURS
        assert hours_present == 0
        assert stale_since is None

    def test_no_rows_in_window_returns_empty_tuple(self):
        """flow_delta_hourly has no rows yet for this currency/window
        (feature just shipped, or the daemon hasn't run) -- never a
        fabricated summary."""
        repo = _make_repo(summary_rows=[])
        service = _make_service(repo)

        buckets, _, _, _ = service._build_delta_flow_summary("BTC")

        assert buckets == ()

    def test_repository_exception_returns_empty_result_not_raised(self):
        repo = _make_repo()
        repo.get_delta_flow_summary.side_effect = Exception("db boom")
        service = _make_service(repo)

        buckets, lookback_hours, hours_present, stale_since = service._build_delta_flow_summary("BTC")

        assert buckets == ()
        assert lookback_hours == service._DELTA_FLOW_LOOKBACK_HOURS
        assert hours_present == 0
        assert stale_since is None

    def test_rows_mapped_to_flow_bucket_tuple(self):
        repo = _make_repo(summary_rows=[_row("ALL", trade_count=7), _row("27MAR26", trade_count=7)])
        service = _make_service(repo)

        buckets, _, _, _ = service._build_delta_flow_summary("BTC")

        assert len(buckets) == 2
        assert all(isinstance(b, FlowBucket) for b in buckets)
        assert {b.expiration for b in buckets} == {"ALL", "27MAR26"}

    def test_since_window_matches_lookback_hours(self):
        repo = _make_repo()
        service = _make_service(repo)

        service._build_delta_flow_summary("BTC")

        call = repo.get_delta_flow_summary.call_args
        assert call.kwargs["currency"] == "BTC"
        # since is roughly now - 24h; just check it's a datetime, exact
        # value depends on wall clock at call time (not stubbed here).
        assert call.kwargs["since"] is not None

    def test_since_uses_utc_now_not_local_now(self):
        """
        Review fix, Important #2: flow_delta_hourly.snapshot_hour is
        written naive-UTC (VPS OS + DB timezone both confirmed UTC). A
        naive local `datetime.now()` here would silently shrink "24h" to
        22-23h on a non-UTC machine (this one is Europe/Berlin, UTC+1/+2).
        Fakes `datetime.now(tz=...)` to return a DIFFERENT value than bare
        `datetime.now()` so the two branches can't accidentally agree --
        proves the UTC branch is the one actually used.
        """
        repo = _make_repo()
        service = _make_service(repo)

        fixed_utc_now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
        fixed_local_now = datetime(2026, 8, 1, 13, 30, 0)  # deliberately different

        class _FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_utc_now if tz is not None else fixed_local_now

        with patch("coding.service.on_chain.on_chain_analysis_service.datetime", _FixedDateTime):
            service._build_delta_flow_summary("BTC")

        since = repo.get_delta_flow_summary.call_args.kwargs["since"]
        expected = fixed_utc_now.replace(tzinfo=None) - timedelta(hours=24)

        assert since == expected
        assert since.tzinfo is None  # naive, matching the naive-UTC DB column
        assert since != fixed_local_now - timedelta(hours=24)  # would be the bug's value

    def test_currency_passed_through(self):
        repo = _make_repo()
        service = _make_service(repo)

        service._build_delta_flow_summary("ETH")

        assert repo.get_delta_flow_summary.call_args.kwargs["currency"] == "ETH"
        assert repo.get_delta_flow_coverage.call_args.kwargs["currency"] == "ETH"


class TestDeltaFlowCoverageAndStaleness:
    """
    Review fix, Important #4: task-C7-brief.md explicitly named "a
    currency with a stale/lagging daemon" as a case to handle, and the
    original implementation only returned SUMs with no way to disclose a
    gap. These tests verify hours_present/stale_since round-trip correctly
    from DatabaseRepository.get_delta_flow_coverage.
    """

    def test_fresh_recent_hour_gives_no_staleness(self):
        repo = _make_repo(
            summary_rows=[_row("ALL")],
            coverage={"hours_present": 24, "max_snapshot_hour": datetime.now(timezone.utc).replace(tzinfo=None)},
        )
        service = _make_service(repo)

        _buckets, _lookback, hours_present, stale_since = service._build_delta_flow_summary("BTC")

        assert hours_present == 24
        assert stale_since is None

    def test_lagging_daemon_beyond_threshold_sets_stale_since(self):
        """A daemon down for 12h: max_snapshot_hour is far behind 'now' --
        must surface as stale_since, not silently disappear behind a
        confident-looking SUM over whatever partial rows landed."""
        now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        stale_hour = now_utc_naive - timedelta(hours=12)
        repo = _make_repo(
            summary_rows=[_row("ALL", trade_count=100)],
            coverage={"hours_present": 12, "max_snapshot_hour": stale_hour},
        )
        service = _make_service(repo)

        _buckets, _lookback, hours_present, stale_since = service._build_delta_flow_summary("BTC")

        assert hours_present == 12
        assert stale_since == stale_hour

    def test_gap_exactly_at_threshold_boundary_is_not_stale(self):
        """Mirrors _compute_historical_context_staleness's strict '<'
        comparison -- exactly at the threshold is still fresh, only
        STRICTLY older than the threshold counts as stale. Freezes "now"
        via a patched datetime class so the boundary can't drift into
        flakiness against real wall-clock microseconds elapsing between
        this test building `boundary_hour` and the implementation
        computing its own threshold."""
        fixed_utc_now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
        boundary_hour = fixed_utc_now.replace(tzinfo=None) - timedelta(hours=3)  # == threshold exactly
        repo = _make_repo(
            summary_rows=[_row("ALL")],
            coverage={"hours_present": 21, "max_snapshot_hour": boundary_hour},
        )
        service = _make_service(repo)

        class _FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_utc_now if tz is not None else fixed_utc_now.replace(tzinfo=None)

        with patch("coding.service.on_chain.on_chain_analysis_service.datetime", _FixedDateTime):
            _buckets, _lookback, _hours_present, stale_since = service._build_delta_flow_summary("BTC")

        assert stale_since is None

    def test_gap_just_past_threshold_is_stale(self):
        fixed_utc_now = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
        past_hour = fixed_utc_now.replace(tzinfo=None) - timedelta(hours=3, minutes=1)
        repo = _make_repo(
            summary_rows=[_row("ALL")],
            coverage={"hours_present": 20, "max_snapshot_hour": past_hour},
        )
        service = _make_service(repo)

        class _FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_utc_now if tz is not None else fixed_utc_now.replace(tzinfo=None)

        with patch("coding.service.on_chain.on_chain_analysis_service.datetime", _FixedDateTime):
            _buckets, _lookback, _hours_present, stale_since = service._build_delta_flow_summary("BTC")

        assert stale_since == past_hour

    def test_coverage_lookup_failure_does_not_raise_or_break_summary(self):
        """Own try/except -- a coverage-query failure must not take down
        the (already-successful) summary SUMs."""
        repo = _make_repo(summary_rows=[_row("ALL", trade_count=7)])
        repo.get_delta_flow_coverage.side_effect = Exception("coverage boom")
        service = _make_service(repo)

        buckets, _lookback, hours_present, stale_since = service._build_delta_flow_summary("BTC")

        assert len(buckets) == 1  # summary itself unaffected
        assert hours_present == 0
        assert stale_since is None

    def test_coverage_since_matches_summary_since(self):
        """Both calls must describe the SAME window -- otherwise
        hours_present could describe a different period than the totals
        it's meant to qualify."""
        repo = _make_repo()
        service = _make_service(repo)

        service._build_delta_flow_summary("BTC")

        summary_since = repo.get_delta_flow_summary.call_args.kwargs["since"]
        coverage_since = repo.get_delta_flow_coverage.call_args.kwargs["since"]
        assert summary_since == coverage_since
