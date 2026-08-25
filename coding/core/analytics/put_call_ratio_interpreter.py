"""
Percentile-based put/call ratio classification (bugfix_spec.md Item 10).

Replaces the hard-coded equity-style 0.7/1.0/1.3 thresholds
(``coding.core.analytics.thresholds.interpret_put_call_ratio``) for the
per-expiration OI-based put/call ratio: Deribit's book is structurally
call-heavy, so a fixed pivot mislabels a permanently bullish market (82.0%
of BTC OI-PCR readings historically print "Strong Bullish"/"Bullish" --
bugfix_spec.md section 10.1). The informative quantity is the current
reading's percentile within its own trailing history, not its absolute
level.

This module owns only the percentile -> label mapping. The percentile
itself comes from ``HistoricalNormalizer.percentile`` (task-C1 brief:
"using the HistoricalNormalizer you're building" -- Item 10 was
deliberately left unbuilt until this class existed, so it does not
duplicate percentile arithmetic in a second implementation). See this
module's test file for the one deliberate, documented deviation from
bugfix_spec.md's own illustrative acceptance test T10.4 (degenerate
history -> "Neutral", not None/dropped -- HistoricalNormalizer's mid-rank
formula returns 50.0 for a degenerate series rather than guarding to None).
"""

from typing import Optional

# Percentile bands (bugfix_spec.md F10.3.1). Symmetric, deliberately wide
# in the middle so only genuinely unusual readings get a directional
# label. LOW percentile = fewer puts than usual (vs. this series' own
# history) = bullish.
PERCENTILE_STRONG_BULLISH = 10.0
PERCENTILE_BULLISH = 30.0
PERCENTILE_BEARISH = 70.0
PERCENTILE_STRONG_BEARISH = 90.0


def interpret_put_call_ratio_percentile(percentile: Optional[float]) -> str:
    """
    Classify a put/call ratio by its percentile within its own trailing
    history (bugfix_spec.md Item 10).

    Args:
        percentile: 0-100, or None when history is too short (< MIN_OBS)
            or the current ratio is non-finite (call OI == 0).

    Returns:
        "Insufficient history" when ``percentile`` is None; otherwise one
        of "Strong Bullish", "Bullish", "Neutral", "Bearish",
        "Strong Bearish".
    """
    if percentile is None:
        return "Insufficient history"
    if percentile <= PERCENTILE_STRONG_BULLISH:
        return "Strong Bullish"
    if percentile <= PERCENTILE_BULLISH:
        return "Bullish"
    if percentile < PERCENTILE_BEARISH:
        return "Neutral"
    if percentile < PERCENTILE_STRONG_BEARISH:
        return "Bearish"
    return "Strong Bearish"
