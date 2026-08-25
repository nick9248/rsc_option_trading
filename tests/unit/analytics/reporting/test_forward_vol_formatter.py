"""
Unit tests for coding.core.analytics.reporting.market_wide_formatter.
format_forward_vol_section (institutional_metrics_spec.md section 8,
Task C9).

ASCII-only assertions: this codebase's console codec is cp1252
(fixed_strike_vol_formatter.py's own explicit convention) -- no sigma/arrow
unicode glyphs in the rendered text.
"""

from coding.core.analytics.reporting.market_wide_formatter import format_forward_vol_section
from coding.core.analytics.results.market_wide_results import ForwardVolBucket, ForwardVolResult


def _bucket(
    from_expiry="25JUL26", to_expiry="31JUL26", t1=0.6, t2=6.6,
    sigma1=18.51, sigma2=34.62, fwd_var=0.1314, fwd_vol=36.20,
    negative_variance=False, event_premium=None, flags=(),
):
    return ForwardVolBucket(
        from_expiry=from_expiry, to_expiry=to_expiry,
        t1_days=t1, t2_days=t2,
        sigma1_pct=sigma1, sigma2_pct=sigma2,
        fwd_var=fwd_var, fwd_vol_pct=fwd_vol,
        negative_variance=negative_variance, event_premium=event_premium,
        flags=tuple(flags),
    )


def test_forward_vol_none_shows_no_data():
    report = format_forward_vol_section(None)
    assert "FORWARD VOL" in report
    assert "No forward vol data available" in report


def test_forward_vol_empty_buckets_shows_no_data():
    report = format_forward_vol_section(ForwardVolResult(buckets=()))
    assert "No forward vol data available" in report


def test_forward_vol_standard_row_rendered():
    result = ForwardVolResult(buckets=(_bucket(),))
    report = format_forward_vol_section(result)

    assert "25JUL26 -> 31JUL26" in report
    assert "0.6d" in report
    assert "6.6d" in report
    assert "18.51" in report
    assert "34.62" in report
    assert "36.20" in report
    assert "EVENT PREMIUM" not in report
    assert "DATA QUALITY" not in report


def test_forward_vol_negative_variance_shows_text_and_header_warning():
    bucket = _bucket(
        sigma1=80.0, sigma2=40.0, fwd_var=-0.96, fwd_vol=None,
        negative_variance=True, flags=("NEGATIVE_VARIANCE",),
    )
    result = ForwardVolResult(buckets=(bucket,))
    report = format_forward_vol_section(result)

    assert "NEG VARIANCE (-0.9600)" in report
    assert "DATA QUALITY" in report
    assert result.has_negative_variance is True


def test_forward_vol_no_warning_when_no_negative_variance():
    result = ForwardVolResult(buckets=(_bucket(),))
    report = format_forward_vol_section(result)
    assert "DATA QUALITY" not in report


def test_forward_vol_event_premium_flag_rendered():
    bucket = _bucket(
        from_expiry="31JUL26", to_expiry="07AUG26", t1=6.6, t2=13.6,
        sigma1=34.62, sigma2=41.59, fwd_var=0.2154, fwd_vol=46.42,
        event_premium=12.3, flags=("EVENT_PREMIUM",),
    )
    result = ForwardVolResult(buckets=(bucket,))
    report = format_forward_vol_section(result)

    assert "46.42" in report
    assert "EVENT PREMIUM" in report


def test_forward_vol_no_flag_column_text_when_flags_empty():
    result = ForwardVolResult(buckets=(_bucket(flags=()),))
    report = format_forward_vol_section(result)
    row_line = [ln for ln in report.splitlines() if "25JUL26 -> 31JUL26" in ln][0]
    assert "EVENT PREMIUM" not in row_line


def test_forward_vol_multiple_buckets_all_rendered():
    buckets = (
        _bucket("25JUL26", "31JUL26", 0.6, 6.6, 18.51, 34.62, 0.1314, 36.20),
        _bucket(
            "31JUL26", "07AUG26", 6.6, 13.6, 34.62, 41.59, 0.2154, 46.42,
            event_premium=12.3, flags=("EVENT_PREMIUM",),
        ),
        _bucket("07AUG26", "28AUG26", 13.6, 34.6, 41.59, 36.21, 0.1109, 33.30),
    )
    result = ForwardVolResult(buckets=buckets)
    report = format_forward_vol_section(result)

    assert "25JUL26 -> 31JUL26" in report
    assert "31JUL26 -> 07AUG26" in report
    assert "07AUG26 -> 28AUG26" in report
    assert report.count("EVENT PREMIUM") == 1
