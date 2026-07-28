"""
Unit tests for coding.core.analytics.reporting.expiry_formatter
(refactor_design_spec.md section T3).

format_expiration_section/format_trend_delta are extracted verbatim from
OnChainAnalyzer.generate_report()/_format_trend — the golden-master
characterization suite (tests/characterization/test_onchain_golden_master.py)
is the byte-identical proof for the full integration path. These tests give
fast, isolated coverage of each branch (N/A ratios, empty support/resistance,
trend arrows, strike-table annotations) using hand-built result models.
"""

import pytest

from coding.core.analytics.reporting.expiry_formatter import (
    format_expiration_section,
    format_trend_delta,
)
from coding.core.analytics.results.analysis_result import TrendSnapshot
from coding.core.analytics.results.expiry_results import (
    ExpirationAnalysisResult,
    LevelRef,
    MaxPainResult,
    MoneynessLeg,
    MoneynessResult,
    PutCallRatioResult,
    StrikeOiRow,
    SupportResistanceResult,
    VolumeStatsResult,
)

SPOT_PRICE = 64405.02


def _leg(itm_oi, otm_oi, itm_notional, otm_notional, itm_pct, otm_pct):
    return MoneynessLeg(
        itm_oi=itm_oi, otm_oi=otm_oi, total_oi=itm_oi + otm_oi,
        itm_notional=itm_notional, otm_notional=otm_notional,
        total_notional=itm_notional + otm_notional, itm_pct=itm_pct, otm_pct=otm_pct,
    )


def _make_analysis(**overrides) -> ExpirationAnalysisResult:
    defaults = dict(
        expiration="14AUG26",
        underlying_price=SPOT_PRICE,
        total_instruments=3,
        call_count=1,
        put_count=2,
        strike_rows=(
            StrikeOiRow(strike=60000.0, call_oi=0.0, put_oi=100.0, call_volume=0.0, put_volume=10.0),
            StrikeOiRow(strike=65000.0, call_oi=50.0, put_oi=20.0, call_volume=5.0, put_volume=2.0),
        ),
        max_pain=MaxPainResult(
            max_pain_strike=65000.0, pain_by_strike={60000.0: 500.0, 65000.0: 100.0}, min_pain_value=100.0
        ),
        put_call_ratio=PutCallRatioResult(
            total_call_oi=50.0, total_put_oi=120.0, ratio=2.4, bias="Strong Bearish"
        ),
        volume_stats=VolumeStatsResult(
            total_call_volume=5.0, total_put_volume=12.0, total_volume=17.0, volume_ratio=2.4
        ),
        moneyness=MoneynessResult(
            calls=_leg(0.0, 50.0, 0.0, 3_000_000.0, 0.0, 100.0),
            puts=_leg(20.0, 100.0, 1_200_000.0, 6_000_000.0, 16.67, 83.33),
            totals=_leg(20.0, 150.0, 1_200_000.0, 9_000_000.0, 11.76, 88.24),
            oi_skew="Heavy OTM (Speculative)",
        ),
        support_resistance=SupportResistanceResult(
            resistance_levels=(LevelRef(strike=65000.0, open_interest=50.0),),
            support_levels=(LevelRef(strike=60000.0, open_interest=100.0),),
            short_term_resistance=LevelRef(strike=65000.0, open_interest=50.0),
            short_term_support=LevelRef(strike=60000.0, open_interest=100.0),
        ),
    )
    defaults.update(overrides)
    return ExpirationAnalysisResult(**defaults)


# ---------------------------------------------------------------------------
# format_trend_delta
# ---------------------------------------------------------------------------

def test_format_trend_delta_empty_when_previous_none():
    assert format_trend_delta(2000.0, None) == ""


def test_format_trend_delta_unchanged():
    assert "unchanged" in format_trend_delta(2000.0, 2000.0)


def test_format_trend_delta_up_integer_mode():
    result = format_trend_delta(2100.0, 1900.0)
    assert "↑" in result and "1,900" in result and "+200" in result


def test_format_trend_delta_down_integer_mode():
    result = format_trend_delta(1800.0, 2000.0)
    assert "↓" in result and "2,000" in result and "-200" in result


def test_format_trend_delta_ratio_mode():
    result = format_trend_delta(1.59, 1.42, is_ratio=True)
    assert "↑" in result and "1.42" in result and "+0.17" in result


# ---------------------------------------------------------------------------
# format_expiration_section — summary / max pain / P-C / volume
# ---------------------------------------------------------------------------

def test_summary_line():
    text = format_expiration_section(_make_analysis(), SPOT_PRICE, None)
    assert "Total Instruments: 3 (1 Calls, 2 Puts)" in text


def test_max_pain_rendered_with_distance():
    text = format_expiration_section(_make_analysis(), SPOT_PRICE, None)
    assert "Max Pain Strike: $65,000" in text
    assert "Distance from Current: $-594.98 (-0.92%)" in text


def test_max_pain_na_when_no_strike():
    analysis = _make_analysis(
        max_pain=MaxPainResult(max_pain_strike=None, pain_by_strike={}, min_pain_value=0.0)
    )
    text = format_expiration_section(analysis, SPOT_PRICE, None)
    assert "Max Pain Strike: N/A" in text


def test_max_pain_trend_shown_when_previous_present():
    trend = TrendSnapshot(
        max_pain_strike=64000.0, call_oi=None, put_oi=None, pc_ratio=None,
        total_volume=None, volume_ratio=None,
    )
    text = format_expiration_section(_make_analysis(), SPOT_PRICE, trend)
    assert "Trend (Max Pain):" in text
    assert "↑" in text


def test_max_pain_trend_absent_without_previous():
    text = format_expiration_section(_make_analysis(), SPOT_PRICE, None)
    assert "Trend (Max Pain):" not in text


def test_put_call_ratio_rendered_insufficient_history_by_default():
    """
    _make_analysis()'s default PutCallRatioResult fixture (used across most
    of this file's tests) never sets percentile_90d, matching the field's
    default -- bugfix_spec.md Item 10's "insufficient history" branch, not
    the old hard-coded-threshold label.
    """
    text = format_expiration_section(_make_analysis(), SPOT_PRICE, None)
    assert "Total Call OI: 50" in text
    assert "Total Put OI: 120" in text
    assert "P/C Ratio: 2.40 (n=0 - insufficient history for a percentile; absolute reading only)" in text


def test_put_call_ratio_percentile_classification_rendered():
    """bugfix_spec.md F10.3.3 report format, sufficient-history branch."""
    analysis = _make_analysis(
        put_call_ratio=PutCallRatioResult(
            total_call_oi=50.0, total_put_oi=120.0, ratio=2.4, bias="Strong Bearish",
            percentile_90d=98.3, history_n_90d=705,
        )
    )
    text = format_expiration_section(analysis, SPOT_PRICE, None)
    assert "P/C Ratio: 2.40 (98th pctile of its own 90d history, n=705) -> Strong Bearish" in text


def test_put_call_ratio_na_when_infinite():
    analysis = _make_analysis(
        put_call_ratio=PutCallRatioResult(
            total_call_oi=0.0, total_put_oi=50.0, ratio=float("inf"), bias="Strong Bearish"
        )
    )
    text = format_expiration_section(analysis, SPOT_PRICE, None)
    assert "P/C Ratio: N/A (No Call OI)" in text


def test_put_call_ratio_trends_shown():
    trend = TrendSnapshot(
        max_pain_strike=None, call_oi=40.0, put_oi=100.0, pc_ratio=2.0,
        total_volume=None, volume_ratio=None,
    )
    text = format_expiration_section(_make_analysis(), SPOT_PRICE, trend)
    assert "Trend (Call OI):" in text
    assert "Trend (Put OI):" in text
    assert "Trend (P/C):" in text


def test_put_call_ratio_trend_skipped_when_ratio_infinite():
    """Trend (P/C) is suppressed when the current ratio is inf, even with a previous pc_ratio."""
    analysis = _make_analysis(
        put_call_ratio=PutCallRatioResult(
            total_call_oi=0.0, total_put_oi=50.0, ratio=float("inf"), bias="Strong Bearish"
        )
    )
    trend = TrendSnapshot(
        max_pain_strike=None, call_oi=10.0, put_oi=40.0, pc_ratio=1.5,
        total_volume=None, volume_ratio=None,
    )
    text = format_expiration_section(analysis, SPOT_PRICE, trend)
    assert "Trend (Call OI):" in text  # call/put OI trend independent of ratio
    assert "Trend (P/C):" not in text


def test_volume_stats_rendered():
    text = format_expiration_section(_make_analysis(), SPOT_PRICE, None)
    assert "Total Call Volume: 5.00" in text
    assert "Total Put Volume: 12.00" in text
    assert "Total Volume: 17.00" in text
    assert "Volume P/C Ratio: 2.40" in text


def test_volume_ratio_na_when_infinite():
    analysis = _make_analysis(
        volume_stats=VolumeStatsResult(
            total_call_volume=0.0, total_put_volume=5.0, total_volume=5.0, volume_ratio=float("inf")
        )
    )
    text = format_expiration_section(analysis, SPOT_PRICE, None)
    assert "Volume P/C Ratio: N/A (No Call Volume)" in text


# ---------------------------------------------------------------------------
# Moneyness
# ---------------------------------------------------------------------------

def test_moneyness_skew_and_breakdown_rendered():
    text = format_expiration_section(_make_analysis(), SPOT_PRICE, None)
    assert "OI Skew: Heavy OTM (Speculative)" in text
    assert "CALLS:" in text
    assert "PUTS:" in text
    assert "COMBINED TOTALS:" in text
    assert "16.67%" in text  # puts ITM pct


# ---------------------------------------------------------------------------
# Strike table + notes
# ---------------------------------------------------------------------------

def test_strike_table_lists_strikes_ascending_with_notes():
    text = format_expiration_section(_make_analysis(), SPOT_PRICE, None)
    idx_60k = text.index("60,000")
    idx_65k = text.index("65,000", idx_60k)
    assert idx_60k < idx_65k  # ascending order
    # 60,000 is a support level (put OI top) -> "Support" note
    line_60k = text.splitlines()[[i for i, l in enumerate(text.splitlines()) if l.strip().startswith("60,000")][0]]
    assert "Support" in line_60k
    # 65,000 is both max pain and a resistance level
    line_65k = text.splitlines()[[i for i, l in enumerate(text.splitlines()) if l.strip().startswith("65,000")][0]]
    assert "<< MAX PAIN" in line_65k
    assert "Resistance" in line_65k


def test_strike_table_no_notes_when_not_special():
    analysis = _make_analysis(
        strike_rows=(StrikeOiRow(strike=61000.0, call_oi=1.0, put_oi=1.0, call_volume=0.1, put_volume=0.1),),
        max_pain=MaxPainResult(max_pain_strike=65000.0, pain_by_strike={}, min_pain_value=0.0),
    )
    text = format_expiration_section(analysis, SPOT_PRICE, None)
    line = next(l for l in text.splitlines() if l.strip().startswith("61,000"))
    assert line.rstrip().endswith("0.10")  # no trailing notes text after put volume column


# ---------------------------------------------------------------------------
# Support/Resistance + short-term levels
# ---------------------------------------------------------------------------

def test_support_resistance_levels_rendered():
    text = format_expiration_section(_make_analysis(), SPOT_PRICE, None)
    assert "RESISTANCE (Top 3 Call OI):" in text
    assert "1. $65,000 - Call OI: 50" in text
    assert "SUPPORT (Top 3 Put OI):" in text
    assert "1. $60,000 - Put OI: 100" in text


def test_support_resistance_none_found_when_empty():
    analysis = _make_analysis(
        support_resistance=SupportResistanceResult(
            resistance_levels=(), support_levels=(), short_term_resistance=None, short_term_support=None,
        )
    )
    text = format_expiration_section(analysis, SPOT_PRICE, None)
    assert text.count("  None found") == 2
    assert "Nearest Resistance: None found above current price" in text
    assert "Nearest Support: None found below current price" in text


def test_short_term_levels_rendered_with_price():
    text = format_expiration_section(_make_analysis(), SPOT_PRICE, None)
    assert f"SHORT-TERM LEVELS (nearest to current price ${SPOT_PRICE:,.2f}):" in text
    assert "Nearest Resistance: $65,000 (Call OI: 50)" in text
    assert "Nearest Support: $60,000 (Put OI: 100)" in text
