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

import math
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
# Expected movements tests
# ---------------------------------------------------------------------------

def test_market_metrics_expected_movements_rendered():
    """Expected daily/weekly/monthly moves appear in report when dvol is set."""
    report = _render_header(dvol=75.95, iv_percentile=92.6)
    assert "Expected Daily Move:" in report
    assert "Expected Weekly Move:" in report
    assert "Expected Monthly Move:" in report


def test_market_metrics_expected_movements_values():
    """Expected move dollar values are mathematically correct."""
    dvol = 80.0
    spot = UNDERLYING_PRICE
    report = _render_header(dvol=dvol, iv_percentile=50.0)

    expected_daily_dollar = dvol / 100 / math.sqrt(365) * spot
    expected_weekly_dollar = dvol / 100 / math.sqrt(52) * spot
    expected_monthly_dollar = dvol / 100 / math.sqrt(12) * spot

    # Check that rounded dollar values appear in the report
    assert f"${expected_daily_dollar:,.2f}" in report
    assert f"${expected_weekly_dollar:,.2f}" in report
    assert f"${expected_monthly_dollar:,.2f}" in report


def test_market_metrics_expected_movements_percent_values():
    """Expected move percentage values are mathematically correct."""
    dvol = 80.0
    report = _render_header(dvol=dvol, iv_percentile=50.0)

    daily_pct = dvol / 100 / math.sqrt(365) * 100
    weekly_pct = dvol / 100 / math.sqrt(52) * 100
    monthly_pct = dvol / 100 / math.sqrt(12) * 100

    assert f"{daily_pct:.1f}%" in report
    assert f"{weekly_pct:.1f}%" in report
    assert f"{monthly_pct:.1f}%" in report


def test_market_metrics_expected_movements_absent_without_dvol():
    """Expected move lines do NOT appear when dvol is None."""
    report = _render_header(dvol=None, iv_percentile=50.0)
    assert "Expected Daily Move:" not in report
    assert "Expected Weekly Move:" not in report
    assert "Expected Monthly Move:" not in report


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
    """IV Rank appears after IV Percentile; Expected moves appear after IV Rank."""
    report = _render_header(dvol=75.95, iv_percentile=92.6, iv_rank=78.4)

    pos_dvol = report.index("DVOL (Volatility Index)")
    pos_percentile = report.index("IV Percentile (365d)")
    pos_rank = report.index("IV Rank (365d)")
    pos_daily = report.index("Expected Daily Move:")

    assert pos_dvol < pos_percentile < pos_rank < pos_daily
