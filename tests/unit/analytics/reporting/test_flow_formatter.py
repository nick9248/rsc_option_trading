"""
Unit tests for coding.core.analytics.reporting.flow_formatter
(refactor_design_spec.md section T3).
"""

from coding.core.analytics.reporting.flow_formatter import format_flow_section
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
    assert "Lookback Window: 24 hours" in text
    assert "Trades Analyzed: 6" in text
    assert "Bias: Heavy Buying" in text
    assert "Trend: Steady Buy Pressure" in text


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
