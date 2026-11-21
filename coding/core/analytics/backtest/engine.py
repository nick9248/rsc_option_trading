"""
One trade of a structure held to expiry, and the statistics of many trades. Pure: no database, no network.

PnL per trade, in USD, for one coin per leg: the cash at entry (sells at the bid received, buys at the ask paid) plus the payoff at
settlement (intrinsic value, received for a bought option, paid for a sold one) minus the trading fee of every fill and the delivery fee of
every leg that settles in the money. Premiums are converted at the index price of the entry, the payoff at the settlement price;
Deribit options are margined and settled in the coin, which this USD view ignores. Trades are independent: they may overlap in time and
no margin or position limit is modelled.
"""

import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional, Sequence, Tuple

from coding.core.analytics.backtest.fees import delivery_fee_usd, trading_fee_usd
from coding.core.analytics.backtest.structures import Leg


@dataclass(frozen=True)
class Trade:
    """One simulated trade."""

    structure: str
    expiration: str
    entry_time: datetime
    spot_at_entry: float
    settlement_price: float
    legs: Tuple[Leg, ...]
    premium_usd: float
    """Cash at entry: positive = received (a credit), negative = paid (a debit)."""
    payoff_usd: float
    trading_fees_usd: float
    delivery_fees_usd: float
    pnl_usd: float


def intrinsic_value_usd(option_type: str, strike: float, settlement_price: float) -> float:
    """What one contract is worth at settlement."""
    return max(settlement_price - strike, 0.0) if option_type == "call" else max(strike - settlement_price, 0.0)


def simulate_trade(structure: str, expiration: str, entry_time: datetime, spot_at_entry: float, legs: Sequence[Leg],
                   settlement_price: float) -> Trade:
    """Hold the legs to expiry and settle them at ``settlement_price``."""
    premium = payoff = trading_fees = delivery_fees = 0.0
    for leg in legs:
        sign = 1.0 if leg.action == "sell" else -1.0                      # a sell brings cash in, a buy pays it out
        premium += sign * leg.entry_price_usd
        trading_fees += trading_fee_usd(spot_at_entry, leg.entry_price_usd)
        intrinsic = intrinsic_value_usd(leg.option_type, leg.strike, settlement_price)
        payoff += -sign * intrinsic                                       # the buyer receives it, the seller pays it
        delivery_fees += delivery_fee_usd(settlement_price, intrinsic)
    return Trade(structure, expiration, entry_time, spot_at_entry, settlement_price, tuple(legs), premium, payoff, trading_fees,
                 delivery_fees, premium + payoff - trading_fees - delivery_fees)


@dataclass(frozen=True)
class Summary:
    """Statistics of a list of trades (None where there are too few trades to compute them)."""

    trades: int
    wins: int
    win_rate: Optional[float]
    total_pnl_usd: float
    mean_pnl_usd: Optional[float]
    median_pnl_usd: Optional[float]
    stdev_pnl_usd: Optional[float]
    worst_pnl_usd: Optional[float]
    best_pnl_usd: Optional[float]
    mean_premium_usd: Optional[float]
    mean_fees_usd: Optional[float]
    max_drawdown_usd: float
    """Largest fall of the cumulative PnL (trades in entry order) from a previous high."""
    pnl_by_month: Dict[str, float]
    skipped: Dict[str, int]
    """Entry days that produced no trade, by reason."""


def summarize(trades: Sequence[Trade], skipped: Optional[Counter] = None) -> Summary:
    """Statistics of the trades, which may be in any order."""
    ordered = sorted(trades, key=lambda trade: trade.entry_time)
    pnl = [trade.pnl_usd for trade in ordered]
    cumulative = peak = drawdown = 0.0
    for value in pnl:
        cumulative += value
        peak = max(peak, cumulative)
        drawdown = max(drawdown, peak - cumulative)
    monthly: Dict[str, float] = {}
    for trade in ordered:
        key = trade.entry_time.strftime("%Y-%m")
        monthly[key] = monthly.get(key, 0.0) + trade.pnl_usd
    count = len(pnl)
    return Summary(
        trades=count, wins=sum(1 for value in pnl if value > 0), win_rate=(sum(1 for v in pnl if v > 0) / count) if count else None,
        total_pnl_usd=sum(pnl), mean_pnl_usd=statistics.fmean(pnl) if count else None,
        median_pnl_usd=statistics.median(pnl) if count else None,
        stdev_pnl_usd=statistics.stdev(pnl) if count > 1 else None, worst_pnl_usd=min(pnl) if count else None,
        best_pnl_usd=max(pnl) if count else None,
        mean_premium_usd=statistics.fmean(t.premium_usd for t in ordered) if count else None,
        mean_fees_usd=statistics.fmean(t.trading_fees_usd + t.delivery_fees_usd for t in ordered) if count else None,
        max_drawdown_usd=drawdown, pnl_by_month=monthly, skipped=dict(skipped or {}))
