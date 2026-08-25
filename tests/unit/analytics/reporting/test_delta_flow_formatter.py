"""
Tests for format_delta_flow_section (institutional_metrics_spec.md section
6 / task C7 -- signed net flow + gross hedging-impact side by side,
Decision D8).
"""

from datetime import datetime

from coding.core.analytics.reporting.delta_flow_formatter import (
    format_delta_adjusted_flow_section,
    format_delta_flow_coverage_line,
    format_delta_flow_section,
)
from coding.core.analytics.results.delta_flow_results import FlowBucket
from coding.core.analytics.results.flow_results import FlowResult, FlowTotals, TopStrikeEntry


def _bucket(expiration, hiro=100.0, premium=10.0, gross=200.0, trade_count=5,
            buy_count=3, sell_count=2, skipped_count=0):
    return FlowBucket(
        expiration=expiration, hiro_usd=hiro, premium_usd=premium, gross_delta_usd=gross,
        net_contracts=1.0, gross_contracts=2.0, trade_count=trade_count,
        buy_count=buy_count, sell_count=sell_count, skipped_count=skipped_count,
    )


def _flow_result(**overrides) -> FlowResult:
    defaults = dict(
        flow_data={},
        expiration_totals=FlowTotals(
            call_buy_volume=10.0, call_sell_volume=2.0, put_buy_volume=1.0, put_sell_volume=5.0
        ),
        bias_interpretation="Heavy Buying",
        flow_trend="Steady Buy Pressure",
        top_buy_strikes=(
            TopStrikeEntry(strike=95000.0, option_type="C", net_flow=8.0, volume=10.0, notional=950_000.0),
        ),
        top_sell_strikes=(
            TopStrikeEntry(strike=90000.0, option_type="P", net_flow=-4.0, volume=5.0, notional=450_000.0),
        ),
        trade_count=6,
        spot_price=95000.0,
        window_start_ms=1_000,
        window_end_ms=1_000_000,
        lookback_hours=24.0,
        sufficient_data=True,
        low_confidence=False,
    )
    defaults.update(overrides)
    return FlowResult(**defaults)


# ---------------------------------------------------------------------------
# format_delta_adjusted_flow_section (institutional_metrics_spec.md section
# 6(c) / section 9(b) per-expiry order item 5, Task D2 independent review
# Important #1)
# ---------------------------------------------------------------------------

class TestFormatDeltaAdjustedFlowSection:
    def test_both_none_returns_empty_string(self):
        assert format_delta_adjusted_flow_section(None, None, lookback_hours=24.0) == ""

    def test_headline_from_bucket_replaces_contract_count_claim(self):
        """spec 6(c): 'replaces the contract-count net flow headline' --
        the old EXPIRATION-LEVEL FLOW bias/trend claim must not appear."""
        bucket = _bucket("25JUL26", hiro=-2_110_400.0, premium=-188_020.0, gross=11_004_220.0)
        text = format_delta_adjusted_flow_section(bucket, _flow_result(), lookback_hours=24.0)
        assert "DELTA-ADJUSTED FLOW" in text
        assert "-2,110,400" in text
        assert "-188,020" in text
        assert "+11,004,220" in text
        assert "EXPIRATION-LEVEL FLOW" not in text
        assert "Bias:" not in text
        assert "Heavy Buying" not in text

    def test_per_strike_tables_kept(self):
        """spec 6(c): 'keep the per-strike table'."""
        bucket = _bucket("25JUL26")
        text = format_delta_adjusted_flow_section(bucket, _flow_result(), lookback_hours=24.0)
        assert "TOP 5 STRIKES BY BUYING PRESSURE:" in text
        assert "TOP 5 STRIKES BY SELLING PRESSURE:" in text
        assert "95,000" in text
        assert "90,000" in text

    def test_bucket_none_flow_present_shows_no_hiro_data_line(self):
        text = format_delta_adjusted_flow_section(None, _flow_result(), lookback_hours=24.0)
        assert "DELTA-ADJUSTED FLOW" in text
        assert "no data for this expiry" in text
        assert "TOP 5 STRIKES BY BUYING PRESSURE:" in text

    def test_flow_none_bucket_present_shows_headline_only(self):
        bucket = _bucket("25JUL26")
        text = format_delta_adjusted_flow_section(bucket, None, lookback_hours=24.0)
        assert "DELTA-ADJUSTED FLOW" in text
        assert "TOP 5 STRIKES BY BUYING PRESSURE:" not in text

    def test_insufficient_flow_data_suppresses_tables_not_headline(self):
        bucket = _bucket("25JUL26", hiro=500.0, premium=50.0, gross=1000.0)
        flow = _flow_result(trade_count=3, sufficient_data=False)
        text = format_delta_adjusted_flow_section(bucket, flow, lookback_hours=24.0)
        assert "+500" in text  # headline still renders
        assert "** INSUFFICIENT FLOW DATA **" in text
        assert "TOP 5 STRIKES BY BUYING PRESSURE:" not in text

    def test_zero_trades_shows_no_activity_line(self):
        bucket = _bucket("25JUL26", trade_count=0, skipped_count=0)
        text = format_delta_adjusted_flow_section(bucket, None, lookback_hours=24.0)
        assert "no trade activity in window" in text.lower()

    def test_skip_rate_line_present(self):
        # skip_rate = skipped_count / (trade_count + skipped_count) = 2/102
        bucket = _bucket("25JUL26", trade_count=100, skipped_count=2)
        text = format_delta_adjusted_flow_section(bucket, None, lookback_hours=24.0)
        assert "skipped 2" in text
        assert "1.96%" in text

    def test_header_carries_window_and_units_label(self):
        """
        Independent review round 2 (Important #2): the merged per-expiry
        header must carry the same window/units disclosure the old
        currency-wide header had -- silently dropping it (bare
        "DELTA-ADJUSTED FLOW" with no window/units anywhere) was the
        defect this test guards against.
        """
        bucket = _bucket("25JUL26")
        text = format_delta_adjusted_flow_section(bucket, None, lookback_hours=24.0)
        assert "DELTA-ADJUSTED FLOW (24h, taker-signed, USD notional)" in text

    def test_header_window_label_reflects_non_integer_lookback(self):
        bucket = _bucket("25JUL26")
        text = format_delta_adjusted_flow_section(bucket, None, lookback_hours=1.5)
        assert "DELTA-ADJUSTED FLOW (1.5h, taker-signed, USD notional)" in text


class TestFormatDeltaFlowCoverageLine:
    """
    Independent review round 2 (Important #2): the STALE/Coverage
    disclosure format_delta_flow_section used to render was silently
    dropped when Important #1's fix retired that currency-wide section --
    format_delta_flow_coverage_line restores it as a single market-wide
    line (see format_market_wide_context_section).
    """

    def test_coverage_line_no_staleness(self):
        line = format_delta_flow_coverage_line(24, 24.0, None)
        assert line == "Delta-flow coverage: 24/24h hourly rows persisted"
        assert "STALE" not in line

    def test_coverage_line_thin_history_not_stale(self):
        line = format_delta_flow_coverage_line(3, 24.0, None)
        assert line == "Delta-flow coverage: 3/24h hourly rows persisted"
        assert "STALE" not in line

    def test_coverage_line_staleness_rendered(self):
        stale_ts = datetime(2026, 7, 31, 2, 0, 0)
        line = format_delta_flow_coverage_line(12, 24.0, stale_ts)
        assert "STALE" in line
        assert "2026-07-31 02:00" in line
        assert "12/24h hourly rows persisted" in line

    def test_coverage_line_non_integer_lookback(self):
        line = format_delta_flow_coverage_line(1, 1.5, None)
        assert "1/1.5h hourly rows persisted" in line


class TestFormatDeltaFlowSection:
    def test_empty_buckets_returns_empty_string(self):
        """No flow_delta_hourly rows in the window (feature just shipped, or
        daemon hasn't run) -> no section, matches the codebase's existing
        'no data -> no section' convention."""
        assert format_delta_flow_section((), lookback_hours=24.0) == ""

    def test_missing_all_bucket_returns_empty_string(self):
        """Per-expiration rows with no total is a shape the report can't
        trust -- render nothing rather than a table missing its own total."""
        buckets = (_bucket("27MAR26"),)
        assert format_delta_flow_section(buckets, lookback_hours=24.0) == ""

    def test_header_shows_lookback_window(self):
        buckets = (_bucket("ALL"),)
        text = format_delta_flow_section(buckets, lookback_hours=24.0)
        assert "DELTA-ADJUSTED FLOW (24h, taker-signed, USD notional)" in text

    def test_total_row_present(self):
        buckets = (_bucket("ALL", hiro=12_480_220.0, premium=1_204_880.0, gross=48_220_110.0,
                            trade_count=14_677, skipped_count=27),)
        text = format_delta_flow_section(buckets, lookback_hours=24.0)

        assert "Total" in text
        assert "+12,480,220" in text
        assert "+1,204,880" in text
        assert "+48,220,110" in text

    def test_signed_values_show_negative_sign(self):
        buckets = (_bucket("ALL", hiro=-2_110_400.0, premium=-188_020.0, gross=11_004_220.0),)
        text = format_delta_flow_section(buckets, lookback_hours=24.0)

        assert "-2,110,400" in text
        assert "-188,020" in text
        # gross is unsigned -- always non-negative, still shown with a '+'
        assert "+11,004,220" in text

    def test_per_expiration_rows_sorted_by_expiration_string(self):
        buckets = (
            _bucket("ALL"),
            _bucket("31JUL26", hiro=14_590_620.0, premium=1_392_900.0, gross=37_215_890.0),
            _bucket("25JUL26", hiro=-2_110_400.0, premium=-188_020.0, gross=11_004_220.0),
        )
        text = format_delta_flow_section(buckets, lookback_hours=24.0)

        idx_25 = text.index("25JUL26")
        idx_31 = text.index("31JUL26")
        assert idx_25 < idx_31

    def test_trade_count_and_skip_rate_line(self):
        buckets = (_bucket("ALL", trade_count=14_677, skipped_count=27),)
        text = format_delta_flow_section(buckets, lookback_hours=24.0)

        assert "14,677" in text
        assert "skipped 27" in text
        assert "0.18%" in text

    def test_zero_trades_shows_no_activity_not_a_fabricated_zero_percent(self):
        """A currency/hour with genuinely zero trades has trade_count == 0
        AND skipped_count == 0 -- skip_rate is None (0/0), and the section
        must say so explicitly rather than printing a misleading '0.00%'
        (which would read as 'checked, found nothing wrong' instead of
        'nothing was here to check')."""
        buckets = (_bucket("ALL", hiro=0.0, premium=0.0, gross=0.0, trade_count=0, buy_count=0,
                            sell_count=0, skipped_count=0),)
        text = format_delta_flow_section(buckets, lookback_hours=24.0)

        assert "%" not in text
        assert "no trade activity" in text.lower()

    def test_all_skipped_shows_full_skip_rate(self):
        """Every trade skipped (e.g. all bad IV): trade_count == 0 but
        skipped_count > 0 -- skip_rate is 1.0 (100%), a real number, distinct
        from the zero-trades case above."""
        buckets = (_bucket("ALL", hiro=0.0, premium=0.0, gross=0.0, trade_count=0, buy_count=0,
                            sell_count=0, skipped_count=5),)
        text = format_delta_flow_section(buckets, lookback_hours=24.0)

        assert "100.00%" in text

    def test_non_integer_lookback_hours_formatted(self):
        buckets = (_bucket("ALL"),)
        text = format_delta_flow_section(buckets, lookback_hours=1.5)
        assert "1.5h" in text

    def test_no_delta_unicode_character_in_output(self):
        """Review fix, Minor #3: this machine's default console codec is
        cp1252, which raises UnicodeEncodeError on U+0394 (Δ). ASCII-safe
        'Delta' label carries the same information with no console-crash
        risk for any caller that prints this string directly."""
        buckets = (_bucket("ALL"),)
        text = format_delta_flow_section(buckets, lookback_hours=24.0)

        assert "Δ" not in text
        assert "Delta" in text
        text.encode("cp1252")  # must not raise

    def test_coverage_line_always_present(self):
        buckets = (_bucket("ALL"),)
        text = format_delta_flow_section(buckets, lookback_hours=24.0, hours_present=24)
        assert "Coverage: 24/24h hourly rows persisted" in text

    def test_coverage_line_present_even_when_thin(self):
        """A currency whose feature just shipped and has only accumulated
        a few hours of history -- informative to disclose even though the
        daemon is NOT lagging (distinct from the staleness note)."""
        buckets = (_bucket("ALL"),)
        text = format_delta_flow_section(buckets, lookback_hours=24.0, hours_present=3)
        assert "Coverage: 3/24h hourly rows persisted" in text
        assert "STALE" not in text

    def test_no_staleness_note_when_stale_since_is_none(self):
        buckets = (_bucket("ALL"),)
        text = format_delta_flow_section(buckets, lookback_hours=24.0, hours_present=24, stale_since=None)
        assert "STALE" not in text

    def test_staleness_note_rendered_when_stale_since_given(self):
        """A daemon down for 12h: hours_present alone (a count) doesn't
        disclose HOW STALE -- the explicit STALE line, mirroring
        format_historical_context_section's 'STALE: history ends {ts}'
        convention, must name the actual gap."""
        stale_ts = datetime(2026, 7, 31, 2, 0, 0)
        buckets = (_bucket("ALL"),)
        text = format_delta_flow_section(
            buckets, lookback_hours=24.0, hours_present=12, stale_since=stale_ts,
        )

        assert "STALE" in text
        assert "2026-07-31 02:00" in text
        assert "Coverage: 12/24h hourly rows persisted" in text

    def test_staleness_note_appears_before_the_data_table(self):
        """Mirrors historical context's placement: the disclosure comes
        right after the header, before any numbers, so a reader can't
        miss it by skimming straight to the totals."""
        stale_ts = datetime(2026, 7, 31, 2, 0, 0)
        buckets = (_bucket("ALL"),)
        text = format_delta_flow_section(
            buckets, lookback_hours=24.0, hours_present=12, stale_since=stale_ts,
        )

        assert text.index("STALE") < text.index("Total")
