"""
Unit tests for coding.core.analytics.reporting.exposure_profile_formatter
(institutional_metrics_spec.md section 4(c), Task C5).
"""

from coding.core.analytics.reporting.exposure_profile_formatter import (
    format_exposure_profile_section,
)
from coding.core.analytics.results.exposure_profile_results import (
    ExposureProfileResult,
    ExposureStrikeRow,
)


def _make_result(**overrides) -> ExposureProfileResult:
    defaults = dict(
        strike_rows=(
            ExposureStrikeRow(
                strike=60000.0, call_oi=1204.0, put_oi=3880.0,
                call_vanna=0.01, put_vanna=0.01, call_charm=-0.02, put_charm=-0.02,
                vex_holder=-412003.0, cex_holder=21004.0,
                vex_assumed_dealer=88120.0, cex_assumed_dealer=-4880.0,
            ),
            ExposureStrikeRow(
                strike=70000.0, call_oi=25417.0, put_oi=210.0,
                call_vanna=0.02, put_vanna=0.02, call_charm=-0.03, put_charm=-0.03,
                vex_holder=2880441.0, cex_holder=-310220.0,
                vex_assumed_dealer=2844900.0, cex_assumed_dealer=-308000.0,
            ),
        ),
        spot_price=64000.0,
        currency="BTC",
        total_vex_holder=3420000.0,
        total_cex_holder=-290000.0,
        total_vex_assumed_dealer=2933020.0,
        total_cex_assumed_dealer=-312880.0,
        peak_vanna_strike=70000.0,
        peak_charm_strike=70000.0,
        skipped_instruments=0,
    )
    defaults.update(overrides)
    return ExposureProfileResult(**defaults)


def test_section_header_and_no_interpretive_sentence():
    """Spec 4(c): 'No interpretive sentence. The numbers and their peaks
    are the output.'"""
    text = format_exposure_profile_section(_make_result(), currency="BTC")
    assert "VANNA / CHARM PROFILE (holder-side raw; assumed-dealer view in brackets)" in text
    # No directional/advisory language like the old aggregate block had.
    assert "bullish" not in text.lower()
    assert "bearish" not in text.lower()
    assert "dealers" not in text.lower()


def test_holder_value_with_bracketed_dealer_value_per_strike():
    text = format_exposure_profile_section(_make_result(), currency="BTC")
    line = next(l for l in text.splitlines() if l.strip().startswith("70,000"))
    assert "+2,880,441" in line
    assert "[+2,844,900]" in line
    assert "-310,220" in line
    assert "[-308,000]" in line


def test_peak_strikes_and_totals_line():
    text = format_exposure_profile_section(_make_result(), currency="BTC")
    assert "Peak vanna strike: $70,000" in text
    assert "Peak charm strike: $70,000" in text
    assert "Total VEX: +3.42M USD/vol pt" in text
    assert "Total CEX: -0.29M USD/day" in text


def test_no_peak_strikes_renders_none():
    result = _make_result(strike_rows=(), peak_vanna_strike=None, peak_charm_strike=None,
                           total_vex_holder=0.0, total_cex_holder=0.0)
    text = format_exposure_profile_section(result, currency="BTC")
    assert "Peak vanna strike: None" in text
    assert "Peak charm strike: None" in text


def test_empty_strike_rows_still_renders_well_formed_section():
    result = _make_result(strike_rows=(), peak_vanna_strike=None, peak_charm_strike=None,
                           total_vex_holder=0.0, total_cex_holder=0.0,
                           total_vex_assumed_dealer=0.0, total_cex_assumed_dealer=0.0)
    text = format_exposure_profile_section(result, currency="BTC")
    assert "VANNA / CHARM PROFILE" in text
    assert "Total VEX: +0.00M USD/vol pt" in text
