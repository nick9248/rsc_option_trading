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
