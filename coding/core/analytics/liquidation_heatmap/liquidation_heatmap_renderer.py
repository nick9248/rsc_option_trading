"""
Matplotlib picture of a liquidation heatmap result, in the style of the Coinglass chart (purple to
yellow, candles on top, price on the right) so the two can be compared side by side. Pure
presentation: every number drawn comes from the arguments. A step without a candle is blank.
"""

from pathlib import Path
from typing import Optional, Sequence

import numpy as np
from matplotlib.dates import DateFormatter, date2num
from matplotlib.figure import Figure

from coding.core.analytics.liquidation_heatmap.liquidation_heatmap_model import STEP, HeatmapResult, PriceCandle

BACKGROUND_COLOR = "#050510"
FOREGROUND_COLOR = "#e6e6e6"
UP_COLOR = "#22c55e"
DOWN_COLOR = "#ef4444"
BELOW_PRICE_MARGIN = 0.035          # Coinglass draws about 3 % under the lowest price ...
ABOVE_PRICE_MARGIN = 0.04           # ... and about 4 % above the highest (read off its screenshots)
MAXIMUM_CANDLES_AS_BARS = 800


def render_heatmap(
    result: HeatmapResult,
    candles: Sequence[PriceCandle],
    output_path: Path,
    title: str,
    footer: str,
    vmax_usd: Optional[float] = None,
) -> Path:
    """
    Draw the heatmap and the candles of the recorded window.

    Args:
        result: ``simulate_heatmap`` result.
        candles: Candles of the recorded window (for the price path and the price range).
        output_path: PNG file to write.
        title: Chart title.
        footer: Small text under the chart (settings, data notes).
        vmax_usd: Colour scale maximum in USD; the largest value shown when omitted.

    Returns:
        ``output_path``.
    """
    figure = Figure(figsize=(14, 7.5), facecolor=BACKGROUND_COLOR)
    axes = figure.subplots()
    axes.set_facecolor(BACKGROUND_COLOR)

    low = min(candle.low for candle in candles)
    high = max(candle.high for candle in candles)
    y_low, y_high = low * (1 - BELOW_PRICE_MARGIN), high * (1 + ABOVE_PRICE_MARGIN)
    half_bin = (result.bin_centers[1] - result.bin_centers[0]) / 2
    shown = (result.bin_centers >= y_low - half_bin) & (result.bin_centers <= y_high + half_bin)

    intensity = result.intensity_usd[:, shown]
    start = date2num(result.times[0])
    end = date2num(result.times[-1] + STEP)
    maximum = vmax_usd if vmax_usd is not None else float(np.nanmax(intensity)) if np.isfinite(intensity).any() else 1.0
    image = axes.imshow(intensity.T, origin="lower", aspect="auto", cmap="viridis", vmin=0, vmax=maximum,
                        extent=(start, end, result.bin_centers[shown][0] - half_bin,
                                result.bin_centers[shown][-1] + half_bin), interpolation="nearest")
    image.cmap.set_bad(BACKGROUND_COLOR)

    step_days = STEP.total_seconds() / 86400
    centers = date2num([candle.open_time for candle in candles]) + step_days / 2
    if len(candles) <= MAXIMUM_CANDLES_AS_BARS:
        for center, candle in zip(centers, candles):
            color = UP_COLOR if candle.close >= candle.low + (candle.high - candle.low) / 2 else DOWN_COLOR
            axes.vlines(center, candle.low, candle.high, color=color, linewidth=0.8)
    else:
        axes.plot(centers, [candle.close for candle in candles], color=UP_COLOR, linewidth=0.8)

    axes.set_ylim(y_low, y_high)
    axes.set_xlim(start, end)
    axes.yaxis.tick_right()
    axes.xaxis_date()
    axes.xaxis.set_major_formatter(DateFormatter("%d %H:%M"))
    axes.tick_params(colors=FOREGROUND_COLOR)
    for spine in axes.spines.values():
        spine.set_color("#444444")
    axes.set_title(title, color=FOREGROUND_COLOR, loc="left", fontsize=13)
    colorbar = figure.colorbar(image, ax=axes, location="left", pad=0.02, fraction=0.03)
    colorbar.ax.tick_params(colors=FOREGROUND_COLOR)
    colorbar.set_label("estimated USD per price bin (relative)", color=FOREGROUND_COLOR)
    figure.text(0.01, 0.005, footer, color=FOREGROUND_COLOR, fontsize=8)
    figure.tight_layout(rect=(0, 0.03, 1, 1))
    figure.savefig(output_path, facecolor=BACKGROUND_COLOR, dpi=110)
    return output_path

