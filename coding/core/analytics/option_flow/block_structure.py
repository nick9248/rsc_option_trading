"""
What a Deribit block trade is made of, and which expiry carries the biggest bars of the buy/sell volume chart.

A block trade (one ``block_trade_id``) is negotiated off the order book and is often a structure of many legs: calls and
puts, bought and sold, over several expiries. The side of one leg is then not a directional view on its own. This module
describes each block (its legs, expiries, bought/sold counts and biggest legs) and says what the bars of the all-expiries
chart are made of per expiry.

Notional is contracts TIMES THE STRIKE (Laevitas's definition, as in ``strike_flow.py``).
A block's legs can straddle the edge of the time window; ``legs_in_window`` is what was counted and ``declared_leg_count``
is what Deribit says the block has.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from coding.core.analytics.option_flow.strike_flow import SERIES, StrikeFlow

NOTIONAL_ROUNDING_DIGITS = 6      # removes float noise when comparing sums of the same numbers


@dataclass(frozen=True)
class BlockTradeLeg:
    """One stored row of a block trade."""

    block_trade_id: str
    trade_id: str
    trade_timestamp: datetime               # UTC-aware
    expiration: str                         # e.g. "25DEC26"
    strike: float
    option_type: str                        # "C" or "P"
    direction: str                          # "buy" or "sell": the taker's side
    amount: float                           # contracts (BTC)
    block_trade_leg_count: Optional[int]    # legs of the whole block as Deribit declares it; None when not stored
    combo_id: Optional[str]                 # None when not stored

    @property
    def notional_usd(self) -> float:
        """Contracts times the strike."""
        return self.amount * self.strike


@dataclass(frozen=True)
class BiggestLeg:
    """One of the biggest legs of a block."""

    expiration: str
    strike: float
    option_type: str
    side: str                       # the taker's side
    contracts: float
    notional_usd: float


@dataclass
class BlockStructure:
    """What one block trade is made of, as far as its legs are in the window."""

    block_trade_id: str
    legs_in_window: int
    declared_leg_count: Optional[int]
    expiries: List[str]             # distinct, sorted by name
    legs_bought: int
    legs_sold: int
    calls: int
    puts: int
    notional_usd: float
    biggest_legs: List[BiggestLeg]  # top 3 by notional
    label: str
    first_time: datetime


@dataclass
class ExpiryShare:
    """The notional one expiry contributes to a bar, and its share of the bar."""

    expiry: str
    notional_usd: float
    share: float


@dataclass
class BarExpiryShare:
    """One drawn bar (strike, option type, taker side; blocked + screen) and the expiries it is made of."""

    strike: float
    option_type: str
    direction: str
    total_notional_usd: float
    blocked_notional_usd: float
    blocked_share: float
    expiries: List[ExpiryShare] = field(default_factory=list)      # largest share first
    unexplained_notional: float = 0.0   # bar total minus the sum over expiries; not 0 when the per-expiry flows are incomplete


def _label(legs_in_window: int, expiry_count: int, declared_leg_count: Optional[int]) -> str:
    if legs_in_window == 1:
        label = "single leg"
    elif legs_in_window == 2:
        label = "two legs"
    else:
        noun = "expiry" if expiry_count == 1 else "expiries"
        label = (f"multi-leg structure over {expiry_count} {noun}: "
                 "the direction of one leg is not a view on its own")
    if declared_leg_count is not None and legs_in_window < declared_leg_count:
        label += f" (only {legs_in_window} of {declared_leg_count} legs in the window)"
    return label


def describe_blocks(legs: List[BlockTradeLeg], top: int = 10) -> List[BlockStructure]:
    """
    Describe the blocks of the legs, the ``top`` largest by notional first (ties by block id).

    Args:
        legs: Every leg of the window (any number of blocks).
        top: How many blocks to return.
    """
    legs_by_block: Dict[str, List[BlockTradeLeg]] = {}
    for leg in legs:
        legs_by_block.setdefault(leg.block_trade_id, []).append(leg)

    structures: List[BlockStructure] = []
    for block_trade_id, block_legs in legs_by_block.items():
        declared_counts = [leg.block_trade_leg_count for leg in block_legs if leg.block_trade_leg_count is not None]
        declared = max(declared_counts) if declared_counts else None
        expiries = sorted({leg.expiration for leg in block_legs})
        biggest = sorted(block_legs, key=lambda leg: (-leg.notional_usd, leg.expiration, leg.strike,
                                                      leg.option_type, leg.direction))[:3]
        structures.append(BlockStructure(
            block_trade_id=block_trade_id,
            legs_in_window=len(block_legs),
            declared_leg_count=declared,
            expiries=expiries,
            legs_bought=sum(1 for leg in block_legs if leg.direction == "buy"),
            legs_sold=sum(1 for leg in block_legs if leg.direction == "sell"),
            calls=sum(1 for leg in block_legs if leg.option_type == "C"),
            puts=sum(1 for leg in block_legs if leg.option_type == "P"),
            notional_usd=sum(leg.notional_usd for leg in block_legs),
            biggest_legs=[BiggestLeg(leg.expiration, leg.strike, leg.option_type, leg.direction, leg.amount,
                                     leg.notional_usd) for leg in biggest],
            label=_label(len(block_legs), len(expiries), declared),
            first_time=min(leg.trade_timestamp for leg in block_legs),
        ))
    structures.sort(key=lambda structure: (-structure.notional_usd, structure.block_trade_id))
    return structures[:top]


def bar_expiry_shares(per_expiry_flows: Dict[str, StrikeFlow], all_flow: StrikeFlow, top: int = 8) -> List[BarExpiryShare]:
    """
    Which expiries make up the ``top`` biggest bars of the all-expiries flow.

    A bar is one (strike, option type, taker side) with its blocked and screen notional added. For each bar the notional
    per expiry comes from ``per_expiry_flows`` (expiry label to the flow of that expiry alone), largest share first
    (ties by expiry name). ``unexplained_notional`` is the bar total minus the sum over expiries: 0 when the per-expiry
    flows are complete.

    Args:
        per_expiry_flows: The flow of each expiry of the window.
        all_flow: The flow of all expiries.
        top: How many bars to return, the biggest by total notional (ties by strike, option type, side).
    """
    bars: List[Tuple[float, str, str, float, float]] = []   # strike, type, side, total, blocked
    for strike in all_flow.strikes:
        for option_type in ("C", "P"):
            for direction in ("buy", "sell"):
                notionals = {series.blocked: all_flow.cell(strike, series.key).notional_usd
                             for series in SERIES if series.option_type == option_type and series.direction == direction}
                total = notionals[True] + notionals[False]
                if total > 0:
                    bars.append((strike, option_type, direction, total, notionals[True]))
    bars.sort(key=lambda bar: (-bar[3], bar[0], bar[1], bar[2]))

    result: List[BarExpiryShare] = []
    for strike, option_type, direction, total, blocked in bars[:top]:
        expiry_shares: List[ExpiryShare] = []
        for expiry, flow in per_expiry_flows.items():
            notional = sum(flow.cell(strike, series.key).notional_usd for series in SERIES
                           if series.option_type == option_type and series.direction == direction)
            if notional > 0:
                expiry_shares.append(ExpiryShare(expiry, notional, notional / total))
        expiry_shares.sort(key=lambda item: (-item.share, item.expiry))
        explained = sum(item.notional_usd for item in expiry_shares)
        result.append(BarExpiryShare(
            strike=strike, option_type=option_type, direction=direction, total_notional_usd=total,
            blocked_notional_usd=blocked, blocked_share=blocked / total, expiries=expiry_shares,
            unexplained_notional=round(total - explained, NOTIONAL_ROUNDING_DIGITS)))
    return result
