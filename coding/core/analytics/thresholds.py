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
# "Bearish" (the 5-level vocabulary). Chosen to align with
# ``interpret_put_call_ratio_percentile``'s "Strong X"/"X"/"Neutral" naming
# scheme -- NOT an existing function in this codebase (task A7 review
# caught this comment, and the task's own report, falsely claiming it
# already exists elsewhere in the report); it is specified in
# bugfix_spec.md F10.3.1 for a future, not-yet-built task. An inf ratio
# (call_oi == 0, put_oi > 0) is "N/A" in both call sites now -- it is a
# data-insufficiency case (nothing to divide by), not a directional claim.
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


# --- 25-delta risk reversal interpretation (M8 / bugfix_spec.md Item 9) ---
#
# code_quality_review.md M8: volatility_surface_calculator.py's
# _calculate_25_delta_skew (now _calculate_25_delta_risk_reversal, Item 9)
# used bare +-1/+-5 literals. Item 9 re-signs the underlying metric to the
# market "risk reversal" convention (call IV - put IV) -- these thresholds
# are re-signed to match (same magnitudes, opposite sign meaning) and reused
# by synthesis.ScoringEngine.score_skew for the same reason: one named
# threshold, not two independently-tuned copies.
RISK_REVERSAL_STRONG_POINTS = 5.0
"""|25d call IV - 25d put IV| beyond this many vol points is labeled
"Strong" (upside speculation if positive, downside hedging if negative)."""

RISK_REVERSAL_MILD_POINTS = 1.0
"""Below this many vol points either way, the risk reversal is labeled
"Balanced" -- matches VWAP_AGGRESSION_THRESHOLD_POINTS's own +-1pt noise
floor above."""


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
surfaced in the "large prints" (screen prints, not blocks) list in the
market-wide report."""

# institutional_metrics_spec.md section 9 / Migration M2 (Task D1):
# block_trade_id was added to historical_trades by migration 022, applied
# 2026-08-02. History is NOT backfillable -- block_trade_id was never
# captured before this date, so the block-trade report section must state
# this as its effective start date rather than imply "no data".
BLOCK_TRADE_ID_TRACKED_SINCE = "2026-08-02"
"""Date migration 022 (block_trade_id + companion columns) was applied.
Printed verbatim in the block-trade report section per the spec's
"state its start date" requirement -- not derived from any row's
timestamp, since no row before this date has the field."""


# --- Max Pain "expiry-week only" gate (institutional_metrics_spec.md
# section 9, Task D2 independent review ruling) -----------------------------
#
# Max pain is a pinning phenomenon that isn't institutionally meaningful
# hundreds of DTE out -- the removals table's "one line, expiry-week only"
# instruction is a TIME-WINDOW gate (only render for near-expiry
# expirations), not a comparison-scope instruction. No existing named
# constant for "near expiry" was found elsewhere in the campaign at review
# time -- GammaRolloffCalculator/GexDexCalculator's own 7-day gamma-cliff
# boundary (gex_dex_calculator.py's calculate_rolloff_profile) uses a raw
# 7.0 literal, not a named constant -- so this defaults to the same 7 days
# or a documented, independent choice.
MAX_PAIN_EXPIRY_WEEK_THRESHOLD_DAYS = 7.0
"""Per-expiry CONTEXT's Max Pain line renders only when this expiration's
DTE (to its own 08:00 UTC settlement) is <= this many days -- matches the
existing (unnamed) 7-day gamma-cliff boundary used elsewhere for the same
"near expiry" concept, for consistency."""


# --- Vol-regime GEX-normalized threshold (Task Wave-I-A Fix 3) -------------
#
# synthesis.RegimeClassifier.classify_vol_regime computes
# ``gex_normalized = gex_total / spot`` and used to compare it against a
# bare +-20. At a real BTC spot (~$64k+) and real aggregate/per-expiry net
# GEX magnitudes (six to nine figures USD), that ratio comes out in the
# tens to low thousands -- so +-20 fired for almost any expiry/aggregate
# with meaningful open interest, degenerating into a pure sign check
# dressed as a normalized band.
#
# Evidence (tests/golden/onchain_result_BTC.json, the one real fixture
# available -- a single point-in-time snapshot, not a validated historical
# distribution): spot $64,371.10; per-expiry gex_total/spot ranged from
# -64.84 (27JUL26, thin OI) up to 1264.26 (31JUL26, the most liquid
# near-dated expiry); the market-wide aggregate (``aggregate_total_gex /
# spot_price``, what classify_vol_regime is actually called with in
# SynthesisEngine.run) was 2173.35. Most expiries with any real OI already
# clear the old +-20 band by 1-2 orders of magnitude (e.g. 26JUL26 at
# 124.65, 28AUG26 at 286.53) -- confirming the "almost always fires"
# finding. 1000.0 sits above every individual-expiry reading in this
# fixture except the two genuine tail cases (31JUL26's 1264.26, the
# 2173.35 aggregate) -- i.e. it separates "typical" per-expiry GEX
# concentration from clear outliers in the one real sample available,
# instead of firing on everything with a nonzero sign.
#
# This is a evidence-informed STATIC threshold, not a validated
# percentile -- HistoricalNormalizer (this codebase's own trailing-window
# percentile/z-score machinery, institutional_metrics_spec.md section
# 1(b)) is the architecturally correct fix ("is this GEX large relative to
# its own recent history"), and a whitelisted history column already
# exists for it (``onchain_analysis_snapshots.total_net_gex`` via
# ``Repository.get_metric_history`` / ``_METRIC_HISTORY_WHITELIST``) --
# but that series is per-expiry (front-month only) and DB-backed, while
# classify_vol_regime is a pure function fed the market-WIDE aggregate GEX
# with no history or repository access. Wiring an aggregate historical
# series and threading a live percentile into this classifier is a larger,
# separate change (same "not currently plumbed into this synthesis path
# at all" scope boundary SynthesisEngine.run's own DATA QUALITY block
# comment already draws for the historical-percentile context table) --
# flagged as a follow-up, not done here.
GEX_NORMALIZED_REGIME_THRESHOLD = 1000.0
"""classify_vol_regime's SUPPRESSED/EXPLOSIVE branches compare
``gex_total / spot`` against +-this value (was a bare +-20)."""
