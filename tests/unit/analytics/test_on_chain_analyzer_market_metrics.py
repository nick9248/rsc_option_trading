"""
Unit tests for OnChainAnalyzer market metrics rendering.

Tests IV Rank (52w) and expected daily/weekly/monthly move display
in the generated report.
"""

import math
import pytest

from coding.core.analytics.on_chain_analyzer import OnChainAnalyzer


@pytest.fixture
def sample_analyzer():
    """Create a minimal OnChainAnalyzer with a known spot price."""
    analyzer = OnChainAnalyzer(data=[], currency="BTC")
    analyzer.underlying_price = 95000.0
    return analyzer


# ---------------------------------------------------------------------------
# IV Rank tests
# ---------------------------------------------------------------------------

def test_market_metrics_iv_rank_rendered(sample_analyzer):
    """IV Rank appears in report when set."""
    sample_analyzer.set_market_metrics(dvol=75.95, iv_percentile=92.6, iv_rank=78.4)
    report = sample_analyzer.generate_report()
    assert "IV Rank (52w): 78.4%" in report


def test_market_metrics_iv_rank_none_skipped(sample_analyzer):
    """No IV Rank line when iv_rank is None."""
    sample_analyzer.set_market_metrics(dvol=75.95, iv_percentile=92.6, iv_rank=None)
    report = sample_analyzer.generate_report()
    assert "IV Rank" not in report


def test_market_metrics_iv_rank_zero(sample_analyzer):
    """IV Rank of 0.0 is rendered (not skipped as falsy)."""
    sample_analyzer.set_market_metrics(dvol=75.95, iv_percentile=10.0, iv_rank=0.0)
    report = sample_analyzer.generate_report()
    assert "IV Rank (52w): 0.0%" in report


def test_market_metrics_iv_rank_100(sample_analyzer):
    """IV Rank of 100.0 is rendered correctly."""
    sample_analyzer.set_market_metrics(dvol=75.95, iv_percentile=99.9, iv_rank=100.0)
    report = sample_analyzer.generate_report()
    assert "IV Rank (52w): 100.0%" in report


# ---------------------------------------------------------------------------
# Expected movements tests
# ---------------------------------------------------------------------------

def test_market_metrics_expected_movements_rendered(sample_analyzer):
    """Expected daily/weekly/monthly moves appear in report when dvol is set."""
    sample_analyzer.set_market_metrics(dvol=75.95, iv_percentile=92.6)
    report = sample_analyzer.generate_report()
    assert "Expected Daily Move:" in report
    assert "Expected Weekly Move:" in report
    assert "Expected Monthly Move:" in report


def test_market_metrics_expected_movements_values(sample_analyzer):
    """Expected move dollar values are mathematically correct."""
    dvol = 80.0
    spot = 95000.0
    sample_analyzer.set_market_metrics(dvol=dvol, iv_percentile=50.0)
    report = sample_analyzer.generate_report()

    expected_daily_dollar = dvol / 100 / math.sqrt(365) * spot
    expected_weekly_dollar = dvol / 100 / math.sqrt(52) * spot
    expected_monthly_dollar = dvol / 100 / math.sqrt(12) * spot

    # Check that rounded dollar values appear in the report
    assert f"${expected_daily_dollar:,.2f}" in report
    assert f"${expected_weekly_dollar:,.2f}" in report
    assert f"${expected_monthly_dollar:,.2f}" in report


def test_market_metrics_expected_movements_percent_values(sample_analyzer):
    """Expected move percentage values are mathematically correct."""
    dvol = 80.0
    spot = 95000.0
    sample_analyzer.set_market_metrics(dvol=dvol, iv_percentile=50.0)
    report = sample_analyzer.generate_report()

    daily_pct = dvol / 100 / math.sqrt(365) * 100
    weekly_pct = dvol / 100 / math.sqrt(52) * 100
    monthly_pct = dvol / 100 / math.sqrt(12) * 100

    assert f"{daily_pct:.1f}%" in report
    assert f"{weekly_pct:.1f}%" in report
    assert f"{monthly_pct:.1f}%" in report


def test_market_metrics_expected_movements_absent_without_dvol(sample_analyzer):
    """Expected move lines do NOT appear when dvol is None."""
    sample_analyzer.set_market_metrics(dvol=None, iv_percentile=50.0)
    report = sample_analyzer.generate_report()
    assert "Expected Daily Move:" not in report
    assert "Expected Weekly Move:" not in report
    assert "Expected Monthly Move:" not in report


# ---------------------------------------------------------------------------
# Existing fields unchanged
# ---------------------------------------------------------------------------

def test_market_metrics_dvol_still_rendered(sample_analyzer):
    """DVOL line still appears alongside new fields."""
    sample_analyzer.set_market_metrics(dvol=75.95, iv_percentile=92.6, iv_rank=78.4)
    report = sample_analyzer.generate_report()
    assert "DVOL (Volatility Index): 75.95" in report


def test_market_metrics_iv_percentile_still_rendered(sample_analyzer):
    """IV Percentile line still appears alongside new fields."""
    sample_analyzer.set_market_metrics(dvol=75.95, iv_percentile=92.6, iv_rank=78.4)
    report = sample_analyzer.generate_report()
    assert "IV Percentile (365d): 92.6%" in report


def test_market_metrics_order_in_report(sample_analyzer):
    """IV Rank appears after IV Percentile; Expected moves appear after IV Rank."""
    sample_analyzer.set_market_metrics(dvol=75.95, iv_percentile=92.6, iv_rank=78.4)
    report = sample_analyzer.generate_report()

    pos_dvol = report.index("DVOL (Volatility Index)")
    pos_percentile = report.index("IV Percentile (365d)")
    pos_rank = report.index("IV Rank (52w)")
    pos_daily = report.index("Expected Daily Move:")

    assert pos_dvol < pos_percentile < pos_rank < pos_daily
