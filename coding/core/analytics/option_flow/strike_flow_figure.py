"""
Plotly figure of the buy/sell volume per strike, in the layout of the Laevitas chart: strikes on the vertical axis (only the
strikes that traded, ascending), calls to the left and puts to the right, two bars per strike (Buy and Sell), and each bar is
the BLOCKED part next to the axis followed by the screen part, so a bar's length is blocked plus screen volume of that type
and side. Hovering a strike lists all eight series with their values (like the Laevitas tooltip).

One figure serves both the interactive GUI tab (``to_html``) and the PNG export (``write_image``). Pure presentation: every
number drawn comes from the ``StrikeFlow``.
"""

import math
import textwrap
from typing import Dict, List, Tuple

import plotly.graph_objects as go

from coding.core.analytics.option_flow.strike_flow import SERIES, FlowCell, StrikeFlow

BACKGROUND_COLOR = "#1b2540"
FOREGROUND_COLOR = "#e6e9f2"
GRID_COLOR = "#33406a"
SERIES_COLORS: Dict[str, str] = {
    "call_buy": "#7dffb3", "call_sell": "#10c8ff", "put_buy": "#ff7a59", "put_sell": "#c7608f",
    "call_buy_block": "#12a083", "call_sell_block": "#f3b6f7", "put_buy_block": "#ffe14a", "put_sell_block": "#2f7cff",
}
# unit -> (axis title, divisor, number format of a value, axis tick suffix)
UNITS: Dict[str, Tuple[str, float, str, str]] = {
    "notional": ("Volume (notional = contracts x strike, USD)", 1e6, "$%{customdata:,.2f}M", "M"),
    "contracts": ("Volume (contracts = coins of the underlying)", 1.0, "%{customdata:,.1f} contracts", ""),
    "trades": ("Number of trades", 1.0, "%{customdata:,.0f} trades", ""),
}
PIXELS_PER_STRIKE = 13
MINIMUM_HEIGHT = 720
LEGEND_AND_FOOTER_SPACE = 190


def cell_value(cell: FlowCell, unit: str) -> float:
    """The number of a cell in ``unit`` ("notional" in USD, "contracts", or "trades")."""
    if unit == "notional":
        return cell.notional_usd
    if unit == "contracts":
        return cell.contracts
    if unit == "trades":
        return float(cell.trades)
    raise ValueError(f"unknown unit {unit!r}; use one of {sorted(UNITS)}")


def nice_ticks(limit: float) -> List[float]:
    """Round tick positions from 0 up to at least ``limit`` (about 4 to 6 ticks)."""
    if limit <= 0:
        return [0.0]
    raw = limit / 5
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(factor * magnitude for factor in (1, 2, 2.5, 5, 10) if factor * magnitude >= raw)
    return [step * index for index in range(int(math.ceil(limit / step)) + 1)]


def tick_decimals(step: float) -> int:
    """Decimals the tick labels need so that neighbouring ticks never look equal (0 for whole steps)."""
    for decimals in range(7):
        if abs(step - round(step, decimals)) < 1e-9:
            return decimals
    return 6


def strike_label(strike: float) -> str:
    """Category label of a strike: whole strikes without decimals, others with the decimals they need."""
    return f"{strike:,.0f}" if float(strike).is_integer() else f"{strike:,.4f}".rstrip("0")


def build_strike_flow_figure(flow: StrikeFlow, title: str, unit: str = "notional", footer: str = "") -> go.Figure:
    """
    Build the figure.

    Args:
        flow: Result of ``aggregate_by_strike``.
        title: Chart title.
        unit: ``"notional"`` (USD millions), ``"contracts"`` (BTC) or ``"trades"``.
        footer: Text under the chart (window, source, definitions).

    Raises:
        ValueError: for an unknown unit.
    """
    if unit not in UNITS:
        raise ValueError(f"unknown unit {unit!r}; use one of {sorted(UNITS)}")
    axis_title, divisor, number_format, suffix = UNITS[unit]
    strikes = flow.strikes
    categories = [strike_label(strike) for strike in strikes]
    figure = go.Figure()

    # The blocked part is added before the screen part of the same bar, so it sits next to the axis.
    order = [(direction, option_type, blocked) for direction in ("buy", "sell") for option_type in ("C", "P")
             for blocked in (True, False)]
    legend_rank = {series.key: rank for rank, series in enumerate(SERIES)}
    totals: Dict[Tuple[str, str], List[float]] = {}
    for direction, option_type, blocked in order:
        series = next(item for item in SERIES if (item.option_type, item.direction, item.blocked) == (option_type, direction, blocked))
        values = [cell_value(flow.cell(strike, series.key), unit) / divisor for strike in strikes]
        sign = -1.0 if option_type == "C" else 1.0
        bar_total = totals.setdefault((direction, option_type), [0.0] * len(strikes))
        for index, value in enumerate(values):
            bar_total[index] += value
        figure.add_bar(
            y=categories, x=[sign * value for value in values], orientation="h", name=series.label,
            marker=dict(color=SERIES_COLORS[series.key], line=dict(width=0)), offsetgroup=direction,
            legendrank=legend_rank[series.key], customdata=values,
            hovertemplate=f"{series.label}: {number_format}<extra></extra>")

    longest = max((max(values, default=0.0) for values in totals.values()), default=0.0)
    ticks = nice_ticks(longest * 1.05)
    tick_values = [-tick for tick in reversed(ticks[1:])] + ticks
    decimals = tick_decimals(ticks[1] - ticks[0]) if len(ticks) > 1 else 0
    prefix = "$" if suffix == "M" else ""
    tick_text = [f"{prefix}{abs(value):,.{decimals}f}{suffix}" for value in tick_values]
    height = max(MINIMUM_HEIGHT, PIXELS_PER_STRIKE * len(strikes) + LEGEND_AND_FOOTER_SPACE)
    wrapped_footer = "<br>".join(textwrap.wrap(footer, 170)) if footer else ""
    figure.update_layout(
        title=dict(text=f"<b>{title}</b>", x=0.01, font=dict(color=FOREGROUND_COLOR, size=16)),
        barmode="relative", bargap=0.18, hovermode="y unified", height=height,
        paper_bgcolor=BACKGROUND_COLOR, plot_bgcolor=BACKGROUND_COLOR, font=dict(color=FOREGROUND_COLOR),
        xaxis=dict(title=axis_title, tickmode="array", tickvals=tick_values, ticktext=tick_text, gridcolor=GRID_COLOR,
                   zeroline=True, zerolinecolor=FOREGROUND_COLOR, zerolinewidth=1.2),
        yaxis=dict(title="Strike", type="category", categoryorder="array", categoryarray=categories, tickfont=dict(size=10),
                   gridcolor=GRID_COLOR),
        legend=dict(orientation="h", yanchor="top", y=-0.07, xanchor="center", x=0.5),
        margin=dict(l=70, r=30, t=60, b=LEGEND_AND_FOOTER_SPACE),
        annotations=[
            dict(text="calls", x=0.005, y=1.0, xref="paper", yref="paper", showarrow=False, xanchor="left", yanchor="bottom"),
            dict(text="puts", x=0.995, y=1.0, xref="paper", yref="paper", showarrow=False, xanchor="right", yanchor="bottom"),
            dict(text=wrapped_footer, x=0.0, y=-0.14, xref="paper", yref="paper", showarrow=False, xanchor="left",
                 yanchor="top", align="left", font=dict(size=10)),
        ],
    )
    return figure
