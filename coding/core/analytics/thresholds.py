"""
Named threshold constants for on-chain analytics interpretation labels
(refactor_design_spec.md T12 / code_quality_review.md M8), plus the shared
put/call ratio interpreter (M4) that used to have two divergent label
vocabularies for the identical metric.

Every threshold that turns a numeric ratio/percentage into a
human-readable report label lives here, so it is one findable, documented,
and tunable place instead of being re-declared as an unexplained literal at
each call site.
"""

from typing import Optional

# --- Put/Call ratio interpretation (M4) -----------------------------------
#
# code_quality_review.md M4: OnChainMetricsCalculator.calculate_put_call_ratio
# (per-expiration total P/C ratio) and VolatilitySurfaceCalculator.
# _calculate_pc_by_moneyness (per-moneyness-bucket P/C ratio) independently
# interpreted the identical 0.7/1.0/1.3 boundaries with two different label
# vocabularies in the same report ("Strong Bullish"/"Bullish"/"Neutral"/
# "Bearish"/"Strong Bearish" vs. "Bullish"/"Slightly Bullish"/
# "Slightly Bearish"/"Bearish"). refactor_design_spec.md T12: unifying onto
# one shared function is a PLANNED golden-master delta -- report text that
# used to say "Slightly Bullish"/"Slightly Bearish" now reads "Bullish"/
# "Bearish" (the 5-level vocabulary, matching interpret_put_call_ratio_
# percentile's existing "Strong X"/"X"/"Neutral" naming scheme elsewhere in
# the same report). An inf ratio (call_oi == 0, put_oi > 0) is "N/A" in
# both call sites now -- it is a data-insufficiency case (nothing to divide
# by), not a directional claim.
PC_RATIO_STRONG_BULLISH_THRESHOLD = 0.7
"""ratio < this -> "Strong Bullish"."""

PC_RATIO_NEUTRAL_THRESHOLD = 1.0
"""this <= ratio < PC_RATIO_BEARISH_THRESHOLD -> "Bearish"; ratio ==
this exactly -> "Neutral" (checked before the general < comparison)."""

PC_RATIO_BEARISH_THRESHOLD = 1.3
"""ratio >= this -> "Strong Bearish"."""


def interpret_put_call_ratio(ratio: Optional[float]) -> str:
    """
    Interpret a put/call open-interest ratio into a directional bias label.

    Shared by ``OnChainMetricsCalculator.calculate_put_call_ratio`` (whole
    expiration) and ``VolatilitySurfaceCalculator._calculate_pc_by_moneyness``
    (per moneyness bucket) -- previously each had its own copy of the
    identical 0.7/1.0/1.3 thresholds with a different vocabulary (M4).

    Args:
        ratio: put_oi / call_oi. ``None`` and ``float("inf")`` (call_oi == 0
            with put_oi > 0) both mean "undefined" -> "N/A".

    Returns:
        One of "N/A", "Strong Bullish", "Bullish", "Neutral", "Bearish",
        "Strong Bearish".
    """
    if ratio is None or ratio == float("inf"):
        return "N/A"
    if ratio < PC_RATIO_STRONG_BULLISH_THRESHOLD:
        return "Strong Bullish"
    if ratio < PC_RATIO_NEUTRAL_THRESHOLD:
        return "Bullish"
    if ratio == PC_RATIO_NEUTRAL_THRESHOLD:
        return "Neutral"
    if ratio < PC_RATIO_BEARISH_THRESHOLD:
        return "Bearish"
    return "Strong Bearish"


# --- OI day-over-day "significant change" gate (M8) -----------------------
#
# code_quality_review.md M8: on_chain_analysis_service.py's
# _format_oi_changes used bare 20/10 literals to decide which strike/type
# OI moves are worth surfacing in the LARGE OI CHANGES section.
OI_CHANGE_SIGNIFICANT_PCT_THRESHOLD = 20.0
"""Minimum |% change| in open interest (vs. the previous day's snapshot)
before a strike/type is reported as a significant OI change."""

OI_CHANGE_SIGNIFICANT_ABS_THRESHOLD = 10.0
"""Minimum |absolute contract change| in open interest required IN
ADDITION to the percentage gate above -- guards against a 100% swing on a
1-contract book being reported as "significant"."""


# --- 25-delta skew interpretation (M8) -------------------------------------
#
# code_quality_review.md M8: volatility_surface_calculator.py's
# _calculate_25_delta_skew used bare +-1/+-5 literals.
SKEW_STRONG_THRESHOLD_POINTS = 5.0
"""|25d put IV - 25d call IV| beyond this many vol points is labeled
"Strong" hedging demand / upside speculation."""

SKEW_MILD_THRESHOLD_POINTS = 1.0
"""Below this many vol points either way, the skew is labeled "Balanced" --
matches VWAP_AGGRESSION_THRESHOLD_POINTS's own +-1pt noise floor above."""


# --- IV percentile advice (M8) ---------------------------------------------
#
# code_quality_review.md M8: the per-expiry IV-percentile advice gates
# (formerly inline in on_chain_analysis_service.py, now
# reporting/oi_changes_formatter.py's format_iv_percentile_section) used
# bare 80/20 literals.
IV_PERCENTILE_HIGH_THRESHOLD = 80.0
"""IV percentile (vs. this expiry's own history) above which the report
suggests favoring selling volatility."""

IV_PERCENTILE_LOW_THRESHOLD = 20.0
"""IV percentile below which the report suggests favoring buying volatility."""


# --- ITM/OTM open-interest skew (M8) ---------------------------------------
#
# code_quality_review.md M8: on_chain_analyzer.py's analyze_moneyness used
# bare 70/40 literals for the notional-weighted ITM/OTM skew label.
OI_SKEW_OTM_HEAVY_THRESHOLD_PCT = 70.0
"""Share of total OI notional sitting OTM above which the book is labeled
"Heavy OTM (Speculative)"."""

OI_SKEW_ITM_HEAVY_THRESHOLD_PCT = 40.0
"""Share of total OI notional sitting ITM above which the book is labeled
"Heavy ITM (Hedging)" (checked only when the OTM gate above doesn't fire)."""


# --- Buy/sell flow trend acceleration (M8) ---------------------------------
#
# code_quality_review.md M8: buy_sell_flow_analyzer.py's _detect_flow_trend
# used bare 1.5/1.2/0.7 literals for both the accelerating-buy and
# accelerating-sell branches.
FLOW_TREND_ACCELERATION_FACTOR = 1.5
"""The 1h rate must exceed the 4h rate by this multiple (same sign) for the
trend to be labeled "Accelerating"."""

FLOW_TREND_CONFIRMATION_FACTOR = 1.2
"""The 4h rate must in turn exceed the full-window rate by this multiple --
both gates must hold before "Accelerating" is reported, so a one-window
spike alone does not qualify."""

FLOW_TREND_DECELERATION_FACTOR = 0.7
"""The 1h rate falling below this fraction of the 4h rate (same sign)
labels the trend "Decelerating"."""


# --- Buy/sell flow bias (M8) ------------------------------------------------
#
# code_quality_review.md M8: buy_sell_flow_analyzer.py's
# _interpret_flow_bias used bare 1.3/1.1/0.9/0.7 literals.
FLOW_BIAS_HEAVY_THRESHOLD = 1.3
"""Expiration-level buy/sell volume ratio above which flow is "Heavy Buying"."""

FLOW_BIAS_MODERATE_THRESHOLD = 1.1
"""Ratio above which (but below the heavy gate) flow is "Moderate Buying"."""

FLOW_BIAS_BALANCED_LOW_THRESHOLD = 0.9
"""Ratio above which (but below the moderate-buying gate) flow is "Balanced"."""

FLOW_BIAS_SELLING_THRESHOLD = 0.7
"""Ratio above which (but below the balanced gate) flow is "Moderate
Selling"; at or below this it is "Heavy Selling"."""


# --- Block trade detection (M8) ---------------------------------------------
#
# code_quality_review.md M8: market_wide_calculator.py's detect_block_trades
# default argument, and the service/orchestrator's BlockTradesResult
# construction, each carried an independent 100_000 literal.
BLOCK_TRADE_NOTIONAL_THRESHOLD_USD = 100_000.0
"""Minimum notional (contracts x index price) for a single trade to be
surfaced as a "block trade" in the market-wide report."""
