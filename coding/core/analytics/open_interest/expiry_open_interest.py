"""
Open interest per expiry of one option-chain snapshot (pure calculation).

Definitions:

* open interest is in coins (contracts of the chain rows, as Deribit reports them);
* notional = open interest in coins x index price at the snapshot (USD);
* share = the expiry's total open interest / the summed total open interest of all listed (unexpired) expiries that have a
  computed total;
* days left = days from the given instant to the 08:00 UTC settlement of the expiry label.

Missing data is None, never 0: a side whose rows all lack open interest is None, not zero.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence

from coding.core.analytics.menthorq_replication_calculator import parse_expiry_settlement

SECONDS_PER_DAY = 86400.0


@dataclass
class ExpiryOpenInterest:
    """
    Open interest of one expiry.

    Attributes:
        expiry: Deribit label, e.g. "25DEC26".
        settlement: Settlement instant (08:00 UTC); None when the label does not parse.
        days_left: Days from the given instant to the settlement; None when the label does not parse.
        call_open_interest: Coins in calls; None when no call row has open interest.
        put_open_interest: Coins in puts; None when no put row has open interest.
        total_open_interest: Coins in calls and puts (see ``open_interest_by_expiry`` for the one-sided rule).
        notional_usd: Total open interest x index price; None when either is missing.
        share: Fraction (0 to 1) of the summed total of all expiries; None when the total is missing.
        rows_without_open_interest: Chain rows of this expiry whose open interest is None.
    """

    expiry: str
    settlement: Optional[datetime]
    days_left: Optional[float]
    call_open_interest: Optional[float]
    put_open_interest: Optional[float]
    total_open_interest: Optional[float]
    notional_usd: Optional[float]
    share: Optional[float]
    rows_without_open_interest: int


def open_interest_by_expiry(chain_rows: Sequence[Mapping[str, Any]], index_price: Optional[float],
                            as_of: datetime) -> List[ExpiryOpenInterest]:
    """
    Open interest of every unexpired expiry of a chain, largest total first (expiries without a total last).

    A side (calls or puts) whose rows all have open interest None is None, not 0. The total is None when both sides are
    None. When only one side is None, the total is the known side alone and ``rows_without_open_interest`` counts the
    rows that are missing, so the reader can see the total is a lower bound. Expiries that settled at or before ``as_of``
    are left out. An index price that is None or not positive gives no notional.

    Args:
        chain_rows: Rows with ``expiration``, ``option_type`` ("C" or "P") and ``open_interest`` (may be None).
        index_price: Index price at the snapshot.
        as_of: UTC-aware instant the days left are counted from.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware (UTC)")
    sums: Dict[str, Dict[str, Optional[float]]] = {}
    missing: Dict[str, int] = {}
    for row in chain_rows:
        expiry = row["expiration"]
        sides = sums.setdefault(expiry, {"C": None, "P": None})
        missing.setdefault(expiry, 0)
        side = row["option_type"]
        if side not in sides:
            continue
        value = row.get("open_interest")
        if value is None:
            missing[expiry] += 1
        else:
            sides[side] = (sides[side] or 0.0) + float(value)

    results: List[ExpiryOpenInterest] = []
    for expiry, sides in sums.items():
        settlement = parse_expiry_settlement(expiry)
        if settlement is not None and settlement <= as_of:
            continue
        calls, puts = sides["C"], sides["P"]
        known = [value for value in (calls, puts) if value is not None]
        total = sum(known) if known else None
        notional = total * index_price if total is not None and index_price is not None and index_price > 0 else None
        days_left = (settlement - as_of).total_seconds() / SECONDS_PER_DAY if settlement is not None else None
        results.append(ExpiryOpenInterest(expiry, settlement, days_left, calls, puts, total, notional, None, missing[expiry]))

    grand_total = sum(item.total_open_interest for item in results if item.total_open_interest is not None)
    for item in results:
        if item.total_open_interest is not None and grand_total > 0:
            item.share = item.total_open_interest / grand_total
    far = datetime.max.replace(tzinfo=as_of.tzinfo)
    results.sort(key=lambda item: (item.total_open_interest is None, -(item.total_open_interest or 0.0), item.settlement or far))
    return results
