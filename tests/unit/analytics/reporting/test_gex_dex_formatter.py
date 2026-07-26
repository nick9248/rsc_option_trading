"""
Unit tests for coding.core.analytics.reporting.gex_dex_formatter
(refactor_design_spec.md section T3).
"""

from coding.core.analytics.reporting.gex_dex_formatter import (
    format_aggregate_gex_dex_section,
    format_gex_dex_section,
)
from coding.core.analytics.results.gex_dex_results import (
    GexDexKeyLevels,
    GexDexLevel,
    GexDexResult,
    GexDexStrikeRow,
)


def _make_result(**overrides) -> GexDexResult:
    defaults = dict(
        strike_rows=(
            GexDexStrikeRow(
                strike=95000.0, call_gamma=1.0, put_gamma=0.5, call_delta=0.6, put_delta=-0.4,
                call_oi=100.0, put_oi=50.0, net_gex=1_000_000.0, net_dex=0.2,
                net_gamma=0.5, cumulative_gex=500_000.0, cumulative_dex=-0.2,
            ),
            GexDexStrikeRow(
                strike=90000.0, call_gamma=0.2, put_gamma=1.5, call_delta=0.3, put_delta=-0.7,
                call_oi=20.0, put_oi=150.0, net_gex=-500_000.0, net_dex=-0.4,
                net_gamma=-1.3, cumulative_gex=-500_000.0, cumulative_dex=-0.4,
            ),
        ),
        cumulative_gex={90000.0: -500_000.0, 95000.0: 500_000.0},
        cumulative_dex={90000.0: -0.4, 95000.0: -0.2},
        key_levels=GexDexKeyLevels(
            call_resistance=GexDexLevel(strike=95000.0, net_gex=1_000_000.0),
            put_support=GexDexLevel(strike=90000.0, net_gex=-500_000.0),
            hvl=92000.0,
            gamma_flip=92000.0,
        ),
        spot_price=93000.0,
        total_net_gex=500_000.0,
        total_net_dex=-0.2,
        currency="BTC",
        expiration_count=None,
    )
    defaults.update(overrides)
    return GexDexResult(**defaults)


def test_gex_dex_section_key_levels_and_totals():
    text = format_gex_dex_section(_make_result(), "BTC")
    assert "GEX/DEX ANALYSIS (Gamma & Delta Exposure)" in text
    assert "Spot Price: $93,000.00" in text
    assert "Call Resistance: $95,000 (Net GEX: +1,000,000.00 USD)" in text
    assert "Put Support: $90,000 (Net GEX: -500,000.00 USD)" in text
    assert "Zero Gamma Level: $92,000" in text
    assert "Total Net GEX: +500,000.00 USD" in text
    assert "Total Net DEX: -0.2000 BTC" in text
    assert "GEX Environment: Positive (Dealers long gamma - stabilizing, buy dips/sell rallies)" in text
    assert "DEX Environment: Negative (Net short delta - bearish pressure)" in text


def test_gex_dex_section_no_levels_found():
    result = _make_result(
        key_levels=GexDexKeyLevels(call_resistance=None, put_support=None, hvl=None, gamma_flip=None)
    )
    text = format_gex_dex_section(result, "BTC")
    assert "Call Resistance: None found" in text
    assert "Put Support: None found" in text
    assert "Zero Gamma Level: Not detected" in text


def test_gex_dex_section_strike_table_ascending_with_notes():
    text = format_gex_dex_section(_make_result(), "BTC")
    idx_90k = text.index("90,000")
    idx_95k = text.index("95,000", idx_90k)
    assert idx_90k < idx_95k
    line_90k = next(l for l in text.splitlines() if l.strip().startswith("90,000"))
    assert "Put Support" in line_90k
    assert "Zero Gamma Level" not in line_90k  # hvl is 92000, not a strike row here
    line_95k = next(l for l in text.splitlines() if l.strip().startswith("95,000"))
    assert "Call Resistance" in line_95k


def test_gex_dex_section_neutral_environment():
    result = _make_result(total_net_gex=0.0, total_net_dex=0.0)
    text = format_gex_dex_section(result, "BTC")
    assert "GEX Environment: Neutral" in text
    assert "DEX Environment: Neutral" in text


def test_aggregate_gex_dex_section_has_expiration_count_and_no_strike_table():
    result = _make_result(expiration_count=5)
    text = format_aggregate_gex_dex_section(result, spot_price=93000.0, currency="BTC")
    assert "MARKET-WIDE GEX/DEX LEVELS (All 5 Expirations Aggregated)" in text
    assert "Spot Price: $93,000.00" in text
    assert "GEX/DEX BY STRIKE" not in text  # no per-strike table in the aggregate section
    assert "Total Net GEX: +500,000.00 USD" in text
