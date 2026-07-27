"""
Unit tests for 1-day trend comparison in the on-chain analysis report.

Covers Max Pain, P/C Ratio, and Volume trend lines.

refactor_design_spec.md section T10: OnChainAnalyzer.generate_report(),
_format_trend(), and set_trend_data() (plus the trend_data dict they
supported) are all deleted. These tests, which previously drove report
generation through set_trend_data() + generate_report(), are rewritten
against format_trend_delta() (the extracted, still-live equivalent of
_format_trend) and format_expiration_section(analysis, spot_price, trend)
directly, per the section 3 compat table's planned replacement for this
file. Trend data is now expressed as a TrendSnapshot (the typed model
set_trend_data used to feed) instead of a raw dict.
"""

import pytest

from coding.core.analytics.on_chain_analyzer import OnChainMetricsCalculator
from coding.core.analytics.reporting.expiry_formatter import (
    format_expiration_section,
    format_trend_delta,
)
from coding.core.analytics.results.analysis_result import TrendSnapshot

EXPIRATION = "10MAR26"


def _make_instruments(expiration: str = EXPIRATION):
    """Return a minimal list of book-summary-style dicts for one expiration."""
    return [
        {
            "instrument_name": f"ETH-{expiration}-2000-C",
            "open_interest": 500,
            "volume": 100,
            "volume_usd": 200000,
            "mark_price": 0.05,
            "mark_iv": 80.0,
            "underlying_price": 1900.0,
        },
        {
            "instrument_name": f"ETH-{expiration}-2000-P",
            "open_interest": 800,
            "volume": 150,
            "volume_usd": 300000,
            "mark_price": 0.08,
            "mark_iv": 85.0,
            "underlying_price": 1900.0,
        },
        {
            "instrument_name": f"ETH-{expiration}-1800-P",
            "open_interest": 300,
            "volume": 50,
            "volume_usd": 90000,
            "mark_price": 0.02,
            "mark_iv": 90.0,
            "underlying_price": 1900.0,
        },
    ]


@pytest.fixture
def analyzer_with_data():
    """OnChainMetricsCalculator with one expiration (10MAR26) pre-parsed."""
    data = _make_instruments()
    a = OnChainMetricsCalculator(data=data, currency="ETH")
    a.parse_instruments()
    return a


def _render(analyzer, trend=None):
    """Render the expiration section for EXPIRATION with the given trend."""
    analysis = analyzer.analyze_expiration(EXPIRATION)
    return format_expiration_section(analysis, analyzer.underlying_price, trend)


# ---------------------------------------------------------------------------
# format_trend_delta (extracted from the deleted _format_trend)
# ---------------------------------------------------------------------------

def test_format_trend_returns_empty_when_previous_none():
    """format_trend_delta returns empty string when previous is None."""
    result = format_trend_delta(2000.0, None)
    assert result == ""


def test_format_trend_unchanged():
    """format_trend_delta returns unchanged marker when values are equal."""
    result = format_trend_delta(2000.0, 2000.0)
    assert "unchanged" in result


def test_format_trend_up_integer():
    """format_trend_delta shows up arrow and delta for increase (integer mode)."""
    result = format_trend_delta(2100.0, 1900.0)
    assert "↑" in result
    assert "1,900" in result
    assert "+200" in result


def test_format_trend_down_integer():
    """format_trend_delta shows down arrow and delta for decrease (integer mode)."""
    result = format_trend_delta(1800.0, 2000.0)
    assert "↓" in result
    assert "2,000" in result
    assert "-200" in result


def test_format_trend_ratio_mode():
    """format_trend_delta uses 2 decimal places in ratio mode."""
    result = format_trend_delta(1.59, 1.42, is_ratio=True)
    assert "↑" in result
    assert "1.42" in result
    assert "+0.17" in result


# ---------------------------------------------------------------------------
# Report output: Max Pain trend
# ---------------------------------------------------------------------------

def test_trend_max_pain_shown_when_set(analyzer_with_data):
    """Trend line appears for Max Pain when trend data has a prior value."""
    trend = TrendSnapshot(
        max_pain_strike=1900.0, call_oi=None, put_oi=None,
        pc_ratio=None, total_volume=None, volume_ratio=None,
    )
    report = _render(analyzer_with_data, trend)
    assert "Trend (Max Pain):" in report


def test_trend_skipped_when_no_data(analyzer_with_data):
    """No trend lines when trend is None."""
    report = _render(analyzer_with_data, None)
    assert "Trend (Max Pain):" not in report


def test_trend_graceful_when_none(analyzer_with_data):
    """No crash when trend is None; report still renders."""
    report = _render(analyzer_with_data, None)
    assert "Max Pain Strike:" in report
    assert "Trend (Max Pain):" not in report


def test_trend_max_pain_unchanged_label(analyzer_with_data):
    """When current and previous max pain are identical, 'unchanged' appears."""
    analysis = analyzer_with_data.analyze_expiration(EXPIRATION)
    mp = analysis.max_pain.max_pain_strike
    trend = TrendSnapshot(
        max_pain_strike=mp, call_oi=None, put_oi=None,
        pc_ratio=None, total_volume=None, volume_ratio=None,
    )
    report = _render(analyzer_with_data, trend)
    assert "→ unchanged" in report


def test_trend_max_pain_up_arrow(analyzer_with_data):
    """Up arrow appears when current max pain is higher than previous."""
    analysis = analyzer_with_data.analyze_expiration(EXPIRATION)
    mp = analysis.max_pain.max_pain_strike
    trend = TrendSnapshot(
        max_pain_strike=mp - 100.0, call_oi=None, put_oi=None,
        pc_ratio=None, total_volume=None, volume_ratio=None,
    )
    report = _render(analyzer_with_data, trend)
    assert "↑" in report


def test_trend_max_pain_down_arrow(analyzer_with_data):
    """Down arrow appears when current max pain is lower than previous."""
    analysis = analyzer_with_data.analyze_expiration(EXPIRATION)
    mp = analysis.max_pain.max_pain_strike
    trend = TrendSnapshot(
        max_pain_strike=mp + 100.0, call_oi=None, put_oi=None,
        pc_ratio=None, total_volume=None, volume_ratio=None,
    )
    report = _render(analyzer_with_data, trend)
    assert "↓" in report


# ---------------------------------------------------------------------------
# Report output: Volume trend
# ---------------------------------------------------------------------------

def test_trend_volume_shown_when_set(analyzer_with_data):
    """Trend line appears for Volume when trend data has a prior value."""
    trend = TrendSnapshot(
        max_pain_strike=None, call_oi=None, put_oi=None,
        pc_ratio=None, total_volume=6000.0, volume_ratio=None,
    )
    report = _render(analyzer_with_data, trend)
    assert "Trend (Volume):" in report


def test_trend_volume_not_shown_without_trend_data(analyzer_with_data):
    """No volume trend line when trend is None."""
    report = _render(analyzer_with_data, None)
    assert "Trend (Volume):" not in report


def test_trend_vol_pc_shown_when_ratio_set(analyzer_with_data):
    """Trend (Vol P/C) line appears when volume_ratio is present."""
    trend = TrendSnapshot(
        max_pain_strike=None, call_oi=None, put_oi=None,
        pc_ratio=None, total_volume=200.0, volume_ratio=1.20,
    )
    report = _render(analyzer_with_data, trend)
    assert "Trend (Vol P/C):" in report


# ---------------------------------------------------------------------------
# Report output: P/C Ratio trend
# ---------------------------------------------------------------------------

def test_trend_pc_ratio_shown_when_set(analyzer_with_data):
    """Trend line appears for P/C Ratio when trend data has a prior value."""
    trend = TrendSnapshot(
        max_pain_strike=None, call_oi=None, put_oi=None,
        pc_ratio=1.20, total_volume=None, volume_ratio=None,
    )
    report = _render(analyzer_with_data, trend)
    assert "Trend (P/C):" in report


def test_trend_call_oi_shown_when_set(analyzer_with_data):
    """Trend (Call OI) line appears when call_oi/put_oi are present."""
    trend = TrendSnapshot(
        max_pain_strike=None, call_oi=400.0, put_oi=700.0,
        pc_ratio=None, total_volume=None, volume_ratio=None,
    )
    report = _render(analyzer_with_data, trend)
    assert "Trend (Call OI):" in report
    assert "Trend (Put OI):" in report


def test_trend_pc_ratio_not_shown_without_trend_data(analyzer_with_data):
    """No P/C trend line when trend is None."""
    report = _render(analyzer_with_data, None)
    assert "Trend (P/C):" not in report


# ---------------------------------------------------------------------------
# Partial data: only some fields present
# ---------------------------------------------------------------------------

def test_trend_partial_data_only_max_pain(analyzer_with_data):
    """When only max_pain_strike is set, only that trend appears."""
    trend = TrendSnapshot(
        max_pain_strike=1800.0, call_oi=None, put_oi=None,
        pc_ratio=None, total_volume=None, volume_ratio=None,
    )
    report = _render(analyzer_with_data, trend)
    assert "Trend (Max Pain):" in report
    assert "Trend (Volume):" not in report
    assert "Trend (P/C):" not in report


def test_trend_partial_data_only_volume(analyzer_with_data):
    """When only total_volume is set, only volume trend appears."""
    trend = TrendSnapshot(
        max_pain_strike=None, call_oi=None, put_oi=None,
        pc_ratio=None, total_volume=5000.0, volume_ratio=None,
    )
    report = _render(analyzer_with_data, trend)
    assert "Trend (Volume):" in report
    assert "Trend (Max Pain):" not in report
    assert "Trend (P/C):" not in report


def test_trend_partial_data_only_oi(analyzer_with_data):
    """When only call_oi/put_oi are present, OI trends appear but not P/C trend."""
    trend = TrendSnapshot(
        max_pain_strike=None, call_oi=500.0, put_oi=900.0,
        pc_ratio=None, total_volume=None, volume_ratio=None,
    )
    report = _render(analyzer_with_data, trend)
    assert "Trend (Call OI):" in report
    assert "Trend (Put OI):" in report
    # pc_ratio not set so P/C trend should not appear
    assert "Trend (P/C):" not in report
