"""
Unit tests for 1-day trend comparison in the on-chain analysis report.

Covers Max Pain, P/C Ratio, and Volume trend lines added via set_trend_data().
"""

import pytest

from coding.core.analytics.on_chain_analyzer import OnChainAnalyzer


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
    """OnChainAnalyzer with one expiration (10MAR26) pre-parsed."""
    data = _make_instruments()
    a = OnChainAnalyzer(data=data, currency="ETH")
    a.parse_instruments()
    return a


# ---------------------------------------------------------------------------
# set_trend_data / _format_trend basics
# ---------------------------------------------------------------------------

def test_trend_data_initially_empty(analyzer_with_data):
    """trend_data dict starts empty before any set_trend_data call."""
    assert analyzer_with_data.trend_data == {}


def test_set_trend_data_stores_value(analyzer_with_data):
    """set_trend_data stores the provided dict under the expiration key."""
    analyzer_with_data.set_trend_data(EXPIRATION, {"max_pain_strike": 1900.0})
    assert analyzer_with_data.trend_data[EXPIRATION]["max_pain_strike"] == 1900.0


def test_set_trend_data_accepts_none(analyzer_with_data):
    """set_trend_data accepts None without raising."""
    analyzer_with_data.set_trend_data(EXPIRATION, None)
    assert analyzer_with_data.trend_data[EXPIRATION] is None


# ---------------------------------------------------------------------------
# _format_trend helper
# ---------------------------------------------------------------------------

def test_format_trend_returns_empty_when_previous_none(analyzer_with_data):
    """_format_trend returns empty string when previous is None."""
    result = analyzer_with_data._format_trend(2000.0, None)
    assert result == ""


def test_format_trend_unchanged(analyzer_with_data):
    """_format_trend returns unchanged marker when values are equal."""
    result = analyzer_with_data._format_trend(2000.0, 2000.0)
    assert "unchanged" in result


def test_format_trend_up_integer(analyzer_with_data):
    """_format_trend shows up arrow and delta for increase (integer mode)."""
    result = analyzer_with_data._format_trend(2100.0, 1900.0)
    assert "↑" in result
    assert "1,900" in result
    assert "+200" in result


def test_format_trend_down_integer(analyzer_with_data):
    """_format_trend shows down arrow and delta for decrease (integer mode)."""
    result = analyzer_with_data._format_trend(1800.0, 2000.0)
    assert "↓" in result
    assert "2,000" in result
    assert "-200" in result


def test_format_trend_ratio_mode(analyzer_with_data):
    """_format_trend uses 2 decimal places in ratio mode."""
    result = analyzer_with_data._format_trend(1.59, 1.42, is_ratio=True)
    assert "↑" in result
    assert "1.42" in result
    assert "+0.17" in result


# ---------------------------------------------------------------------------
# Report output: Max Pain trend
# ---------------------------------------------------------------------------

def test_trend_max_pain_shown_when_set(analyzer_with_data):
    """Trend line appears for Max Pain when trend_data has prior value."""
    analyzer_with_data.set_trend_data(EXPIRATION, {"max_pain_strike": 1900.0})
    report = analyzer_with_data.generate_report()
    assert "Trend (Max Pain):" in report


def test_trend_skipped_when_no_data(analyzer_with_data):
    """No trend lines when trend_data not set for expiration."""
    report = analyzer_with_data.generate_report()
    assert "Trend (Max Pain):" not in report


def test_trend_graceful_when_none(analyzer_with_data):
    """No crash when set_trend_data called with None; report still renders."""
    analyzer_with_data.set_trend_data(EXPIRATION, None)
    report = analyzer_with_data.generate_report()
    assert "Max Pain Strike:" in report
    assert "Trend (Max Pain):" not in report


def test_trend_max_pain_unchanged_label(analyzer_with_data):
    """When current and previous max pain are identical, 'unchanged' appears."""
    # To know the calculated max pain we need to run the analysis first
    analysis = analyzer_with_data.analyze_expiration(EXPIRATION)
    mp = analysis["max_pain"]["max_pain_strike"]
    analyzer_with_data.set_trend_data(EXPIRATION, {"max_pain_strike": mp})
    report = analyzer_with_data.generate_report()
    assert "→ unchanged" in report


def test_trend_max_pain_up_arrow(analyzer_with_data):
    """Up arrow appears when current max pain is higher than previous."""
    analysis = analyzer_with_data.analyze_expiration(EXPIRATION)
    mp = analysis["max_pain"]["max_pain_strike"]
    analyzer_with_data.set_trend_data(EXPIRATION, {"max_pain_strike": mp - 100.0})
    report = analyzer_with_data.generate_report()
    assert "↑" in report


def test_trend_max_pain_down_arrow(analyzer_with_data):
    """Down arrow appears when current max pain is lower than previous."""
    analysis = analyzer_with_data.analyze_expiration(EXPIRATION)
    mp = analysis["max_pain"]["max_pain_strike"]
    analyzer_with_data.set_trend_data(EXPIRATION, {"max_pain_strike": mp + 100.0})
    report = analyzer_with_data.generate_report()
    assert "↓" in report


# ---------------------------------------------------------------------------
# Report output: Volume trend
# ---------------------------------------------------------------------------

def test_trend_volume_shown_when_set(analyzer_with_data):
    """Trend line appears for Volume when trend_data has prior value."""
    analyzer_with_data.set_trend_data(EXPIRATION, {"total_volume": 6000.0})
    report = analyzer_with_data.generate_report()
    assert "Trend (Volume):" in report


def test_trend_volume_not_shown_without_trend_data(analyzer_with_data):
    """No volume trend line when trend_data not set."""
    report = analyzer_with_data.generate_report()
    assert "Trend (Volume):" not in report


def test_trend_vol_pc_shown_when_ratio_set(analyzer_with_data):
    """Trend (Vol P/C) line appears when volume_ratio is in trend_data."""
    analyzer_with_data.set_trend_data(
        EXPIRATION, {"total_volume": 200.0, "volume_ratio": 1.20}
    )
    report = analyzer_with_data.generate_report()
    assert "Trend (Vol P/C):" in report


# ---------------------------------------------------------------------------
# Report output: P/C Ratio trend
# ---------------------------------------------------------------------------

def test_trend_pc_ratio_shown_when_set(analyzer_with_data):
    """Trend line appears for P/C Ratio when trend_data has prior value."""
    analyzer_with_data.set_trend_data(EXPIRATION, {"pc_ratio": 1.20})
    report = analyzer_with_data.generate_report()
    assert "Trend (P/C):" in report


def test_trend_call_oi_shown_when_set(analyzer_with_data):
    """Trend (Call OI) line appears when call_oi is in trend_data."""
    analyzer_with_data.set_trend_data(
        EXPIRATION, {"call_oi": 400.0, "put_oi": 700.0}
    )
    report = analyzer_with_data.generate_report()
    assert "Trend (Call OI):" in report
    assert "Trend (Put OI):" in report


def test_trend_pc_ratio_not_shown_without_trend_data(analyzer_with_data):
    """No P/C trend line when trend_data not set."""
    report = analyzer_with_data.generate_report()
    assert "Trend (P/C):" not in report


# ---------------------------------------------------------------------------
# Partial data: only some keys present
# ---------------------------------------------------------------------------

def test_trend_partial_data_only_max_pain(analyzer_with_data):
    """When only max_pain_strike is in trend_data, only that trend appears."""
    analyzer_with_data.set_trend_data(EXPIRATION, {"max_pain_strike": 1800.0})
    report = analyzer_with_data.generate_report()
    assert "Trend (Max Pain):" in report
    assert "Trend (Volume):" not in report
    assert "Trend (P/C):" not in report


def test_trend_partial_data_only_volume(analyzer_with_data):
    """When only total_volume is in trend_data, only volume trend appears."""
    analyzer_with_data.set_trend_data(EXPIRATION, {"total_volume": 5000.0})
    report = analyzer_with_data.generate_report()
    assert "Trend (Volume):" in report
    assert "Trend (Max Pain):" not in report
    assert "Trend (P/C):" not in report


def test_trend_partial_data_only_oi(analyzer_with_data):
    """When only call_oi/put_oi present, OI trends appear but not P/C trend."""
    analyzer_with_data.set_trend_data(
        EXPIRATION, {"call_oi": 500.0, "put_oi": 900.0}
    )
    report = analyzer_with_data.generate_report()
    assert "Trend (Call OI):" in report
    assert "Trend (Put OI):" in report
    # pc_ratio not in trend_data so P/C trend should not appear
    assert "Trend (P/C):" not in report
