"""
Unit tests for coding.core.analytics.reporting.report_formatter
(refactor_design_spec.md section T3).

Covers OnChainReportFormatter's header/expiration/market-wide rendering and
the exact block-joining semantics render_full relies on to reproduce
OnChainAnalyzer.generate_report()'s single flat "\n".join(lines) output byte
for byte (proven end-to-end by the golden-master characterization suite).
"""

from datetime import date, datetime

from coding.core.analytics.reporting.report_formatter import (
    ExpirationRenderInput,
    OnChainReportFormatter,
)
from coding.core.analytics.results.analysis_result import (
    ExpirationBundle,
    MarketMetricsResult,
    OnChainAnalysisResult,
)
from coding.core.analytics.results.expiry_results import (
    ExpirationAnalysisResult,
    MaxPainResult,
    MoneynessLeg,
    MoneynessResult,
    PutCallRatioResult,
    SupportResistanceResult,
    VolumeStatsResult,
)
from coding.core.analytics.results.fixed_strike_vol_results import FixedStrikeVolResult
from coding.core.analytics.results.flow_results import FlowResult, FlowTotals
from coding.core.analytics.results.gex_dex_results import GexDexKeyLevels, GexDexResult
from coding.core.analytics.results.market_wide_results import (
    GammaRolloffResult,
    GammaRolloffRow,
    MarketWideResult,
    PerpetualFundingResult,
    SkewTermStructureEntry,
    SkewTermStructureResult,
    TermStructureEntry,
    TermStructureResult,
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


def test_render_header_funding_annualization_uses_funding_8h_not_current_funding():
    """
    CARRIED FINDING #2 (A5 review, task A6 brief): render_header used to
    compute funding_annualized = current_funding * 3 * 365 * 100 --
    current_funding is the instantaneous accruing rate, funding_8h is the
    realised 8h rate; bugfix_spec.md Item 4 defect (b) already fixed the
    calculator's own annualization to use funding_8h but missed this
    second site. Live evidence cited in the bug report: current_funding =
    5.562e-05, funding_8h = 9.1e-07 two hours apart from current_funding =
    0.0, funding_8h = 1.41e-06 -- a 61x divergence between the two bases.
    """
    formatter = OnChainReportFormatter()
    # Deliberately divergent values so the two possible formulas disagree.
    metrics = MarketMetricsResult(
        dvol=None, iv_percentile=None, iv_rank=None,
        current_funding=5.562e-05, funding_8h=9.1e-07,
    )
    text = formatter.render_header("BTC", 95000.0, GENERATED_AT, metrics)

    # Correct: 9.1e-07 * 1095 * 100 = 0.0996...% -- NOT the current_funding-based
    # 5.562e-05 * 1095 * 100 = 6.0904...% the old formula would have printed.
    assert "0.10% annualized" in text
    assert "6.09% annualized" not in text


def test_render_header_current_funding_without_funding_8h_omits_annualized():
    """
    With no funding_8h available, there is no correct annualization basis
    -- the line must show the instantaneous rate without a fabricated
    annualized figure, rather than falling back to the wrong formula.
    """
    formatter = OnChainReportFormatter()
    metrics = MarketMetricsResult(
        dvol=None, iv_percentile=None, iv_rank=None,
        current_funding=0.0001, funding_8h=None,
    )
    text = formatter.render_header("BTC", 95000.0, GENERATED_AT, metrics)

    assert "Current Funding Rate: 0.0100%" in text
    assert "annualized" not in text


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


# NOTE (task A7, carried finding #1): this section used to test
# OnChainReportFormatter.render_full (the legacy per-argument full-report
# path) directly -- test_render_full_joins_header_expirations_and_market_
# wide_with_blank_lines and test_render_full_without_market_wide_sections_
# omits_the_block. render_full is deleted (zero production callers --
# render_full_from_result is the sole live full-report path since T10).
# Both scenarios are already redundantly covered below by
# test_render_full_from_result_matches_manual_composition (equivalent to
# the blank-line-joining assertion, since both build the same "\n".join(
# blocks)) and test_render_full_from_result_omits_market_wide_block_when_
# empty (identical assertions, same trailing-separator check).

# ---------------------------------------------------------------------------
# T8: render_header_from_result / render_expiration_from_result /
# render_market_wide_from_result — render directly from the typed
# OnChainAnalysisResult, no string scanning (refactor_design_spec.md T8).
# ---------------------------------------------------------------------------

def _make_empty_market_wide(spot_price: float = 95000.0) -> MarketWideResult:
    return MarketWideResult(
        spot_price=spot_price, currency="BTC", dvol=None, iv_percentile_365d=None,
        aggregate_gex_dex=None, term_structure=None, futures_basis=None,
        realized_volatility=None, variance_risk_premium=None, volatility_cone=None,
        perpetual_funding=None, block_trades=None, cross_asset_correlation=None,
        failed_sections=(),
    )


def _make_result(expiration: str = "10MAR26", **overrides) -> OnChainAnalysisResult:
    bundle = ExpirationBundle(
        expiration=expiration, analysis=_make_analysis(expiration), gex_dex=None,
        flow=None, vol_surface=None, oi_changes=None, iv_percentile=None, trend=None,
        flow_chart_paths={}, enriched_instruments=(),
    )
    defaults = dict(
        currency="BTC", underlying_price=95000.0, generated_at=GENERATED_AT,
        market_metrics=None, expirations=(bundle,), market_wide=_make_empty_market_wide(),
        parsed_instruments={expiration: ()}, atm_iv_by_expiration={}, recent_trades=(),
    )
    defaults.update(overrides)
    return OnChainAnalysisResult(**defaults)


def test_render_header_from_result_matches_render_header():
    formatter = OnChainReportFormatter()
    metrics = MarketMetricsResult(dvol=75.0, iv_percentile=90.0, iv_rank=80.0, current_funding=0.0001, funding_8h=0.0001)
    result = _make_result(market_metrics=metrics)

    from_result = formatter.render_header_from_result(result)
    from_args = formatter.render_header("BTC", 95000.0, GENERATED_AT, metrics)
    assert from_result == from_args


def test_render_expiration_from_result_unknown_expiration_returns_empty():
    formatter = OnChainReportFormatter()
    result = _make_result("10MAR26")
    assert formatter.render_expiration_from_result(result, "NOTFOUND") == ""


def test_render_expiration_from_result_includes_evidence_line_when_flow_present():
    formatter = OnChainReportFormatter()
    flow = FlowResult(
        flow_data={}, expiration_totals=FlowTotals(0.0, 0.0, 0.0, 0.0),
        bias_interpretation="Insufficient flow data", flow_trend="Insufficient flow data",
        top_buy_strikes=(), top_sell_strikes=(), trade_count=3, spot_price=95000.0,
        window_start_ms=0, window_end_ms=86_400_000, lookback_hours=24.0,
        sufficient_data=False, low_confidence=False,
    )
    bundle = ExpirationBundle(
        expiration="10MAR26", analysis=_make_analysis("10MAR26"), gex_dex=None, flow=flow,
        vol_surface=None, oi_changes=None, iv_percentile=None, trend=None,
        flow_chart_paths={}, enriched_instruments=(),
    )
    result = _make_result(expirations=(bundle,))
    text = formatter.render_expiration_from_result(result, "10MAR26")
    assert "EVIDENCE: OI/GEX from full book | Flow: INSUFFICIENT (3 trades in 24h)" in text


def test_render_expiration_from_result_includes_gex_dex_section_when_present():
    formatter = OnChainReportFormatter()
    gex_dex = GexDexResult(
        strike_rows=(), cumulative_gex={}, cumulative_dex={},
        key_levels=GexDexKeyLevels(call_resistance=None, put_support=None, hvl=None, gamma_flip=None),
        spot_price=95000.0, total_net_gex=1234.5, total_net_dex=6.7, currency="BTC",
    )
    bundle = ExpirationBundle(
        expiration="10MAR26", analysis=_make_analysis("10MAR26"), gex_dex=gex_dex, flow=None,
        vol_surface=None, oi_changes=None, iv_percentile=None, trend=None,
        flow_chart_paths={}, enriched_instruments=(),
    )
    result = _make_result(expirations=(bundle,))
    text = formatter.render_expiration_from_result(result, "10MAR26")
    assert "GEX/DEX ANALYSIS" in text
    assert "+1,234.50" in text


def test_render_expiration_from_result_includes_fixed_strike_vol_section_when_present():
    """institutional_metrics_spec.md section 7 / Task C8: even an
    INDETERMINATE (insufficient-history) result must render its explicit
    message, not silently disappear -- unlike gex_dex/vol_surface's plain
    'no data -> no section' convention, a PRESENT bundle.fixed_strike_vol
    is never dropped regardless of its regime."""
    formatter = OnChainReportFormatter()
    fixed_strike_vol = FixedStrikeVolResult(
        expiration="10MAR26", today_date=date(2026, 7, 31),
        prior_date=None, expected_prior_date=date(2026, 7, 30),
        stale_prior=True, spot_today=95000.0, spot_prior=None,
        spot_move_pct=None, atm_iv_today=None, atm_iv_prior=None, d_atm=None,
        rows=(), n_strikes_matched=0, n_strikes_unmatched=0, regime="INDETERMINATE",
    )
    bundle = ExpirationBundle(
        expiration="10MAR26", analysis=_make_analysis("10MAR26"), gex_dex=None, flow=None,
        vol_surface=None, oi_changes=None, iv_percentile=None, trend=None,
        flow_chart_paths={}, enriched_instruments=(), fixed_strike_vol=fixed_strike_vol,
    )
    result = _make_result(expirations=(bundle,))
    text = formatter.render_expiration_from_result(result, "10MAR26")
    assert "FIXED-STRIKE VOL CHANGE" in text
    assert "no comparable prior snapshot" in text


def test_render_market_wide_from_result_empty_returns_empty_string():
    formatter = OnChainReportFormatter()
    result = _make_result()
    assert formatter.render_market_wide_from_result(result) == ""


def test_render_market_wide_from_result_includes_present_sections_only():
    formatter = OnChainReportFormatter()
    funding = PerpetualFundingResult(
        perp_open_interest=1_000_000.0, funding_rate=0.0001, funding_8h=0.0002,
        funding_trend="Stable", history_points=10,
    )
    mw = MarketWideResult(
        spot_price=95000.0, currency="BTC", dvol=None, iv_percentile_365d=None,
        aggregate_gex_dex=None, term_structure=None, futures_basis=None,
        realized_volatility=None, variance_risk_premium=None, volatility_cone=None,
        perpetual_funding=funding, block_trades=None, cross_asset_correlation=None,
        failed_sections=(),
    )
    result = _make_result(market_wide=mw)
    text = formatter.render_market_wide_from_result(result)
    assert "MARKET-WIDE METRICS" in text
    assert "PERPETUAL FUNDING & OI" in text
    assert "FUTURES BASIS" not in text
    assert "IV TERM STRUCTURE" not in text


def test_render_market_wide_from_result_includes_skew_term_structure_before_iv_term_structure():
    """institutional_metrics_spec.md section 9(b): SKEW TERM STRUCTURE
    (section 3) renders before IV TERM STRUCTURE in the market-wide order
    (Task C4)."""
    formatter = OnChainReportFormatter()
    skew_entry = SkewTermStructureEntry(
        expiration="25JUL26", dte=0.6, atm_iv_interp=18.51, n_quotes_used=14,
        rr_25d=-3.80, rr_percentile_30d=None, rr_regime_30d=None, rr_n_30d=0,
        bf_25d=0.90, bf_percentile_30d=None, bf_n_30d=0,
    )
    skew_result = SkewTermStructureResult(entries=(skew_entry,), rr_slope=None)
    atm_ivs = {"25JUL26": 18.51}
    term_structure = TermStructureResult(
        entries=(TermStructureEntry(expiration="25JUL26", dte=0, atm_iv=18.51),),
        shape="FLAT", spread=0.0, spread_signed=0.0, iv_by_dte=atm_ivs,
    )
    mw = MarketWideResult(
        spot_price=95000.0, currency="BTC", dvol=None, iv_percentile_365d=None,
        aggregate_gex_dex=None, term_structure=term_structure, futures_basis=None,
        realized_volatility=None, variance_risk_premium=None, volatility_cone=None,
        perpetual_funding=None, block_trades=None, cross_asset_correlation=None,
        failed_sections=(), skew_term_structure=skew_result,
    )
    result = _make_result(market_wide=mw)
    text = formatter.render_market_wide_from_result(result)

    assert "SKEW TERM STRUCTURE" in text
    assert "IV TERM STRUCTURE" in text
    assert text.index("SKEW TERM STRUCTURE") < text.index("IV TERM STRUCTURE")


def test_render_market_wide_from_result_omits_skew_term_structure_when_none():
    formatter = OnChainReportFormatter()
    result = _make_result()  # _make_empty_market_wide() -> skew_term_structure defaults to None
    text = formatter.render_market_wide_from_result(result)
    assert "SKEW TERM STRUCTURE" not in text


def test_render_market_wide_from_result_includes_gamma_rolloff_after_aggregate_gex_dex():
    """institutional_metrics_spec.md section 9(b): GAMMA ROLL-OFF (section
    5) renders immediately after AGGREGATE GEX/DEX and before SKEW TERM
    STRUCTURE (Task C6)."""
    formatter = OnChainReportFormatter()
    aggregate_gex_dex = GexDexResult(
        strike_rows=(), cumulative_gex={}, cumulative_dex={},
        key_levels=GexDexKeyLevels(call_resistance=None, put_support=None, hvl=None, gamma_flip=None),
        spot_price=95000.0, total_net_gex=100_000_000.0, total_net_dex=0.0, currency="BTC",
        expiration_count=1,
    )
    gamma_rolloff = GammaRolloffResult(
        rows=(
            GammaRolloffRow(
                expiration="25JUL26", dte_days=0.6, net_gex=100_000_000.0,
                share_pct=100.0, cum_share_pct=100.0, cum_net_gex=100_000_000.0,
            ),
        ),
        gamma_cliff_7d=True, cum_share_7d=100.0, cum_share_30d=100.0,
        gross_total=100_000_000.0,
    )
    skew_entry = SkewTermStructureEntry(
        expiration="25JUL26", dte=0.6, atm_iv_interp=18.51, n_quotes_used=14,
        rr_25d=-3.80, rr_percentile_30d=None, rr_regime_30d=None, rr_n_30d=0,
        bf_25d=0.90, bf_percentile_30d=None, bf_n_30d=0,
    )
    skew_result = SkewTermStructureResult(entries=(skew_entry,), rr_slope=None)
    mw = MarketWideResult(
        spot_price=95000.0, currency="BTC", dvol=None, iv_percentile_365d=None,
        aggregate_gex_dex=aggregate_gex_dex, term_structure=None, futures_basis=None,
        realized_volatility=None, variance_risk_premium=None, volatility_cone=None,
        perpetual_funding=None, block_trades=None, cross_asset_correlation=None,
        failed_sections=(), skew_term_structure=skew_result, gamma_rolloff=gamma_rolloff,
    )
    result = _make_result(market_wide=mw)
    text = formatter.render_market_wide_from_result(result)

    assert "GAMMA ROLL-OFF" in text
    assert "GAMMA CLIFF" in text
    assert text.index("MARKET-WIDE GEX/DEX LEVELS") < text.index("GAMMA ROLL-OFF")
    assert text.index("GAMMA ROLL-OFF") < text.index("SKEW TERM STRUCTURE")


def test_render_market_wide_from_result_omits_gamma_rolloff_when_none():
    formatter = OnChainReportFormatter()
    result = _make_result()  # _make_empty_market_wide() -> gamma_rolloff defaults to None
    text = formatter.render_market_wide_from_result(result)
    assert "GAMMA ROLL-OFF" not in text


# ---------------------------------------------------------------------------
# T10: render_full_from_result -- flips render_market_wide_from_result live
# (composes header + expirations + market-wide, all from the typed result).
# ---------------------------------------------------------------------------

def test_render_full_from_result_matches_manual_composition():
    formatter = OnChainReportFormatter()
    funding = PerpetualFundingResult(
        perp_open_interest=1_000_000.0, funding_rate=0.0001, funding_8h=0.0002,
        funding_trend="Stable", history_points=10,
    )
    mw = MarketWideResult(
        spot_price=95000.0, currency="BTC", dvol=None, iv_percentile_365d=None,
        aggregate_gex_dex=None, term_structure=None, futures_basis=None,
        realized_volatility=None, variance_risk_premium=None, volatility_cone=None,
        perpetual_funding=funding, block_trades=None, cross_asset_correlation=None,
        failed_sections=(),
    )
    metrics = MarketMetricsResult(dvol=75.0, iv_percentile=90.0, iv_rank=80.0, current_funding=0.0001, funding_8h=0.0001)
    result = _make_result(market_metrics=metrics, market_wide=mw)

    full_text = formatter.render_full_from_result(result)

    expected_blocks = [formatter.render_header_from_result(result)]
    for expiration in result.expiration_names():
        expected_blocks.append(formatter.render_expiration_from_result(result, expiration))
    expected_blocks.append(formatter.render_market_wide_from_result(result))
    expected = "\n".join(expected_blocks)

    assert full_text == expected
    assert "ON CHAIN ANALYSIS REPORT" in full_text
    assert "EXPIRATION: 10MAR26" in full_text
    assert "MARKET-WIDE METRICS" in full_text
    assert "PERPETUAL FUNDING & OI" in full_text


def test_render_full_from_result_omits_market_wide_block_when_empty():
    formatter = OnChainReportFormatter()
    result = _make_result()  # _make_empty_market_wide() -> every sub-result None

    text = formatter.render_full_from_result(result)

    assert "MARKET-WIDE METRICS" not in text
    assert text.endswith("=" * 80 + "\n")
    assert not text.endswith("=" * 80 + "\n\n")


def test_render_full_from_result_renders_multiple_expirations_in_order():
    formatter = OnChainReportFormatter()
    bundle_a = ExpirationBundle(
        expiration="10MAR26", analysis=_make_analysis("10MAR26"), gex_dex=None, flow=None,
        vol_surface=None, oi_changes=None, iv_percentile=None, trend=None,
        flow_chart_paths={}, enriched_instruments=(),
    )
    bundle_b = ExpirationBundle(
        expiration="27JUN26", analysis=_make_analysis("27JUN26"), gex_dex=None, flow=None,
        vol_surface=None, oi_changes=None, iv_percentile=None, trend=None,
        flow_chart_paths={}, enriched_instruments=(),
    )
    result = _make_result(expirations=(bundle_a, bundle_b))

    text = formatter.render_full_from_result(result)

    idx_a = text.index("EXPIRATION: 10MAR26")
    idx_b = text.index("EXPIRATION: 27JUN26")
    assert idx_a < idx_b
