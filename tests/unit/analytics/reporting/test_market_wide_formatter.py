"""
Unit tests for coding.core.analytics.reporting.market_wide_formatter
(refactor_design_spec.md section T3).

None-input cases exercise each function's "no data" text, matching the
legacy MarketWideCalculator methods' early-return message for an
unavailable/uncalculated phase.
"""

from datetime import datetime

from coding.core.analytics.reporting.market_wide_formatter import (
    format_block_trades_section,
    format_cross_asset_correlation_line,
    format_expected_move_line,
    format_futures_basis_section,
    format_market_wide_context_section,
    format_perpetual_funding_section,
    format_realized_volatility_section,
    format_skew_term_structure_section,
    format_term_structure_section,
    format_volatility_cone_section,
    format_vrp_section,
)
from coding.core.analytics.results.market_wide_results import (
    Block,
    BlockTrade,
    BlockTradesResult,
    CrossAssetCorrelationResult,
    FuturesBasisEntry,
    FuturesBasisResult,
    PerpetualFundingResult,
    RealizedVolatilityResult,
    SkewTermStructureEntry,
    SkewTermStructureResult,
    TermStructureEntry,
    TermStructureResult,
    VarianceRiskPremiumResult,
    VolatilityConeResult,
    VolatilityConeWindowStats,
)


# ---------------------------------------------------------------------------
# Skew term structure (institutional_metrics_spec.md section 3(c), Task C4)
# ---------------------------------------------------------------------------

def _skew_entry(expiration, dte, rr=None, rr_pctile=None, rr_regime=None, rr_n=0,
                 bf=None, bf_pctile=None, bf_n=0, atm=None, n_quotes=None):
    return SkewTermStructureEntry(
        expiration=expiration, dte=dte, atm_iv_interp=atm, n_quotes_used=n_quotes,
        rr_25d=rr, rr_percentile_30d=rr_pctile, rr_regime_30d=rr_regime, rr_n_30d=rr_n,
        bf_25d=bf, bf_percentile_30d=bf_pctile, bf_n_30d=bf_n,
    )


def test_skew_term_structure_none_shows_no_data():
    report = format_skew_term_structure_section(None)
    assert "SKEW TERM STRUCTURE" in report
    assert "No skew data available" in report


def test_skew_term_structure_empty_entries_shows_no_data():
    report = format_skew_term_structure_section(SkewTermStructureResult(entries=(), rr_slope=None))
    assert "No skew data available" in report


def test_skew_term_structure_with_history_shows_percentile_and_regime():
    """Spec's worked example row format: RR25 gets value + percentile +
    regime word; BF25 gets value + percentile only (no regime word)."""
    entry = _skew_entry(
        "25JUL26", dte=0.6, atm=18.51, n_quotes=14,
        rr=-3.80, rr_pctile=12.0, rr_regime="LOW", rr_n=45,
        bf=0.90, bf_pctile=44.0, bf_n=45,
    )
    result = SkewTermStructureResult(entries=(entry,), rr_slope=None)
    report = format_skew_term_structure_section(result)

    assert "25JUL26" in report
    assert "0.6d" in report
    assert "18.51%" in report
    assert "-3.80" in report and "p12 LOW" in report
    assert "+0.90" in report and "p44" in report
    # BF25 column has no regime word after "p44" (unlike RR25's "p12 LOW") --
    # the text immediately following "p44" on the BF25 cell must not carry
    # a regime label the way the RR25 cell does.
    bf_cell_end = report.split("+0.90  p44", 1)[1].split("\n")[0]
    assert "LOW" not in bf_cell_end and "NORMAL" not in bf_cell_end
    assert "14 quotes" in report


def test_skew_term_structure_insufficient_history_fallback():
    """Table starts empty (Decision D10) -- expected state: value present,
    percentile absent, C1's "n/a (N obs)" fallback shown instead of a
    fabricated percentile."""
    entry = _skew_entry(
        "25JUL26", dte=0.6, atm=18.51, n_quotes=14,
        rr=-3.80, rr_pctile=None, rr_regime=None, rr_n=0,
        bf=0.90, bf_pctile=None, bf_n=0,
    )
    result = SkewTermStructureResult(entries=(entry,), rr_slope=None)
    report = format_skew_term_structure_section(result)

    assert "n/a (0 obs)" in report
    assert "-3.80" in report
    assert "+0.90" in report
    # Must never fabricate a percentile when history is insufficient.
    assert "p0" not in report and " p " not in report


def test_skew_term_structure_insufficient_chain_row():
    """T3.2/T3.3: rr_25d/bf_25d None (chain does not bracket 25-delta) must
    print 'insufficient chain', never a fabricated 0.0."""
    entry = _skew_entry("7AUG26", dte=13.0, atm=None, n_quotes=4, rr=None, bf=None)
    result = SkewTermStructureResult(entries=(entry,), rr_slope=None)
    report = format_skew_term_structure_section(result)

    assert "insufficient chain" in report
    assert "0.00" not in report


def test_skew_term_structure_rr_slope_rendered():
    entries = (
        _skew_entry("25JUL26", dte=0.6, rr=-3.80, bf=0.90),
        _skew_entry("25DEC26", dte=153.6, rr=-4.34, bf=2.85),
    )
    result = SkewTermStructureResult(entries=entries, rr_slope=-0.54)
    report = format_skew_term_structure_section(result)

    assert "RR slope (front->back): -0.54 vol pts" in report
    assert "back-month more put-skewed" in report


def test_skew_term_structure_no_slope_line_when_none():
    entry = _skew_entry("25JUL26", dte=0.6, rr=-3.80, bf=0.90)
    result = SkewTermStructureResult(entries=(entry,), rr_slope=None)
    report = format_skew_term_structure_section(result)
    assert "RR slope" not in report


# ---------------------------------------------------------------------------
# Term structure
# ---------------------------------------------------------------------------

def test_term_structure_none_shows_no_data():
    text = format_term_structure_section(None)
    assert "No ATM IV data available" in text


def test_term_structure_contango():
    result = TermStructureResult(
        entries=(
            TermStructureEntry(expiration="28FEB26", dte=30, atm_iv=60.0),
            TermStructureEntry(expiration="27JUN26", dte=150, atm_iv=65.0),
        ),
        shape="CONTANGO", spread=5.0, spread_signed=5.0, iv_by_dte={30: 60.0, 150: 65.0},
    )
    text = format_term_structure_section(result)
    assert "28FEB26" in text and "27JUN26" in text
    assert "Structure: CONTANGO (+5.0 pts)" in text


def test_term_structure_backwardated():
    result = TermStructureResult(
        entries=(
            TermStructureEntry(expiration="28FEB26", dte=30, atm_iv=70.0),
            TermStructureEntry(expiration="27JUN26", dte=150, atm_iv=60.0),
        ),
        shape="BACKWARDATION", spread=10.0, spread_signed=-10.0, iv_by_dte={30: 70.0, 150: 60.0},
    )
    text = format_term_structure_section(result)
    assert "Structure: BACKWARDATED (-10.0 pts)" in text


def test_term_structure_no_line_when_fewer_than_two_entries():
    result = TermStructureResult(
        entries=(TermStructureEntry(expiration="28FEB26", dte=30, atm_iv=70.0),),
        shape="FLAT", spread=0.0, spread_signed=0.0, iv_by_dte={30: 70.0},
    )
    text = format_term_structure_section(result)
    assert "Structure:" not in text


# ---------------------------------------------------------------------------
# Futures basis
# ---------------------------------------------------------------------------

def test_futures_basis_no_data():
    text = format_futures_basis_section(None)
    assert "No futures data available" in text


def test_futures_basis_rendered():
    result = FuturesBasisResult(
        entries=(
            FuturesBasisEntry(
                instrument_name="BTC-28FEB26", dte=30, mark_price=96000.0,
                index_price=95000.0, annualized_premium_pct=12.5,
            ),
        ),
        futures_basis={"28FEB26": 12.5},
    )
    text = format_futures_basis_section(result)
    assert "BTC-28FEB26" in text
    assert "12.5%" in text
    assert "annualized simple, ACT/365, to 08:00 UTC settlement" in text


def test_futures_basis_none_annualized_premium_renders_na_not_crash():
    """bugfix_spec.md Item 5 / Decision D12: annualized_premium_pct may be
    None (suppressed sub-daily tenor) — must not TypeError on the format spec."""
    result = FuturesBasisResult(
        entries=(
            FuturesBasisEntry(
                instrument_name="BTC-26JUL26", dte=0, mark_price=100_200.0,
                index_price=100_000.0, annualized_premium_pct=None,
            ),
        ),
        futures_basis={"26JUL26": None},
    )
    text = format_futures_basis_section(result)
    assert "BTC-26JUL26" in text
    assert "n/a" in text


# ---------------------------------------------------------------------------
# Realized volatility
# ---------------------------------------------------------------------------

def test_realized_volatility_insufficient_data():
    text = format_realized_volatility_section(None)
    assert "Insufficient price history" in text


def test_realized_volatility_rendered():
    result = RealizedVolatilityResult(rv_by_window={10: 0.40, 20: 0.45, 30: 0.50})
    text = format_realized_volatility_section(result)
    assert "10d: 40.0%" in text
    assert "20d: 45.0%" in text
    assert "30d: 50.0%" in text


# ---------------------------------------------------------------------------
# VRP
# ---------------------------------------------------------------------------

def test_vrp_dvol_not_available():
    text = format_vrp_section(None)
    assert "DVOL not available" in text


def test_vrp_expensive_signal_advises_sell_vol():
    """Real VRPCalculator.calculate_vrp() signal values are VERY_EXPENSIVE/EXPENSIVE/
    FAIR/CHEAP/VERY_CHEAP — not the "RICH"/"FAIR"/"CHEAP" shorthand in the spec's
    field comment. format_vrp_section must match the calculator's actual advice
    mapping (checked against EXPENSIVE/VERY_EXPENSIVE -> "Sell vol")."""
    result = VarianceRiskPremiumResult(vrp=15.0, signal="EXPENSIVE", dvol=65.0, rv_30d=0.5)
    text = format_vrp_section(result)
    assert "DVOL: 65.0%" in text
    assert "30d RV: 50.0%" in text
    assert "VRP: +15.0 pts (EXPENSIVE - Sell vol)" in text


def test_vrp_cheap_signal_advises_buy_vol():
    result = VarianceRiskPremiumResult(vrp=-10.0, signal="CHEAP", dvol=40.0, rv_30d=0.5)
    text = format_vrp_section(result)
    assert "VRP: -10.0 pts (CHEAP - Buy vol)" in text


def test_vrp_fair_signal_neutral():
    result = VarianceRiskPremiumResult(vrp=0.5, signal="FAIR", dvol=50.0, rv_30d=0.5)
    text = format_vrp_section(result)
    assert "FAIR - Neutral" in text


# ---------------------------------------------------------------------------
# Volatility cone
# ---------------------------------------------------------------------------

def test_volatility_cone_insufficient_data():
    text = format_volatility_cone_section(None)
    assert "Insufficient price history for vol cone" in text


def test_volatility_cone_rendered():
    result = VolatilityConeResult(percentile_by_window={10: 25.0, 20: 50.0, 30: 75.0})
    text = format_volatility_cone_section(result)
    assert "10d" in text and "25th" in text
    assert "30d" in text and "75th" in text


def test_volatility_cone_rendered_with_full_stats_shows_six_column_table():
    """
    T10 (refactor_design_spec.md): found live wiring render_market_wide_from_result
    into the main report path -- the legacy text table has 6 columns
    (Window/Current/25th/Median/75th/Pctile), not 2. When stats_by_window
    is populated (the one production call site always populates it), the
    formatter must reproduce the full legacy table byte-for-byte.
    """
    result = VolatilityConeResult(
        percentile_by_window={10: 40.0},
        stats_by_window={
            10: VolatilityConeWindowStats(current_rv=30.8, p25=26.4, p50=35.3, p75=44.7, percentile=40.0),
        },
    )
    text = format_volatility_cone_section(result)
    expected_row = "      10d     30.8%     26.4%     35.3%     44.7%      40th"
    assert expected_row in text
    assert "Current" in text and "Median" in text


# ---------------------------------------------------------------------------
# Perpetual funding
# ---------------------------------------------------------------------------

def test_perpetual_funding_not_available():
    text = format_perpetual_funding_section(None)
    assert "Funding data not available" in text


def test_perpetual_funding_rendered_with_8h():
    """bugfix_spec.md Item 4: annualization uses funding_8h, not
    funding_rate/current_funding (T6, carried from A4 review — this
    dormant formatter must not perpetuate the bug it fixed elsewhere)."""
    result = PerpetualFundingResult(
        perp_open_interest=1_000_000.0, funding_rate=0.0001, funding_8h=1.41e-06,
        funding_trend="Rising", history_points=10,
    )
    text = format_perpetual_funding_section(result)
    assert "Perp OI: 1,000,000 USD" in text
    assert "Funding (8h): 0.0001%" in text
    assert "Trend: Rising" in text
    assert "Instantaneous funding: 0.0100%" in text
    # 1.41e-06 * 3 * 365 * 100 = 0.154395%
    assert "Annualized: 0.15%" in text


def test_perpetual_funding_omits_8h_line_when_none():
    result = PerpetualFundingResult(
        perp_open_interest=1_000_000.0, funding_rate=0.0001, funding_8h=None,
        funding_trend="Stable", history_points=10,
    )
    text = format_perpetual_funding_section(result)
    assert "Funding (8h): not available" in text
    assert "Instantaneous funding: 0.0100%" in text


# ---------------------------------------------------------------------------
# Block trades
# ---------------------------------------------------------------------------

def test_block_trades_no_data():
    text = format_block_trades_section(None)
    assert "No recent trade data available" in text


def test_block_trades_none_detected():
    """institutional_metrics_spec.md section 9 / Migration M2 (Task D1):
    no blocks in the window is an empty section that states the
    tracked-since date -- not a bare "no data" message (that message is
    reserved for the None-result / no-recent-trades case above)."""
    result = BlockTradesResult(
        trades=(), notional_threshold=100_000.0, total_detected=0,
        blocks=(), tracked_since="2026-08-02",
    )
    text = format_block_trades_section(result)
    assert "No blocks detected in recent activity" in text
    assert "2026-08-02" in text
    assert "No large prints detected in recent activity" in text


def test_block_trades_rendered():
    """`trades` (large prints) and `blocks` render as two clearly
    separated, distinctly labelled sections."""
    result = BlockTradesResult(
        trades=(
            BlockTrade(
                timestamp=1700000000000, instrument_name="BTC-28FEB26-100000-C",
                amount=5.0, direction="buy", notional=500_000.0, implied_volatility=70.0,
            ),
        ),
        notional_threshold=100_000.0, total_detected=1,
        blocks=(
            Block(
                block_trade_id="BLOCK-281688", leg_count=3, observed_leg_count=3,
                combo_id="BTC-STRD-31JUL26-63000", combined_premium_usd=1234.5,
                total_amount=37.5,
                instruments=("BTC-31JUL26-63000-C", "BTC-31JUL26-63000-P", "BTC-31JUL26-64000-C"),
                timestamp=1785546525278,
            ),
        ),
        tracked_since="2026-08-02",
    )
    text = format_block_trades_section(result)

    assert "BLOCK TRADES" in text
    assert "BLOCK-281688" in text
    assert "BTC-STRD-31JUL26-63000" in text
    assert "2026-08-02" in text

    assert "LARGE PRINTS" in text
    assert "screen prints" in text.lower()
    assert "BTC-28FEB26-100000-C" in text
    assert "70.0%" in text

    # the two sections are clearly separated: BLOCK TRADES precedes
    # LARGE PRINTS, and the block's own instrument names never appear in
    # the large-prints section (no double-counting rendered).
    assert text.index("BLOCK TRADES") < text.index("LARGE PRINTS")
    large_prints_text = text[text.index("LARGE PRINTS"):]
    for instrument in result.blocks[0].instruments:
        assert instrument not in large_prints_text


def test_block_timestamp_rendered_in_utc_not_local():
    """Independent review round 2 (Important #2): same banned bug class as
    the calculator's own detect_block_trades -- ts=1785546525278 is
    01:08:45 UTC; on a UTC+2 dev machine the old naive
    datetime.fromtimestamp(ts/1000) rendered 03:08:45 instead."""
    result = BlockTradesResult(
        trades=(), notional_threshold=100_000.0, total_detected=0,
        blocks=(
            Block(
                block_trade_id="BLOCK-282155", leg_count=1, observed_leg_count=1,
                combo_id=None, combined_premium_usd=100.0, total_amount=12.5,
                instruments=("BTC-1AUG26-63000-C",), timestamp=1785546525278,
            ),
        ),
        tracked_since="2026-08-02",
    )
    text = format_block_trades_section(result)

    assert "01:08:45" in text
    assert "03:08:45" not in text


def test_large_print_timestamp_rendered_in_utc_not_local():
    """Wave H fresh-audit finding (Task Wave-H-C): the large-prints table
    sits ~38 lines below the block-trades table in the same file/section
    and was missed by the block-trades table's own UTC fix above -- same
    bug, same repro timestamp (ts=1785546525278 is 01:08:45 UTC; the old
    naive datetime.fromtimestamp(ts/1000) rendered 03:08:45 on a UTC+2
    host)."""
    result = BlockTradesResult(
        trades=(
            BlockTrade(
                timestamp=1785546525278, instrument_name="BTC-1AUG26-63000-C",
                amount=5.0, direction="buy", notional=500_000.0, implied_volatility=70.0,
            ),
        ),
        notional_threshold=100_000.0, total_detected=1,
        blocks=(),
        tracked_since="2026-08-02",
    )
    text = format_block_trades_section(result)

    assert "01:08:45" in text
    assert "03:08:45" not in text


def test_block_and_large_print_tables_render_the_same_clock_for_same_timestamp():
    """Regression test for the exact "one table fixed, sibling table
    missed" bug class (Task Wave-H-C): render both tables for a block and
    a large print sharing the SAME timestamp, and assert they render the
    identical wall-clock string. If either table silently reverts to
    naive-local while the other stays UTC, this fails even though each
    table in isolation might still look plausible."""
    shared_timestamp = 1785546525278  # 01:08:45 UTC
    result = BlockTradesResult(
        trades=(
            BlockTrade(
                timestamp=shared_timestamp, instrument_name="BTC-1AUG26-63000-C",
                amount=5.0, direction="buy", notional=500_000.0, implied_volatility=70.0,
            ),
        ),
        notional_threshold=100_000.0, total_detected=1,
        blocks=(
            Block(
                block_trade_id="BLOCK-282155", leg_count=1, observed_leg_count=1,
                combo_id=None, combined_premium_usd=100.0, total_amount=12.5,
                instruments=("BTC-1AUG26-64000-C",), timestamp=shared_timestamp,
            ),
        ),
        tracked_since="2026-08-02",
    )
    text = format_block_trades_section(result)

    block_section = text[text.index("BLOCK TRADES"):text.index("LARGE PRINTS")]
    large_prints_section = text[text.index("LARGE PRINTS"):]

    assert "01:08:45" in block_section
    assert "01:08:45" in large_prints_section
    assert "03:08:45" not in text


# ---------------------------------------------------------------------------
# Cross-asset correlation
# ---------------------------------------------------------------------------

def test_cross_asset_correlation_line_no_data():
    """institutional_metrics_spec.md section 9: one line, no section
    header -- rendered as part of the market-wide CONTEXT block."""
    line = format_cross_asset_correlation_line(None, currency="BTC")
    assert line == "BTC change-correlation (30d): price insufficient data  |  DVOL N/A"


def test_cross_asset_correlation_line_rendered():
    result = CrossAssetCorrelationResult(
        other_currency="ETH", price_correlation=0.85, dvol_correlation=0.6, sample_size=30,
        dvol_correlation_observations=29,
    )
    line = format_cross_asset_correlation_line(result, currency="BTC")
    assert line == (
        "BTC/ETH change-correlation (30d): price 0.85  |  "
        "DVOL 0.60 (log changes, 29d)"
    )


def test_expected_move_line_integer_dollars():
    """institutional_metrics_spec.md section 9: one line, integer dollars."""
    line = format_expected_move_line(dvol=75.0, underlying_price=95000.0)
    assert line.startswith("Expected Move: 1d $")
    assert "." not in line.split("$")[1].split(" ")[0]  # integer, no decimals
    assert "7d $" in line and "30d $" in line


def test_expected_move_line_na_when_no_dvol():
    assert format_expected_move_line(dvol=None, underlying_price=95000.0) == (
        "Expected Move: N/A (no DVOL)"
    )


def test_market_wide_context_section_combines_both_lines():
    result = CrossAssetCorrelationResult(
        other_currency="ETH", price_correlation=0.85, dvol_correlation=0.6, sample_size=30,
        dvol_correlation_observations=29,
    )
    text = format_market_wide_context_section(result, "BTC", 75.0, 95000.0)
    assert text.startswith("CONTEXT\n" + "-" * 80 + "\n")
    assert "change-correlation" in text
    assert "Expected Move:" in text


def test_market_wide_context_section_omits_delta_flow_coverage_by_default():
    text = format_market_wide_context_section(None, "BTC", None, 95000.0)
    assert "Delta-flow coverage" not in text


def test_market_wide_context_section_includes_delta_flow_coverage_when_present():
    """
    Independent review round 2 (Important #2): the delta-flow coverage/
    staleness disclosure format_delta_flow_section used to own is restored
    here, once, market-wide (not repeated per expiry -- see
    delta_flow_formatter.format_delta_flow_coverage_line's docstring).
    """
    text = format_market_wide_context_section(
        None, "BTC", None, 95000.0,
        delta_flow_has_total=True, delta_flow_hours_present=24, delta_flow_lookback_hours=24.0,
    )
    assert "Delta-flow coverage: 24/24h hourly rows persisted" in text


def test_market_wide_context_section_includes_delta_flow_staleness_when_stale():
    stale_ts = datetime(2026, 7, 31, 2, 0, 0)
    text = format_market_wide_context_section(
        None, "BTC", None, 95000.0,
        delta_flow_has_total=True, delta_flow_hours_present=12, delta_flow_lookback_hours=24.0,
        delta_flow_stale_since=stale_ts,
    )
    assert "STALE" in text
    assert "2026-07-31 02:00" in text
