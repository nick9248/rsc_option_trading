"""
Unit tests for on-chain market metrics rendering.

Tests IV Rank (365d) and expected daily/weekly/monthly move display in the
report header.

refactor_design_spec.md section T10: OnChainAnalyzer.generate_report() (and
its supporting setters) are deleted -- these tests, which previously drove
report generation through set_market_metrics() + generate_report(), are
rewritten against OnChainReportFormatter.render_header(result) directly
(the section 3 compat table's planned replacement for this file).
"""

from datetime import datetime

import pytest

from coding.core.analytics.reporting.report_formatter import OnChainReportFormatter
from coding.core.analytics.results.analysis_result import MarketMetricsResult

GENERATED_AT = datetime(2026, 1, 1)
UNDERLYING_PRICE = 95000.0


def _render_header(
    dvol=None,
    iv_percentile=None,
    iv_rank=None,
    current_funding=None,
    funding_8h=None,
) -> str:
    metrics = MarketMetricsResult(
        dvol=dvol, iv_percentile=iv_percentile, iv_rank=iv_rank,
        current_funding=current_funding, funding_8h=funding_8h,
    )
    return OnChainReportFormatter().render_header("BTC", UNDERLYING_PRICE, GENERATED_AT, metrics)


# ---------------------------------------------------------------------------
# IV Rank tests
# ---------------------------------------------------------------------------

def test_market_metrics_iv_rank_rendered():
    """IV Rank appears in report when set."""
    report = _render_header(dvol=75.95, iv_percentile=92.6, iv_rank=78.4)
    assert "IV Rank (365d): 78.4%" in report


def test_market_metrics_iv_rank_none_skipped():
    """No IV Rank line when iv_rank is None."""
    report = _render_header(dvol=75.95, iv_percentile=92.6, iv_rank=None)
    assert "IV Rank" not in report


def test_market_metrics_iv_rank_zero():
    """IV Rank of 0.0 is rendered (not skipped as falsy)."""
    report = _render_header(dvol=75.95, iv_percentile=10.0, iv_rank=0.0)
    assert "IV Rank (365d): 0.0%" in report


def test_market_metrics_iv_rank_100():
    """IV Rank of 100.0 is rendered correctly."""
    report = _render_header(dvol=75.95, iv_percentile=99.9, iv_rank=100.0)
    assert "IV Rank (365d): 100.0%" in report


# ---------------------------------------------------------------------------
# Expected movements: institutional_metrics_spec.md section 9 (Task D2)
# deletes the header's three-line $+% breakdown -- its one-line integer-
# dollar replacement (market_wide_formatter.format_expected_move_line)
# renders in the market-wide CONTEXT block instead, not the header. See
# tests/unit/analytics/reporting/test_market_wide_formatter.py's
# test_expected_move_line_* tests for the new one-liner's coverage.
# ---------------------------------------------------------------------------

def test_market_metrics_expected_movements_never_in_header():
    """Expected move lines never appear in the header, with or without dvol."""
    report_with_dvol = _render_header(dvol=75.95, iv_percentile=92.6)
    report_without_dvol = _render_header(dvol=None, iv_percentile=50.0)
    for report in (report_with_dvol, report_without_dvol):
        assert "Expected Daily Move:" not in report
        assert "Expected Weekly Move:" not in report
        assert "Expected Monthly Move:" not in report
        assert "Expected Move:" not in report


# ---------------------------------------------------------------------------
# Existing fields unchanged
# ---------------------------------------------------------------------------

def test_market_metrics_dvol_still_rendered():
    """DVOL line still appears alongside new fields."""
    report = _render_header(dvol=75.95, iv_percentile=92.6, iv_rank=78.4)
    assert "DVOL (Volatility Index): 75.95" in report


def test_market_metrics_iv_percentile_still_rendered():
    """IV Percentile line still appears alongside new fields."""
    report = _render_header(dvol=75.95, iv_percentile=92.6, iv_rank=78.4)
    assert "IV Percentile (365d): 92.6%" in report


def test_market_metrics_order_in_report():
    """IV Rank appears after IV Percentile."""
    report = _render_header(dvol=75.95, iv_percentile=92.6, iv_rank=78.4)

    pos_dvol = report.index("DVOL (Volatility Index)")
    pos_percentile = report.index("IV Percentile (365d)")
    pos_rank = report.index("IV Rank (365d)")

    assert pos_dvol < pos_percentile < pos_rank
