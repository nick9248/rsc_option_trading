"""
Shared helper for picking one expiry's forward (future) price out of a group
of same-expiry instrument dicts.

bugfix_spec.md Item 7 (F7.3.1): this exact "highest-volume instrument in the
group, falling back to any priced instrument" pick used to be duplicated in
``DeribitApiService.get_option_chain_snapshot`` (``futures_by_expiry``) and is
now needed a second time by ``OnChainMetricsCalculator`` (per-expiry
``forward_price_by_expiration``) -- extracted here rather than duplicated a
third time.
"""

from typing import Any, Dict, List, Optional


def select_forward_price(items: List[Dict[str, Any]]) -> Optional[float]:
    """
    Pick one expiry's forward (future) price from a list of same-expiry
    instrument dicts.

    Each item is expected to carry ``volume`` and ``underlying_price`` keys
    (book-summary/ticker shape). Picks the ``underlying_price`` of the
    highest-24h-volume instrument in the group -- avoids the stale cached
    ``underlying_price`` that illiquid strikes can carry. Falls back to any
    instrument with a priced ``underlying_price`` when none of them have
    volume.

    Args:
        items: Instrument dicts for a SINGLE expiry (already grouped by the
            caller).

    Returns:
        The picked forward price, or ``None`` if no instrument in the group
        has a usable ``underlying_price`` at all.
    """
    active = [i for i in items if (i.get("volume") or 0) > 0 and i.get("underlying_price")]
    if active:
        return max(active, key=lambda i: i.get("volume", 0)).get("underlying_price")

    priced = [i for i in items if i.get("underlying_price")]
    return priced[0]["underlying_price"] if priced else None
