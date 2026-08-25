"""
Regression test for OnChainAnalysisService._fetch_market_metrics's IV Rank
computation (Wave H Task H-F, Fix 4).

Confirmed bug this fixes: when the 365d DVOL high/low range is degenerate
(dvol_max == dvol_min -- e.g. exactly one observation, or a genuinely flat
series), iv_rank was fabricated as 50.0. That value renders in the report
as "IV Rank (365d): 50.0%", indistinguishable from a real, computed median
rank -- a reader has no way to tell "50.0% from 200 observations" (fine)
apart from "50.0% from 1 observation" (not fine). The observation count
(len(close_values)) was already computed and logged to progress_callback
but never carried into market_metrics/the report.

Fix: the degenerate branch now sets iv_rank = None (report_formatter
already gates the "IV Rank" line on `is not None`, so this line is simply
omitted rather than showing a fabricated number), and the observation
count is now threaded through analyzer.market_metrics /
MarketMetricsResult.iv_rank_observation_count so a non-degenerate rank
can also be disclosed with its sample size.
"""
from unittest.mock import MagicMock

from coding.core.analytics.on_chain_analyzer import OnChainMetricsCalculator
from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService


def _make_service(dvol_data):
    service = OnChainAnalysisService.__new__(OnChainAnalysisService)
    service.api = MagicMock()
    service.api.get_volatility_index_data.return_value = dvol_data
    service.api.get_ticker.return_value = {}  # no funding data -- irrelevant to this test
    service.repository = None
    return service


def _make_analyzer():
    return OnChainMetricsCalculator([], "BTC")


def _noop_progress(_message):
    pass


class TestIvRankDegenerateRange:
    def test_single_observation_does_not_fabricate_fifty(self):
        """Exactly one DVOL data point: high == low == close for the only
        row, so dvol_max == dvol_min. Must NOT produce iv_rank == 50.0."""
        dvol_data = {
            "data": [
                [1_700_000_000_000, 60.0, 60.0, 60.0, 60.0],  # [ts, open, high, low, close]
            ]
        }
        service = _make_service(dvol_data)
        analyzer = _make_analyzer()

        service._fetch_market_metrics(analyzer, _noop_progress)

        assert analyzer.market_metrics["iv_rank"] is None
        assert analyzer.market_metrics["iv_rank_observation_count"] == 1

    def test_flat_series_does_not_fabricate_fifty(self):
        """A genuinely flat multi-day series (every high/low identical)
        is just as degenerate as a single observation -- same fix applies."""
        dvol_data = {
            "data": [
                [1_700_000_000_000 + i * 86_400_000, 55.0, 55.0, 55.0, 55.0]
                for i in range(30)
            ]
        }
        service = _make_service(dvol_data)
        analyzer = _make_analyzer()

        service._fetch_market_metrics(analyzer, _noop_progress)

        assert analyzer.market_metrics["iv_rank"] is None
        assert analyzer.market_metrics["iv_rank_observation_count"] == 30

    def test_non_degenerate_range_still_computes_real_rank_and_count(self):
        """Sanity check the opposite direction: a real spread must still
        produce a genuine, non-fabricated rank AND disclose its sample
        size -- the fix must not suppress the normal case."""
        rows = []
        for i in range(10):
            high = 50.0 + i  # 50..59
            low = 40.0 + i   # 40..49
            close = 45.0 + i
            rows.append([1_700_000_000_000 + i * 86_400_000, low, high, low, close])
        dvol_data = {"data": rows}
        service = _make_service(dvol_data)
        analyzer = _make_analyzer()

        service._fetch_market_metrics(analyzer, _noop_progress)

        dvol_min = 40.0
        dvol_max = 59.0
        last_close = 54.0
        expected_rank = (last_close - dvol_min) / (dvol_max - dvol_min) * 100

        assert analyzer.market_metrics["iv_rank"] is not None
        assert analyzer.market_metrics["iv_rank"] != 50.0 or abs(expected_rank - 50.0) < 1e-9
        assert analyzer.market_metrics["iv_rank"] == expected_rank
        assert analyzer.market_metrics["iv_rank_observation_count"] == 10


class TestIvRankReportRendering:
    """End-to-end: the degenerate-range None must actually suppress the
    'IV Rank' report line (report_formatter's existing `is not None` gate),
    and a real rank must render with its observation count disclosed."""

    def test_degenerate_range_omits_iv_rank_line_from_report(self):
        from datetime import datetime

        from coding.core.analytics.reporting.report_formatter import OnChainReportFormatter
        from coding.core.analytics.results.analysis_result import MarketMetricsResult

        formatter = OnChainReportFormatter()
        metrics = MarketMetricsResult(
            dvol=60.0, iv_percentile=50.0, iv_rank=None,
            current_funding=None, funding_8h=None,
            iv_rank_observation_count=1,
        )
        report = formatter.render_header(
            "BTC", 60000.0, datetime(2026, 8, 22), metrics,
        )
        assert "IV Rank" not in report

    def test_real_rank_renders_with_observation_count(self):
        from datetime import datetime

        from coding.core.analytics.reporting.report_formatter import OnChainReportFormatter
        from coding.core.analytics.results.analysis_result import MarketMetricsResult

        formatter = OnChainReportFormatter()
        metrics = MarketMetricsResult(
            dvol=60.0, iv_percentile=50.0, iv_rank=73.4,
            current_funding=None, funding_8h=None,
            iv_rank_observation_count=247,
        )
        report = formatter.render_header(
            "BTC", 60000.0, datetime(2026, 8, 22), metrics,
        )
        assert "IV Rank (365d): 73.4%" in report
        assert "247" in report
