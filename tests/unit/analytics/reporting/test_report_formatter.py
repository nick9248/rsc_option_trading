"""
Unit tests for coding.core.analytics.reporting.report_formatter
(refactor_design_spec.md section T3).

Covers OnChainReportFormatter's header/expiration/market-wide rendering and
the exact block-joining semantics render_full relies on to reproduce
OnChainAnalyzer.generate_report()'s single flat "\n".join(lines) output byte
for byte (proven end-to-end by the golden-master characterization suite).
"""

from datetime import datetime

from coding.core.analytics.reporting.report_formatter import (
    ExpirationRenderInput,
    OnChainReportFormatter,
)
from coding.core.analytics.results.analysis_result import MarketMetricsResult
from coding.core.analytics.results.expiry_results import (
    ExpirationAnalysisResult,
    MaxPainResult,
    MoneynessLeg,
    MoneynessResult,
    PutCallRatioResult,
    SupportResistanceResult,
    VolumeStatsResult,
)

GENERATED_AT = datetime(2026, 7, 25, 12, 0, 0)


def _leg():
    return MoneynessLeg(
        itm_oi=0.0, otm_oi=0.0, total_oi=0.0, itm_notional=0.0,
        otm_notional=0.0, total_notional=0.0, itm_pct=0.0, otm_pct=0.0,
    )


def _make_analysis(expiration: str) -> ExpirationAnalysisResult:
    return ExpirationAnalysisResult(
        expiration=expiration,
        underlying_price=95000.0,
        total_instruments=0,
        call_count=0,
        put_count=0,
        strike_rows=(),
        max_pain=MaxPainResult(max_pain_strike=None, pain_by_strike={}, min_pain_value=0.0),
        put_call_ratio=PutCallRatioResult(total_call_oi=0.0, total_put_oi=0.0, ratio=0.0, bias="Neutral"),
        volume_stats=VolumeStatsResult(total_call_volume=0.0, total_put_volume=0.0, total_volume=0.0, volume_ratio=0.0),
        moneyness=MoneynessResult(calls=_leg(), puts=_leg(), totals=_leg(), oi_skew="Balanced"),
        support_resistance=SupportResistanceResult(
            resistance_levels=(), support_levels=(), short_term_resistance=None, short_term_support=None,
        ),
    )


# ---------------------------------------------------------------------------
# render_header
# ---------------------------------------------------------------------------

def test_render_header_without_market_metrics():
    formatter = OnChainReportFormatter()
    text = formatter.render_header("BTC", 95000.0, GENERATED_AT, None)
    assert "ON CHAIN ANALYSIS REPORT" in text
    assert "Generated: 2026-07-25 12:00:00" in text
    assert "Currency: BTC" in text
    assert "Current Underlying Price: $95,000.00" in text
    assert "MARKET METRICS" not in text


def test_render_header_with_market_metrics():
    formatter = OnChainReportFormatter()
    metrics = MarketMetricsResult(dvol=75.0, iv_percentile=90.0, iv_rank=80.0, current_funding=0.0001, funding_8h=0.0001)
    text = formatter.render_header("BTC", 95000.0, GENERATED_AT, metrics)
    assert "MARKET METRICS" in text
    assert "DVOL (Volatility Index): 75.00" in text
    assert "IV Percentile (365d): 90.0%" in text
    assert "IV Rank (365d): 80.0%" in text
    assert "Expected Daily Move:" in text
    assert "Current Funding Rate:" in text
    assert "8h Funding Rate:" in text


def test_render_header_ends_with_single_trailing_newline_no_metrics():
    formatter = OnChainReportFormatter()
    text = formatter.render_header("BTC", 95000.0, GENERATED_AT, None)
    assert text.endswith("$95,000.00\n" + "=" * 80 + "\n")


# ---------------------------------------------------------------------------
# render_expiration
# ---------------------------------------------------------------------------

def test_render_expiration_includes_header_and_analysis_and_closing_separator():
    formatter = OnChainReportFormatter()
    render_input = ExpirationRenderInput(expiration="10MAR26", analysis=_make_analysis("10MAR26"))
    text = formatter.render_expiration(render_input, spot_price=95000.0)
    assert text.startswith("EXPIRATION: 10MAR26\n" + "-" * 80 + "\n")
    assert "Total Instruments: 0 (0 Calls, 0 Puts)" in text
    assert text.rstrip("\n").endswith("=" * 80)


def test_render_expiration_appends_extra_sections_in_order():
    formatter = OnChainReportFormatter()
    render_input = ExpirationRenderInput(
        expiration="10MAR26",
        analysis=_make_analysis("10MAR26"),
        extra_sections=("GEX/DEX TEXT", "FLOW TEXT"),
    )
    text = formatter.render_expiration(render_input, spot_price=95000.0)
    assert text.index("GEX/DEX TEXT") < text.index("FLOW TEXT")


# ---------------------------------------------------------------------------
# render_market_wide
# ---------------------------------------------------------------------------

def test_render_market_wide_empty_returns_empty_string():
    formatter = OnChainReportFormatter()
    assert formatter.render_market_wide({}) == ""


def test_render_market_wide_orders_sections_per_legacy_fixed_order():
    formatter = OnChainReportFormatter()
    sections = {
        "block_trades": "BLOCK TEXT",
        "aggregate_gex_dex": "GEXDEX TEXT",
        "vrp": "VRP TEXT",
    }
    text = formatter.render_market_wide(sections)
    assert "MARKET-WIDE METRICS" in text
    assert text.index("GEXDEX TEXT") < text.index("VRP TEXT") < text.index("BLOCK TEXT")


def test_render_market_wide_skips_unknown_keys():
    formatter = OnChainReportFormatter()
    text = formatter.render_market_wide({"not_a_real_section": "TEXT"})
    assert "TEXT" not in text
    assert "MARKET-WIDE METRICS" in text


# ---------------------------------------------------------------------------
# render_full — block-joining semantics
# ---------------------------------------------------------------------------

def test_render_full_joins_header_expirations_and_market_wide_with_blank_lines():
    formatter = OnChainReportFormatter()
    expirations = (
        ExpirationRenderInput(expiration="10MAR26", analysis=_make_analysis("10MAR26")),
        ExpirationRenderInput(expiration="28MAR26", analysis=_make_analysis("28MAR26")),
    )
    text = formatter.render_full(
        currency="BTC",
        underlying_price=95000.0,
        generated_at=GENERATED_AT,
        market_metrics=None,
        expirations=expirations,
        market_wide_sections={"vrp": "VRP TEXT"},
    )

    assert "EXPIRATION: 10MAR26" in text
    assert "EXPIRATION: 28MAR26" in text
    assert "MARKET-WIDE METRICS" in text
    assert text.index("EXPIRATION: 10MAR26") < text.index("EXPIRATION: 28MAR26") < text.index("MARKET-WIDE METRICS")

    # Blank-line separation: header's own trailing blank + the outer join's \n
    # produces exactly one blank line before the first EXPIRATION line.
    header_end = text.index("EXPIRATION: 10MAR26")
    assert text[:header_end].endswith("\n\n")


def test_render_full_without_market_wide_sections_omits_the_block():
    formatter = OnChainReportFormatter()
    expirations = (ExpirationRenderInput(expiration="10MAR26", analysis=_make_analysis("10MAR26")),)
    text = formatter.render_full(
        currency="BTC",
        underlying_price=95000.0,
        generated_at=GENERATED_AT,
        market_metrics=None,
        expirations=expirations,
        market_wide_sections={},
    )
    assert "MARKET-WIDE METRICS" not in text
    # Report ends with the last expiration's own closing separator, one trailing \n.
    assert text.endswith("=" * 80 + "\n")
    assert not text.endswith("=" * 80 + "\n\n")
