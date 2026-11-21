"""
Profit and loss at expiry of an option structure, as numbers and as a chart (pure: no database, no network).

The PnL of a structure at settlement price S is the cash of its entry plus the sum over its legs of the intrinsic value received for a bought
option or paid for a sold one, times the quantity. Fees are not included. The curve is piecewise linear with kinks at the strikes, so the
extremes and breakevens are exact: they are read from the kinks and from the slope beyond the last strike.
"""

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import plotly.graph_objects as go

from coding.core.analytics.charts.chart_theme import GRAY, GREEN, INK, RED, base_layout


@dataclass(frozen=True)
class PayoffLeg:
    """One leg of a structure."""

    action: str
    """"buy" or "sell"."""
    option_type: str
    """"call" or "put"."""
    strike: float
    quantity: float


@dataclass(frozen=True)
class PayoffSummary:
    """The shape of the PnL at expiry."""

    max_profit_usd: Optional[float]
    """None when the profit is unlimited."""
    max_loss_usd: Optional[float]
    """A positive number (the loss), or None when the loss is unlimited."""
    breakevens: Tuple[float, ...]
    """Settlement prices above 0 at which the PnL is zero, ascending."""


def _signed_quantity(leg: PayoffLeg) -> float:
    return leg.quantity if leg.action == "buy" else -leg.quantity


def _intrinsic(leg: PayoffLeg, settlement_price: float) -> float:
    return max(settlement_price - leg.strike, 0.0) if leg.option_type == "call" else max(leg.strike - settlement_price, 0.0)


def pnl_at_expiry(legs: Sequence[PayoffLeg], entry_cash_usd: float, settlement_price: float) -> float:
    """PnL before fees when the structure settles at ``settlement_price``; ``entry_cash_usd`` is positive for a credit, negative for a debit."""
    return entry_cash_usd + sum(_signed_quantity(leg) * _intrinsic(leg, settlement_price) for leg in legs)


def summarize_payoff(legs: Sequence[PayoffLeg], entry_cash_usd: float) -> PayoffSummary:
    """
    Maximum profit and loss and the breakevens of a structure held to expiry.

    Raises:
        ValueError: for no legs.
    """
    if not legs:
        raise ValueError("a payoff needs at least one leg")
    points = sorted({0.0} | {leg.strike for leg in legs})
    values = [pnl_at_expiry(legs, entry_cash_usd, point) for point in points]
    slope_up = sum(_signed_quantity(leg) for leg in legs if leg.option_type == "call")      # PnL per unit of price above the last strike
    unlimited_profit, unlimited_loss = slope_up > 1e-12, slope_up < -1e-12
    breakevens: List[float] = []
    for (left, right), (value_left, value_right) in zip(zip(points, points[1:]), zip(values, values[1:])):
        if value_left == 0 and left > 0:
            breakevens.append(left)
        if value_left * value_right < 0:
            breakevens.append(left + (right - left) * (-value_left) / (value_right - value_left))
    if values[-1] == 0 and points[-1] > 0:
        breakevens.append(points[-1])
    if slope_up != 0 and values[-1] * slope_up < 0:
        breakevens.append(points[-1] - values[-1] / slope_up)
    return PayoffSummary(None if unlimited_profit else max(values), None if unlimited_loss else max(-min(values), 0.0),
                         tuple(sorted(set(round(value, 6) for value in breakevens if math.isfinite(value) and value > 0))))


def payoff_figure(legs: Sequence[PayoffLeg], entry_cash_usd: float, spot: float, currency: str = "BTC") -> go.Figure:
    """
    The PnL at expiry against the settlement price: green above zero, red below, the spot and the breakevens marked.

    The price range runs from 70% of the lowest of spot and strikes to 130% of the highest.
    """
    summary = summarize_payoff(legs, entry_cash_usd)
    low = 0.7 * min([spot] + [leg.strike for leg in legs])
    high = 1.3 * max([spot] + [leg.strike for leg in legs])
    prices = sorted({low, high, *(leg.strike for leg in legs), *summary.breakevens, spot})
    values = [pnl_at_expiry(legs, entry_cash_usd, price) for price in prices]
    figure = go.Figure()
    for sign, color in ((1, GREEN), (-1, RED)):
        shown = [value if value * sign > 0 else 0.0 for value in values]
        figure.add_trace(go.Scatter(x=prices, y=shown, mode="lines", line=dict(width=0), fill="tozeroy",
                                    fillcolor="rgba(47,125,85,0.18)" if sign > 0 else "rgba(184,67,58,0.18)", hoverinfo="skip"))
    figure.add_trace(go.Scatter(x=prices, y=values, mode="lines", line=dict(color=INK, width=2.5), name="PnL at expiry",
                                hovertemplate=f"{currency} %{{x:,.0f}}<br>PnL %{{y:,.0f}} USD<extra></extra>"))
    figure.add_vline(x=spot, line=dict(color=GRAY, width=1.5, dash="dash"), annotation_text=f"spot {spot:,.0f}", annotation_position="top")
    figure.add_hline(y=0, line=dict(color=GRAY, width=1))
    for breakeven in summary.breakevens:
        figure.add_trace(go.Scatter(x=[breakeven], y=[0], mode="markers", marker=dict(color=INK, size=9, symbol="diamond"),
                                    hovertemplate=f"breakeven {breakeven:,.0f}<extra></extra>"))
    return base_layout(figure, f"{currency} settlement price", "PnL at expiry, before fees (USD)")
