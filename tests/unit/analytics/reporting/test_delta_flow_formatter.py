"""
Tests for format_delta_flow_section (institutional_metrics_spec.md section
6 / task C7 -- signed net flow + gross hedging-impact side by side,
Decision D8).
"""

from coding.core.analytics.reporting.delta_flow_formatter import format_delta_flow_section
from coding.core.analytics.results.delta_flow_results import FlowBucket


def _bucket(expiration, hiro=100.0, premium=10.0, gross=200.0, trade_count=5,
            buy_count=3, sell_count=2, skipped_count=0):
    return FlowBucket(
        expiration=expiration, hiro_usd=hiro, premium_usd=premium, gross_delta_usd=gross,
        net_contracts=1.0, gross_contracts=2.0, trade_count=trade_count,
        buy_count=buy_count, sell_count=sell_count, skipped_count=skipped_count,
    )


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
