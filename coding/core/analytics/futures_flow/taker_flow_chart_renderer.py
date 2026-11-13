"""
Matplotlib chart of the taker buy/sell flow of one perpetual, in the same dark
style as the MenthorQ charts. Three panels: daily taker buy/sell ratio, daily
net taker flow in USD, rolling 24-hour ratio. Pure presentation: every number
drawn comes from the arguments; a gap in the data is a gap in the chart.
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Sequence, Tuple

from matplotlib.dates import DateFormatter
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

from coding.core.analytics.futures_flow.taker_flow import TakerFlowDay

BACKGROUND_COLOR = "#000000"
FOREGROUND_COLOR = "#e6e6e6"
GRID_COLOR = "#444444"
BUYER_COLOR = "#1fa51f"
SELLER_COLOR = "#d2451e"
LINE_COLOR = "#f0e030"
NEUTRAL_RATIO = 1.0


def _style_axes(axes, title: str) -> None:
    axes.set_facecolor(BACKGROUND_COLOR)
    axes.set_title(title, color=FOREGROUND_COLOR, fontsize=11, loc="left")
    axes.tick_params(colors=FOREGROUND_COLOR)
    axes.grid(True, color=GRID_COLOR, linewidth=0.5, alpha=0.6)
    for spine in axes.spines.values():
        spine.set_color(GRID_COLOR)


def _line_with_gaps(rolling: Sequence[Tuple[datetime, float]]) -> Tuple[List[datetime], List[float]]:
    """
    The rolling points as x and y lists, plotted at the END of each window (the hour
    start plus one hour), with a NaN between two points that are not consecutive hours
    so the line breaks there instead of bridging missing data.
    """
    moments: List[datetime] = []
    values: List[float] = []
    previous = None
    for hour_start, ratio in rolling:
        if previous is not None and hour_start - previous > timedelta(hours=1):
            moments.append(previous + timedelta(hours=1, minutes=30))
            values.append(float("nan"))
        moments.append(hour_start + timedelta(hours=1))
        values.append(ratio)
        previous = hour_start
    return moments, values


def render_taker_flow_chart(
    label: str,
    days: Sequence[TakerFlowDay],
    rolling: Sequence[Tuple[datetime, float]],
    output_path: Path,
    source_note: str,
) -> Path:
    """
    Draw and save the chart.

    Args:
        label: Asset label, e.g. "BTC".
        days: Complete UTC days, oldest first (days with a missing hour are not in it).
        rolling: ``rolling_ratio`` points (start of the last hour of each window, ratio).
        output_path: PNG file to write.
        source_note: Footer text (source and newest data time).

    Returns:
        ``output_path``.
    """
    figure = Figure(figsize=(13, 9), facecolor=BACKGROUND_COLOR)
    ratio_axes, net_axes, rolling_axes = figure.subplots(3, 1)
    figure.suptitle(f"{label} perpetual taker buy/sell flow (futures market, not options)",
                    color=FOREGROUND_COLOR, fontsize=14)

    _style_axes(ratio_axes, "Daily taker buy/sell ratio (above 1 = buyers more aggressive)")
    rated = [day for day in days if day.ratio is not None]
    ratio_axes.bar([day.day for day in rated], [(day.ratio or 0.0) - NEUTRAL_RATIO for day in rated],
                   bottom=NEUTRAL_RATIO,
                   color=[BUYER_COLOR if (day.ratio or 0.0) >= NEUTRAL_RATIO else SELLER_COLOR for day in rated])
    ratio_axes.axhline(NEUTRAL_RATIO, color=FOREGROUND_COLOR, linewidth=0.8, linestyle="--")

    _style_axes(net_axes, "Daily net taker flow (taker buy minus taker sell, USD)")
    net_axes.bar([day.day for day in days], [day.net_usd for day in days],
                 color=[BUYER_COLOR if day.net_usd >= 0 else SELLER_COLOR for day in days])
    net_axes.axhline(0, color=FOREGROUND_COLOR, linewidth=0.8)
    net_axes.yaxis.set_major_formatter(FuncFormatter(lambda value, _: "0" if value == 0 else f"{value / 1e9:+.1f}B"))

    _style_axes(rolling_axes, "Rolling 24-hour taker buy/sell ratio (hourly resolution)")
    moments, values = _line_with_gaps(rolling)
    if moments:
        rolling_axes.plot(moments, values, color=LINE_COLOR, linewidth=1.2)
    rolling_axes.axhline(NEUTRAL_RATIO, color=FOREGROUND_COLOR, linewidth=0.8, linestyle="--")
    rolling_axes.xaxis.set_major_formatter(DateFormatter("%m-%d"))

    figure.text(0.01, 0.01, source_note, color=FOREGROUND_COLOR, fontsize=8)
    figure.tight_layout(rect=(0, 0.03, 1, 0.96))
    figure.savefig(output_path, facecolor=BACKGROUND_COLOR, dpi=110)
    return output_path
