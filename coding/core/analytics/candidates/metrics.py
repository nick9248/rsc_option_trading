"""
Numbers that describe a candidate: cost, risk and its expected result under price distributions (pure, numpy).

Costs are exact: the spread paid by trading at the touch instead of the mid, the trading fee of every fill. The expected result under a
distribution is the probability-weighted PnL over the distribution's grid, after the trading fees and the delivery fee of every leg that
settles in the money.
"""

from dataclasses import dataclass
from typing import Callable, Dict, Mapping, Optional, Tuple

import numpy as np

from coding.core.analytics.backtest.fees import (
    DELIVERY_FEE_RATE, FEE_CAP_FRACTION_OF_OPTION_VALUE, trading_fee_usd)
from coding.core.analytics.candidates.distribution import LognormalDistribution
from coding.core.analytics.candidates.generator import Candidate
from coding.core.analytics.charts.payoff import PayoffLeg, summarize_payoff
from coding.core.analytics.risk.venue import FeeSchedule, Venue

MAX_SIZE_ITERATIONS = 6
"""Repeats of the size search of ``metrics_at_venue_size`` (it converges in two or three)."""
TAIL_FRACTION = 0.05
"""The tail loss is the average PnL of the worst 5% of outcomes."""


@dataclass(frozen=True)
class DistributionMetrics:
    """The result of a candidate if the price finished as the distribution says."""

    probability_of_profit: float
    expected_pnl_usd: float
    tail_loss_usd: float
    """Average PnL of the worst 5% of outcomes (negative for a loss)."""


@dataclass(frozen=True)
class CandidateMetrics:
    """All numbers of one candidate, for one coin per leg."""

    entry_cash_usd: float
    """At the touch: positive = credit, negative = debit."""
    spread_cost_usd: float
    """What trading at the touch instead of the mid costs (positive)."""
    trading_fees_usd: float
    max_profit_usd: Optional[float]
    """After trading fees, before delivery fees; None when unlimited (a long call)."""
    max_loss_usd: float
    """After trading fees, before delivery fees; positive."""
    breakevens: Tuple[float, ...]
    by_distribution: Dict[str, DistributionMetrics]
    quantity: Optional[float] = None
    """The position size (coins per leg) the per-coin fees assume (a venue's base fee is per order); None: one coin, Deribit fees."""

    @property
    def market_expected_pnl_usd(self) -> float:
        """
        The expected PnL at the chain's own prices: minus what trading at the touch costs (the spread against the mid and the trading
        fees), for every structure. At the mid a structure has an expected PnL of zero under the market's own (risk-neutral) distribution;
        delivery fees (a few USD) are not included.
        """
        return -(self.spread_cost_usd + self.trading_fees_usd)


def pnl_on_grid(candidate: Candidate, spot: float, prices: np.ndarray, leg_fee: Optional[Callable[[float], float]] = None) -> np.ndarray:
    """
    PnL (USD, one coin per leg) at each settlement price: entry cash, minus trading fees, plus payoff, minus delivery fees.

    Args:
        leg_fee: Trading fee per coin of a leg as a function of its entry price; None: the Deribit schedule.
    """
    fee_of = leg_fee or (lambda price: trading_fee_usd(spot, price))
    pnl = np.full_like(prices, candidate.entry_cash_usd - sum(fee_of(leg.entry_price_usd) for leg in candidate.legs))
    for leg in candidate.legs:
        intrinsic = np.maximum(prices - leg.strike, 0.0) if leg.option_type == "call" else np.maximum(leg.strike - prices, 0.0)
        pnl += (intrinsic if leg.action == "buy" else -intrinsic)
        pnl -= np.where(intrinsic > 0, np.minimum(DELIVERY_FEE_RATE * prices, FEE_CAP_FRACTION_OF_OPTION_VALUE * intrinsic), 0.0)
    return pnl


def _distribution_metrics(pnl: np.ndarray, weights: np.ndarray) -> DistributionMetrics:
    order = np.argsort(pnl)
    sorted_pnl, sorted_weights = pnl[order], weights[order]
    cumulative = np.cumsum(sorted_weights)
    tail = cumulative <= TAIL_FRACTION
    tail_weight = sorted_weights[tail].sum()
    tail_loss = float((sorted_pnl[tail] * sorted_weights[tail]).sum() / tail_weight) if tail_weight > 0 else float(sorted_pnl[0])
    return DistributionMetrics(float(weights[pnl > 0].sum()), float((pnl * weights).sum()), tail_loss)


def compute_metrics(candidate: Candidate, spot: float, distributions: Mapping[str, LognormalDistribution],
                    fee_schedule: Optional[FeeSchedule] = None, quantity: float = 1.0) -> CandidateMetrics:
    """
    Cost, risk and expected result of a candidate.

    Args:
        fee_schedule: A venue's taker fees; None: the Deribit schedule, one coin (the research default).
        quantity: With a fee schedule, the position size in coins per leg: its base fee per order is spread over this many coins.

    Raises:
        ValueError: when the candidate's loss is unlimited (only defined-risk structures are scored; an unlimited profit is allowed).
    """
    leg_fee: Optional[Callable[[float], float]] = None
    if fee_schedule is None:
        fees = sum(trading_fee_usd(spot, leg.entry_price_usd) for leg in candidate.legs)
    else:
        def venue_leg_fee(price: float) -> float:
            return fee_schedule.fee_per_coin_usd(spot, price, quantity)
        leg_fee = venue_leg_fee
        fees = sum(venue_leg_fee(leg.entry_price_usd) for leg in candidate.legs)
    summary = summarize_payoff([PayoffLeg(leg.action, leg.option_type, leg.strike, 1.0) for leg in candidate.legs],
                               candidate.entry_cash_usd - fees)
    if summary.max_loss_usd is None:
        raise ValueError("only candidates with a limited loss are scored")
    by_distribution = {}
    for name, distribution in distributions.items():
        prices, weights = distribution.grid()
        by_distribution[name] = _distribution_metrics(pnl_on_grid(candidate, spot, prices, leg_fee), weights)
    return CandidateMetrics(candidate.entry_cash_usd, candidate.mid_cash_usd - candidate.entry_cash_usd, fees, summary.max_profit_usd,
                            summary.max_loss_usd, summary.breakevens, by_distribution,
                            None if fee_schedule is None else quantity)


def metrics_at_venue_size(candidate: Candidate, spot: float, distributions: Mapping[str, LognormalDistribution], venue: Venue,
                          risk_per_trade_usd: float) -> Optional[CandidateMetrics]:
    """
    The metrics of the candidate at the largest size the venue allows whose maximum loss stays within the risk per trade, fees at that size.

    The base fee per order falls per coin as the size grows, so the size is found by repeating: start at the minimum order, size from the loss
    it gives, recompute. The sizes only grow, so the loss never ends above the limit.

    Returns:
        The metrics (with ``quantity``), or None when even the venue's minimum order risks more than ``risk_per_trade_usd``.
    """
    quantity = venue.minimum_amount
    metrics = compute_metrics(candidate, spot, distributions, venue.taker_fees, quantity)
    if metrics.max_loss_usd * quantity > risk_per_trade_usd:
        return None
    for _ in range(MAX_SIZE_ITERATIONS):
        larger = venue.floor_to_step(risk_per_trade_usd / metrics.max_loss_usd)
        if larger <= quantity:
            break
        quantity = larger
        metrics = compute_metrics(candidate, spot, distributions, venue.taker_fees, quantity)
    return metrics
