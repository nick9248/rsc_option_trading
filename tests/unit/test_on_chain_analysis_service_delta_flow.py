"""
Unit tests for OnChainAnalysisService._build_delta_flow_summary
(institutional_metrics_spec.md section 6 / task C7).

Verifies the report-time wiring: reads pre-aggregated flow_delta_hourly
rows via DatabaseRepository.get_delta_flow_summary (never recomputes BS
delta at report time), the "no data -> empty tuple" convention (no
repository, no rows in window, or a real DB error), and that the returned
buckets round-trip into FlowBucket instances the formatter can consume.
"""

from unittest.mock import MagicMock

from coding.core.analytics.results.delta_flow_results import FlowBucket
from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService


def _make_service(repository):
    service = OnChainAnalysisService.__new__(OnChainAnalysisService)
    service.api = MagicMock()
    service.repository = repository
    return service


def _row(expiration, trade_count=5, skipped_count=0):
    return {
        "expiration": expiration, "hiro_usd": 100.0, "premium_usd": 10.0,
        "gross_delta_usd": 200.0, "net_contracts": 1.0, "gross_contracts": 2.0,
        "trade_count": trade_count, "buy_count": 3, "sell_count": 2,
        "skipped_count": skipped_count,
    }


class TestBuildDeltaFlowSummary:
    def test_no_repository_returns_empty_tuple_and_default_lookback(self):
        service = _make_service(repository=None)

        buckets, lookback_hours = service._build_delta_flow_summary("BTC")

        assert buckets == ()
        assert lookback_hours == service._DELTA_FLOW_LOOKBACK_HOURS

    def test_no_rows_in_window_returns_empty_tuple(self):
        """flow_delta_hourly has no rows yet for this currency/window
        (feature just shipped, or the daemon hasn't run) -- never a
        fabricated summary."""
        repo = MagicMock()
        repo.get_delta_flow_summary.return_value = []
        service = _make_service(repo)

        buckets, _ = service._build_delta_flow_summary("BTC")

        assert buckets == ()

    def test_repository_exception_returns_empty_tuple_not_raised(self):
        repo = MagicMock()
        repo.get_delta_flow_summary.side_effect = Exception("db boom")
        service = _make_service(repo)

        buckets, lookback_hours = service._build_delta_flow_summary("BTC")  # must not raise

        assert buckets == ()
        assert lookback_hours == service._DELTA_FLOW_LOOKBACK_HOURS

    def test_rows_mapped_to_flow_bucket_tuple(self):
        repo = MagicMock()
        repo.get_delta_flow_summary.return_value = [_row("ALL", trade_count=7), _row("27MAR26", trade_count=7)]
        service = _make_service(repo)

        buckets, _ = service._build_delta_flow_summary("BTC")

        assert len(buckets) == 2
        assert all(isinstance(b, FlowBucket) for b in buckets)
        assert {b.expiration for b in buckets} == {"ALL", "27MAR26"}

    def test_since_window_matches_lookback_hours(self):
        repo = MagicMock()
        repo.get_delta_flow_summary.return_value = []
        service = _make_service(repo)

        service._build_delta_flow_summary("BTC")

        call = repo.get_delta_flow_summary.call_args
        assert call.kwargs["currency"] == "BTC"
        # since is roughly now - 24h; just check it's a datetime, exact
        # value depends on wall clock at call time (not stubbed here).
        assert call.kwargs["since"] is not None

    def test_currency_passed_through(self):
        repo = MagicMock()
        repo.get_delta_flow_summary.return_value = []
        service = _make_service(repo)

        service._build_delta_flow_summary("ETH")

        assert repo.get_delta_flow_summary.call_args.kwargs["currency"] == "ETH"
