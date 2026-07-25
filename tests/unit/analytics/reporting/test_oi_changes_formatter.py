"""
Unit tests for coding.core.analytics.reporting.oi_changes_formatter
(refactor_design_spec.md section T3).
"""

from coding.core.analytics.reporting.oi_changes_formatter import (
    format_iv_percentile_section,
    format_oi_changes_section,
)
from coding.core.analytics.results.analysis_result import (
    IvPercentileResult,
    OiChangeRow,
    OiChangesResult,
)


def test_oi_changes_no_significant_changes():
    result = OiChangesResult(rows=(), total_significant=0, has_previous_snapshot=True)
    text = format_oi_changes_section(result)
    assert "LARGE OI CHANGES (Day-over-Day)" in text
    assert "No significant OI changes (>20%) detected" in text


def test_oi_changes_rendered_call_and_put():
    rows = (
        OiChangeRow(strike=95000.0, option_type="C", previous_oi=100.0, current_oi=150.0, change=50.0, change_pct=50.0),
        OiChangeRow(strike=90000.0, option_type="P", previous_oi=200.0, current_oi=100.0, change=-100.0, change_pct=-50.0),
    )
    result = OiChangesResult(rows=rows, total_significant=2, has_previous_snapshot=True)
    text = format_oi_changes_section(result)

    call_line = next(l for l in text.splitlines() if l.strip().startswith("95,000"))
    assert "Call" in call_line
    assert "+50" in call_line

    put_line = next(l for l in text.splitlines() if l.strip().startswith("90,000"))
    assert "Put" in put_line
    assert "-100" in put_line


def test_oi_changes_caps_display_at_15_rows():
    rows = tuple(
        OiChangeRow(strike=float(90000 + i), option_type="C", previous_oi=100.0, current_oi=150.0, change=50.0, change_pct=50.0)
        for i in range(20)
    )
    result = OiChangesResult(rows=rows, total_significant=20, has_previous_snapshot=True)
    text = format_oi_changes_section(result)
    rendered_rows = [l for l in text.splitlines() if l.strip() and l.strip()[0].isdigit()]
    assert len(rendered_rows) == 15


def test_iv_percentile_section_high_favors_selling_vol():
    result = IvPercentileResult(atm_strike=95000.0, current_iv=90.0, percentile=85.0, history_days=180)
    text = format_iv_percentile_section(result)
    assert "IV PERCENTILE (per-expiry, 180 days history)" in text
    assert "ATM Strike: $95,000  |  Current IV: 90.0%  |  Percentile: 85.0%" in text
    assert "IV is very high relative to history - favor selling vol" in text


def test_iv_percentile_section_low_favors_buying_vol():
    result = IvPercentileResult(atm_strike=95000.0, current_iv=40.0, percentile=15.0, history_days=180)
    text = format_iv_percentile_section(result)
    assert "IV is very low relative to history - favor buying vol" in text


def test_iv_percentile_section_mid_range_no_advice_line():
    result = IvPercentileResult(atm_strike=95000.0, current_iv=60.0, percentile=50.0, history_days=180)
    text = format_iv_percentile_section(result)
    assert "favor selling vol" not in text
    assert "favor buying vol" not in text


def test_iv_percentile_section_ends_with_blank_line():
    """Matches the legacy service's string concatenation (existing + iv_section),
    where iv_section always ended with an extra trailing blank line."""
    result = IvPercentileResult(atm_strike=95000.0, current_iv=60.0, percentile=50.0, history_days=180)
    text = format_iv_percentile_section(result)
    assert text.endswith("\n\n")
