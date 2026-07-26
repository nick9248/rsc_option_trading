"""
Unit tests for coding.core.analytics.reporting.market_wide_formatter
(refactor_design_spec.md section T3).

None-input cases exercise each function's "no data" text, matching the
legacy MarketWideCalculator methods' early-return message for an
unavailable/uncalculated phase.
"""

from coding.core.analytics.reporting.market_wide_formatter import (
    format_block_trades_section,
    format_cross_asset_correlation_section,
    format_futures_basis_section,
    format_perpetual_funding_section,
    format_realized_volatility_section,
    format_term_structure_section,
    format_volatility_cone_section,
    format_vrp_section,
)
from coding.core.analytics.results.market_wide_results import (
    BlockTrade,
    BlockTradesResult,
    CrossAssetCorrelationResult,
    FuturesBasisEntry,
    FuturesBasisResult,
    PerpetualFundingResult,
    RealizedVolatilityResult,
    TermStructureEntry,
    TermStructureResult,
    VarianceRiskPremiumResult,
    VolatilityConeResult,
    VolatilityConeWindowStats,
)


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
    result = BlockTradesResult(trades=(), notional_threshold=100_000.0, total_detected=0)
    text = format_block_trades_section(result)
    assert "No block trades detected in recent activity" in text


def test_block_trades_rendered():
    result = BlockTradesResult(
        trades=(
            BlockTrade(
                timestamp=1700000000000, instrument_name="BTC-28FEB26-100000-C",
                amount=5.0, direction="buy", notional=500_000.0, implied_volatility=70.0,
            ),
        ),
        notional_threshold=100_000.0, total_detected=1,
    )
    text = format_block_trades_section(result)
    assert "BLOCK TRADES (>$100,000 notional)" in text
    assert "BTC-28FEB26-100000-C" in text
    assert "70.0%" in text


# ---------------------------------------------------------------------------
# Cross-asset correlation
# ---------------------------------------------------------------------------

def test_cross_asset_correlation_no_data():
    text = format_cross_asset_correlation_section(None, currency="BTC")
    assert "CROSS-ASSET CORRELATION (30d, BTC/)" in text
    assert "Price Correlation: Insufficient data" in text
    assert "DVOL Correlation: N/A" in text


def test_cross_asset_correlation_rendered():
    result = CrossAssetCorrelationResult(
        other_currency="ETH", price_correlation=0.85, dvol_correlation=0.6, sample_size=30,
    )
    text = format_cross_asset_correlation_section(result, currency="BTC")
    assert "CROSS-ASSET CORRELATION (30d, BTC/ETH)" in text
    assert "Price Correlation: 0.85" in text
    assert "DVOL Correlation: 0.60" in text
