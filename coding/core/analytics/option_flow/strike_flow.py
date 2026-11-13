"""
Buy/sell volume per strike of Deribit option trades (the "Buy/Sell Volume by strike" view of Laevitas).

Every trade is put in one of eight series by option type (call/put), the taker's side (buy/sell: Deribit's ``direction``
is the side of the aggressor, i.e. who crossed the spread) and whether it was a block trade (``block_trade_id`` set), and
added up per strike: contracts (1 contract = 1 BTC of underlying), notional in USD (contracts times the index price at the
time of the trade) and the number of trades.

A "buy" or "sell" here is what the taker did. It does not say whether the position was opened or closed, nor whether the
taker was a customer or a market maker; blocks are negotiated and often several legs of one structure.

A trade without a usable index price (missing, zero or negative) or with an unknown type or side is skipped and counted,
never counted as zero.
"""

from dataclasses import dataclass, field
from typing import Dict, Iterable, Optional, Tuple


@dataclass(frozen=True)
class FlowSeries:
    """One of the eight series of the chart."""

    key: str
    label: str
    option_type: str        # "C" or "P"
    direction: str          # "buy" or "sell" (the taker's side)
    blocked: bool


# The order of Laevitas's legend.
SERIES: Tuple[FlowSeries, ...] = (
    FlowSeries("call_buy", "Calls Buy", "C", "buy", False),
    FlowSeries("call_sell", "Calls Sell", "C", "sell", False),
    FlowSeries("put_buy", "Puts Buy", "P", "buy", False),
    FlowSeries("put_sell", "Puts Sell", "P", "sell", False),
    FlowSeries("call_buy_block", "Call Buy Blocked", "C", "buy", True),
    FlowSeries("call_sell_block", "Call Sell Blocked", "C", "sell", True),
    FlowSeries("put_buy_block", "Put Buy Blocked", "P", "buy", True),
    FlowSeries("put_sell_block", "Put Sell Blocked", "P", "sell", True),
)
_SERIES_BY_KIND = {(series.option_type, series.direction, series.blocked): series.key for series in SERIES}


@dataclass(frozen=True)
class OptionTrade:
    """The fields of one option trade that the flow needs."""

    expiration: str                 # e.g. "27NOV26"
    strike: float
    option_type: str                # "C" or "P"
    direction: str                  # "buy" or "sell": the taker's side
    amount: float                   # contracts (BTC)
    index_price: Optional[float]    # USD, at the time of the trade
    is_block: bool


@dataclass
class FlowCell:
    """Contracts, notional (USD) and number of trades of one series at one strike."""

    contracts: float = 0.0
    notional_usd: float = 0.0
    trades: int = 0


@dataclass
class StrikeFlow:
    """The flow per strike and series, and how many trades were used or skipped."""

    cells: Dict[float, Dict[str, FlowCell]] = field(default_factory=dict)
    trades_used: int = 0
    trades_skipped: int = 0

    def cell(self, strike: float, series_key: str) -> FlowCell:
        """The cell of a strike and series (an empty cell when nothing traded there)."""
        return self.cells.get(strike, {}).get(series_key, FlowCell())

    @property
    def strikes(self) -> list:
        """The strikes that have at least one trade, ascending."""
        return sorted(self.cells)


def aggregate_by_strike(trades: Iterable[OptionTrade], expiry: Optional[str] = None) -> StrikeFlow:
    """
    Add the trades up per strike and series.

    Args:
        trades: The option trades of the window.
        expiry: Only this expiration (e.g. ``"27NOV26"``); all expirations when omitted.
    """
    flow = StrikeFlow()
    for trade in trades:
        if expiry is not None and trade.expiration != expiry:
            continue
        key = _SERIES_BY_KIND.get((trade.option_type.upper(), trade.direction.lower(), trade.is_block))
        if key is None or trade.index_price is None or trade.index_price <= 0 or trade.amount <= 0:
            flow.trades_skipped += 1
            continue
        cell = flow.cells.setdefault(trade.strike, {}).setdefault(key, FlowCell())
        cell.contracts += trade.amount
        cell.notional_usd += trade.amount * trade.index_price
        cell.trades += 1
        flow.trades_used += 1
    return flow
