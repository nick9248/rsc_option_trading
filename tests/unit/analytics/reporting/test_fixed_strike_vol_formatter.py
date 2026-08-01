"""
Tests for format_fixed_strike_vol_section (institutional_metrics_spec.md
section 7 / Task C8).

Covers the full-table render (calls/puts split, ATM-region-only display per
spec's "compact" requirement) and every graceful insufficient-history
fallback message the brief requires, reusing Task C1's historical-context
"no data -> no section" / explicit-message convention rather than
fabricating a comparison.
"""

from datetime import date

from coding.core.analytics.reporting.fixed_strike_vol_formatter import (
    format_fixed_strike_vol_section,
)
from coding.core.analytics.results.fixed_strike_vol_results import (
    FixedStrikeVolResult,
    StrikeIvChangeRow,
)

TODAY = date(2026, 7, 31)
YESTERDAY = date(2026, 7, 30)
FOUR_DAYS_AGO = date(2026, 7, 27)


def _base_result(**overrides):
    defaults = dict(
        expiration="31JUL26",
        today_date=TODAY,
        prior_date=YESTERDAY,
        expected_prior_date=YESTERDAY,
        stale_prior=False,
        spot_today=64182.0,
        spot_prior=64182.0,
        spot_move_pct=0.0,
        atm_iv_today=33.00,
        atm_iv_prior=30.00,
        d_atm=3.00,
        rows=(
            StrikeIvChangeRow(
                strike=65000.0, option_type="C", iv_today=34.50, iv_prior=32.00,
                d_iv=2.50, d_vs_atm=-0.50, moneyness_pct=1.27,
            ),
            StrikeIvChangeRow(
                strike=65000.0, option_type="P", iv_today=36.00, iv_prior=33.00,
                d_iv=3.00, d_vs_atm=0.00, moneyness_pct=1.27,
            ),
        ),
        n_strikes_matched=2,
        n_strikes_unmatched=0,
        regime="REPRICED",
    )
    defaults.update(overrides)
    return FixedStrikeVolResult(**defaults)


class TestNoDataConvention:
    def test_none_result_returns_empty_string(self):
        assert format_fixed_strike_vol_section(None) == ""


class TestFullTableRender(object):
    def test_header_shows_expiration_and_dates(self):
        text = format_fixed_strike_vol_section(_base_result())
        assert "FIXED-STRIKE VOL CHANGE" in text
        assert "31JUL26" in text
        assert "2026-07-30" in text
        assert "2026-07-31" in text

    def test_spot_and_atm_summary_line(self):
        text = format_fixed_strike_vol_section(_base_result())
        assert "ATM IV 30.00 -> 33.00 (+3.00)" in text

    def test_price_line_labelled_forward_not_spot(self):
        """Independent review (Task C8 fix round, Important #1): the
        anchor is this expiry's FORWARD price, not the spot index --
        labelling it 'Spot' would relocate the same confusion the anchor
        fix eliminated into the report text itself."""
        text = format_fixed_strike_vol_section(_base_result())
        assert "Fwd " in text
        assert "Spot " not in text

    def test_calls_and_puts_rendered_as_separate_blocks(self):
        text = format_fixed_strike_vol_section(_base_result())
        assert "CALLS" in text
        assert "PUTS" in text
        calls_idx = text.index("CALLS")
        puts_idx = text.index("PUTS")
        assert calls_idx < puts_idx

    def test_row_values_rendered(self):
        text = format_fixed_strike_vol_section(_base_result())
        assert "65,000" in text
        assert "34.50" in text
        assert "+2.50" in text
        assert "-0.50" in text

    def test_regime_and_match_counts_footer(self):
        text = format_fixed_strike_vol_section(_base_result())
        assert "Regime: REPRICED" in text
        assert "2 strikes matched" in text
        assert "0 unmatched" in text

    def test_rows_outside_atm_region_excluded_from_display(self):
        far_row = StrikeIvChangeRow(
            strike=150000.0, option_type="C", iv_today=90.0, iv_prior=10.0,
            d_iv=80.0, d_vs_atm=77.0, moneyness_pct=133.7,
        )
        result = _base_result(rows=_base_result().rows + (far_row,), n_strikes_matched=3)
        text = format_fixed_strike_vol_section(result)
        assert "150,000" not in text
        # Still counted in the footer even though not displayed.
        assert "3 strikes matched" in text

    def test_row_with_none_d_vs_atm_renders_as_na(self):
        row = StrikeIvChangeRow(
            strike=65000.0, option_type="C", iv_today=34.50, iv_prior=32.00,
            d_iv=2.50, d_vs_atm=None, moneyness_pct=1.27,
        )
        result = _base_result(rows=(row,), n_strikes_matched=1)
        text = format_fixed_strike_vol_section(result)
        assert "n/a" in text

    def test_no_ascii_delta_character_for_console_safety(self):
        """Mirrors delta_flow_formatter's cp1252-safety fix (review Minor
        #3) -- must never emit U+0394 into report text."""
        text = format_fixed_strike_vol_section(_base_result())
        assert "Δ" not in text


class TestInsufficientHistoryFallback:
    def test_missing_prior_date_entirely(self):
        result = _base_result(
            prior_date=None, stale_prior=True, atm_iv_prior=None, d_atm=None,
            rows=(), n_strikes_matched=0, n_strikes_unmatched=1, regime="INDETERMINATE",
        )
        text = format_fixed_strike_vol_section(result)
        assert "no comparable prior snapshot" in text
        assert "CALLS" not in text
        assert "Regime:" not in text

    def test_stale_prior_shows_actual_date_per_t7_3(self):
        """T7.3: the actual (too-old) prior date must be surfaced, never
        silently omitted."""
        result = _base_result(
            prior_date=FOUR_DAYS_AGO, expected_prior_date=YESTERDAY, stale_prior=True,
            regime="INDETERMINATE",
        )
        text = format_fixed_strike_vol_section(result)
        assert "no comparable prior snapshot" in text
        assert "2026-07-27" in text

    def test_missing_atm_iv_message(self):
        result = _base_result(
            atm_iv_prior=None, d_atm=None, stale_prior=False, regime="INDETERMINATE",
        )
        text = format_fixed_strike_vol_section(result)
        assert "ATM IV" in text

    def test_zero_matched_atm_region_rows_message(self):
        result = _base_result(
            rows=(), n_strikes_matched=0, n_strikes_unmatched=2,
            stale_prior=False, regime="INDETERMINATE",
        )
        text = format_fixed_strike_vol_section(result)
        assert "overlapping strikes" in text
