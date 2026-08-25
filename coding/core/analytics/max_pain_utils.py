"""
Shared helper for max-pain distance-from-spot percentage.

Task Wave-J-C Fix 1: ``expiry_formatter.format_context_section`` and
``synthesis.py`` (``score_max_pain_gravity`` and the GEX-window per-expiry
summary line) each independently computed "how far is max pain from spot,
as a percentage" -- and had silently diverged into two DIFFERENT formulas:
one used ``(spot - max_pain) / max_pain * 100`` (opposite sign AND
max_pain as the percentage base), the other used
``(max_pain - spot) / spot * 100``. For the same expiry/strike/spot this
told a reader max pain was on OPPOSITE sides of spot depending on which
report section they read.

Standard convention (distance FROM a point, as a percentage OF that
point's own reference -- spot, since spot is what a trader is comparing
max pain against): ``(max_pain - spot) / spot * 100``. Positive means max
pain sits ABOVE spot; negative means max pain sits BELOW spot. This
matches ``score_max_pain_gravity``'s pre-existing convention, which is
also the convention already used for RR25, VRP, and every other
"distance from reference" percentage in this codebase.

Extracted here (mirroring ``forward_price_utils.select_forward_price``)
so this specific bug class -- two independently-written copies of the
same calculation silently diverging -- cannot recur: there is now exactly
one place this formula lives.
"""


def calculate_max_pain_distance_pct(max_pain: float, spot: float) -> float:
    """
    Percentage distance of max pain from spot.

    Args:
        max_pain: The max-pain strike (or a known fallback value -- callers
            that distinguish a genuine measurement from a fallback must do
            so themselves; this function has no opinion on data quality).
        spot: The reference spot/forward price. Must be nonzero -- callers
            are expected to have already validated spot > 0 (see
            ``synthesis.py``'s spot-fallback handling) before calling this.

    Returns:
        ``(max_pain - spot) / spot * 100``. Positive = max pain above
        spot. Negative = max pain below spot.
    """
    return (max_pain - spot) / spot * 100
