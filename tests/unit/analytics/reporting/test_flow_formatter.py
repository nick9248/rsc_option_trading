"""
Unit tests for coding.core.analytics.reporting.flow_formatter
(refactor_design_spec.md section T3).
"""

from coding.core.analytics.reporting.flow_formatter import (
    format_flow_section,
    format_flow_strike_tables,
)
from coding.core.analytics.results.flow_results import FlowResult, FlowTotals, TopStrikeEntry


def _make_result(**overrides) -> FlowResult:
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


def test_flow_section_header_and_totals():
    text = format_flow_section(_make_result(), lookback_hours=24)
    assert "BUY/SELL FLOW ANALYSIS (Trade Direction-Based)" in text
    assert "Spot Price: $95,000.00" in text
    assert "Window:" in text and "UTC" in text
    assert "Trades Analyzed: 6" in text
    assert "Bias: Heavy Buying" in text
    assert "Trend: Steady Buy Pressure" in text
    assert "LOW CONFIDENCE" not in text  # sufficient_data=True, low_confidence=False


def test_flow_section_suppressed_below_sufficiency_floor():
    """bugfix_spec.md Item 6 / Decision D5: <5 trades suppresses the whole section."""
    result = _make_result(trade_count=3, sufficient_data=False, low_confidence=False)
    text = format_flow_section(result, lookback_hours=24)
    assert "** INSUFFICIENT FLOW DATA **" in text
    assert "3 trade(s) in window, 5 required" in text
    assert "EXPIRATION-LEVEL FLOW:" not in text
    assert "Heavy Buying" not in text


def test_flow_section_low_confidence_tag_rendered():
    result = _make_result(sufficient_data=True, low_confidence=True)
    text = format_flow_section(result, lookback_hours=24)
    assert "Bias: Heavy Buying (LOW CONFIDENCE)" in text
    assert "Trend: Steady Buy Pressure (LOW CONFIDENCE)" in text


def test_flow_section_top_buy_and_sell_tables():
    text = format_flow_section(_make_result(), lookback_hours=24)
    assert "TOP 5 STRIKES BY BUYING PRESSURE:" in text
    buy_line = next(l for l in text.splitlines() if l.strip().startswith("95,000"))
    assert "+8.0" in buy_line
    assert "950,000.00" in buy_line

    assert "TOP 5 STRIKES BY SELLING PRESSURE:" in text
    sell_line = next(l for l in text.splitlines() if l.strip().startswith("90,000"))
    assert "-4.0" in sell_line
    assert "450,000.00" in sell_line


def test_flow_section_no_buying_or_selling_detected():
    result = _make_result(top_buy_strikes=(), top_sell_strikes=())
    text = format_flow_section(result, lookback_hours=24)
    assert "  No net buying detected" in text
    assert "  No net selling detected" in text


# ---------------------------------------------------------------------------
# format_flow_strike_tables (institutional_metrics_spec.md section 6(c),
# Task D2 independent review Important #1: the shared "keep the per-strike
# table" half, reused by delta_flow_formatter.format_delta_adjusted_flow_
# section)
# ---------------------------------------------------------------------------

def test_flow_strike_tables_no_header_or_headline():
    """Just the two tables -- no 'BUY/SELL FLOW ANALYSIS' header, no
    'EXPIRATION-LEVEL FLOW' bias/trend headline."""
    text = format_flow_strike_tables(_make_result())
    assert "BUY/SELL FLOW ANALYSIS" not in text
    assert "EXPIRATION-LEVEL FLOW" not in text
    assert "Heavy Buying" not in text
    assert "TOP 5 STRIKES BY BUYING PRESSURE:" in text
    assert "TOP 5 STRIKES BY SELLING PRESSURE:" in text


def test_flow_strike_tables_matches_format_flow_section_tables_byte_for_byte():
    """The refactor that extracted this function must not change
    format_flow_section's own rendered table bytes."""
    result = _make_result()
    full_text = format_flow_section(result, lookback_hours=24)
    tables_text = format_flow_strike_tables(result)
    assert tables_text in full_text
