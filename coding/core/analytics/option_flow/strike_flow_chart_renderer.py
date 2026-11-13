"""
Matplotlib chart of the buy/sell volume per strike, in the style of the Laevitas chart: strikes on the vertical axis
(only the strikes that traded, ascending), calls to the left and puts to the right, one colour per series. Each side has
two thin bars per strike, Buy and Sell, and each bar is the BLOCKED part next to the axis followed by the screen part (so the
length of a bar is the blocked plus the screen volume of that type and side), as on Laevitas: at 70,000 their longest put bar
of about $83M is puts sold on screen ($31.7M) plus puts sold in blocks ($50.9M). Notional in USD millions. Pure
presentation: every number drawn comes from the ``StrikeFlow``.
"""

import textwrap
from pathlib import Path
from typing import Dict, List

import numpy as np
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.ticker import FuncFormatter

from coding.core.analytics.option_flow.strike_flow import SERIES, StrikeFlow, series_key

BACKGROUND_COLOR = "#1b2540"
FOREGROUND_COLOR = "#e6e9f2"
GRID_COLOR = "#33406a"
SERIES_COLORS: Dict[str, str] = {
    "call_buy": "#7dffb3", "call_sell": "#10c8ff", "put_buy": "#ff7a59", "put_sell": "#c7608f",
    "call_buy_block": "#12a083", "call_sell_block": "#f3b6f7", "put_buy_block": "#ffe14a", "put_sell_block": "#2f7cff",
}
MAXIMUM_STRIKE_LABELS = 28


def render_strike_flow_chart(flow: StrikeFlow, title: str, footer: str, output_path: Path) -> Path:
    """
    Draw the chart and save it as a PNG.

    Args:
        flow: Result of ``aggregate_by_strike``.
        title: Chart title.
        footer: Small text under the chart (window, source, definitions).
        output_path: PNG file to write.

    Returns:
        ``output_path``.
    """
    strikes = flow.strikes
    figure = Figure(figsize=(13, 9), facecolor=BACKGROUND_COLOR)
    axes = figure.subplots()
    axes.set_facecolor(BACKGROUND_COLOR)
    positions = np.arange(len(strikes))
    bar_height = 0.34
    for sign, option_type in ((-1.0, "C"), (1.0, "P")):                      # calls to the left, puts to the right
        for index, direction in enumerate(("buy", "sell")):                  # two bars per strike and side
            offset = (index - 0.5) * bar_height
            base = np.zeros(len(strikes))
            for blocked in (True, False):                                    # the blocked part first, then the screen part
                key = series_key(option_type, direction, blocked)
                values = np.array([flow.cell(strike, key).notional_usd / 1e6 for strike in strikes])
                axes.barh(positions + offset, sign * values, left=sign * base, height=bar_height,
                          color=SERIES_COLORS[key], linewidth=0)
                base = base + values

    step = max(1, int(np.ceil(len(strikes) / MAXIMUM_STRIKE_LABELS)))
    axes.set_yticks(positions[::step])
    axes.set_yticklabels([f"{strikes[index]:,.0f}" for index in range(0, len(strikes), step)])
    axes.axvline(0, color=FOREGROUND_COLOR, linewidth=0.8)
    axes.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"${abs(value):,.0f}M"))
    axes.set_xlabel("Volume (notional = contracts x strike, USD)", color=FOREGROUND_COLOR)
    axes.set_ylabel("Strike", color=FOREGROUND_COLOR)
    axes.tick_params(colors=FOREGROUND_COLOR)
    axes.grid(True, axis="x", color=GRID_COLOR, linewidth=0.5, alpha=0.6)
    for spine in axes.spines.values():
        spine.set_color(GRID_COLOR)
    axes.set_title(title, color=FOREGROUND_COLOR, loc="left", fontsize=13, fontweight="bold")
    axes.text(0.01, 0.985, "calls", transform=axes.transAxes, color=FOREGROUND_COLOR, va="top", fontsize=9)
    axes.text(0.99, 0.985, "puts", transform=axes.transAxes, color=FOREGROUND_COLOR, va="top", ha="right", fontsize=9)

    handles: List[Line2D] = [Line2D([0], [0], marker="o", linestyle="", markersize=8, color=SERIES_COLORS[series.key],
                                    label=series.label) for series in SERIES]
    figure.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 0.05), ncol=4, frameon=False,
                  labelcolor=FOREGROUND_COLOR, fontsize=9)
    figure.text(0.01, 0.004, textwrap.fill(footer, 190), color=FOREGROUND_COLOR, fontsize=7.5, va="bottom")
    figure.tight_layout(rect=(0, 0.14, 1, 1))
    figure.savefig(output_path, facecolor=BACKGROUND_COLOR, dpi=110)
    return output_path
