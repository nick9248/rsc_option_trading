"""
Matplotlib rendering of a ``replicate_menthorq`` result in MenthorQ's
visual style: dark background, horizontal bars with strikes on the y axis,
green for positive / red-orange for negative, dashed level lines, legend.

Pure presentation: every number drawn comes from the replication dict; no
arithmetic beyond unit scaling and picking the display window. Uses the
object-oriented ``Figure`` API (no pyplot global state, no GUI backend).
"""

import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

from coding.core.analytics.menthorq_replication_calculator import (
    IMPLIED_VOLATILITY_UNIT_DECIMAL,
    IV_OI_AGGREGATION_SUM,
    PANEL_FIRST_EXPIRATION,
    PANEL_HIGHEST_GEX,
    PANEL_NAMES,
    PANEL_NEXT_EXPIRATION,
    PANEL_RANKING_ABSOLUTE_NET,
    PANEL_SECOND_HIGHEST_GEX,
    parse_expiry_settlement,
)

logger = logging.getLogger(__name__)

BACKGROUND_COLOR = "#000000"
FOREGROUND_COLOR = "#e6e6e6"
GRID_COLOR = "#444444"
POSITIVE_COLOR = "#1fa51f"
NEGATIVE_COLOR = "#d2451e"
CALL_RESISTANCE_COLOR = "#ff2a2a"
PUT_SUPPORT_COLOR = "#22b322"
HVL_COLOR = "#d4d42a"
SPOT_COLOR = "#cccccc"
PROFILE_COLOR = "#f0e030"

DISPLAY_LOW_MULTIPLIER = 0.75
DISPLAY_HIGH_MULTIPLIER = 1.35
MILLION = 1_000_000.0

# Which candidate each drawn level line uses, chosen by comparison with
# MenthorQ's charts labelled 2026-11-06, 2026-10-12 (tuning) and 2026-08-24
# (hold-out), replayed at 23:59 UTC with open interest from 00:00 UTC: the
# net-GEX extremes and the cumulative-net-GEX minimum matched 24 of 24
# per-expiry panel levels (the two tuning dates) and 3 of 3 all-expiry
# levels on each date when the expiry set that fits that date is used
# (quarterly + front month on 2026-11-06, all expiries on the other two),
# while the call-only / put-only gamma maxima (MenthorQ's published wording)
# matched 8 of 16 per-expiry levels. The JSON output carries every candidate.
DEFAULT_LEVEL_CANDIDATES = {
    "call_resistance": "net_gex_argmax_positive",
    "put_support": "net_gex_argmin_negative",
    "hvl": "cumulative_net_gex_argmin",
}

PANEL_TITLES = {
    PANEL_FIRST_EXPIRATION: "First Expiration",
    PANEL_NEXT_EXPIRATION: "Next Expiration",
    PANEL_HIGHEST_GEX: "Expiration with Highest GEX",
    PANEL_SECOND_HIGHEST_GEX: "Expiration with 2nd Highest GEX",
}


def _display_window(
    spot: float, level_strikes: Iterable[Optional[float]]
) -> Tuple[float, float]:
    """Strike range shown: 0.75x-1.35x spot, widened to include every level line."""
    levels = [strike for strike in level_strikes if strike is not None]
    low = min([spot * DISPLAY_LOW_MULTIPLIER] + levels)
    high = max([spot * DISPLAY_HIGH_MULTIPLIER] + levels)
    return low, high


def _bar_height(strikes: List[float]) -> float:
    """80% of the smallest strike spacing, so neighbouring bars never overlap."""
    if len(strikes) < 2:
        return 500.0
    spacing = min(b - a for a, b in zip(strikes, strikes[1:]))
    return 0.8 * spacing


def _style_axes(axes) -> None:
    """Dark MenthorQ-like axes styling."""
    axes.set_facecolor(BACKGROUND_COLOR)
    axes.tick_params(colors=FOREGROUND_COLOR, labelsize=8)
    for spine in axes.spines.values():
        spine.set_color(FOREGROUND_COLOR)
    axes.grid(True, color=GRID_COLOR, linestyle=":", linewidth=0.6)
    axes.set_axisbelow(True)
    axes.yaxis.label.set_color(FOREGROUND_COLOR)
    axes.xaxis.label.set_color(FOREGROUND_COLOR)


def _draw_level_lines(axes, levels: Dict[str, Optional[float]], spot: float) -> List[Line2D]:
    """Dashed horizontal level lines; returns legend handles."""
    handles = []
    for label, value, color in (
        ("Call Resistance", levels.get("call_resistance"), CALL_RESISTANCE_COLOR),
        ("Put Support", levels.get("put_support"), PUT_SUPPORT_COLOR),
        ("HVL", levels.get("hvl"), HVL_COLOR),
        ("Spot Price", spot, SPOT_COLOR),
    ):
        text = f"{label}: {value:.0f}" if value is not None else f"{label}: not computed"
        if value is not None:
            axes.axhline(value, color=color, linestyle="--", linewidth=1.1)
        handles.append(Line2D([], [], color=color, linestyle="--", label=text))
    return handles


def _chosen_levels(level_candidates: Dict[str, Dict[str, Optional[float]]]) -> Dict[str, Optional[float]]:
    """Pick the drawn candidate for each level (see DEFAULT_LEVEL_CANDIDATES)."""
    return {
        level_name: level_candidates[level_name].get(candidate_name)
        for level_name, candidate_name in DEFAULT_LEVEL_CANDIDATES.items()
    }


def render_iv_times_open_interest_chart(
    replication: Dict[str, Any], output_path: Path, timestamp_label: str
) -> None:
    """
    Horizontal IV*OI bars (IV as a decimal, summed over the expiry
    universe): calls filled green, puts as orange outlines, plus level lines.
    """
    spot = replication["settings"]["index_price"]
    by_strike = replication["iv_times_open_interest"]["by_candidate"][
        f"{IMPLIED_VOLATILITY_UNIT_DECIMAL}_{IV_OI_AGGREGATION_SUM}"
    ]
    levels = _chosen_levels(replication["iv_times_open_interest"]["levels"])
    low, high = _display_window(spot, levels.values())
    strikes = [strike for strike in by_strike if low <= strike <= high]
    height = _bar_height(strikes)

    figure = Figure(figsize=(9, 10), facecolor=BACKGROUND_COLOR)
    axes = figure.add_axes((0.13, 0.08, 0.82, 0.73))
    _style_axes(axes)
    call_values = [by_strike[strike]["call"] for strike in strikes]
    put_values = [by_strike[strike]["put"] for strike in strikes]
    axes.barh(
        [s for s, v in zip(strikes, call_values) if v is not None],
        [v for v in call_values if v is not None],
        height=height, color=POSITIVE_COLOR,
    )
    axes.barh(
        [s for s, v in zip(strikes, put_values) if v is not None],
        [v for v in put_values if v is not None],
        height=height, facecolor="none", edgecolor=NEGATIVE_COLOR, linewidth=1.0,
    )
    handles = _draw_level_lines(axes, levels, spot)
    handles += [
        Patch(facecolor=POSITIVE_COLOR, label="IV*OI Calls"),
        Patch(facecolor="none", edgecolor=NEGATIVE_COLOR, label="IV*OI Puts"),
    ]
    axes.set_ylim(low - height, high + height)
    axes.set_xlim(left=0)
    axes.set_ylabel("Strike Price")
    axes.set_xlabel("IV*OI  (IV decimal x OI in BTC)")
    settings = replication["settings"]
    figure.text(0.05, 0.95, "IV * OI Chart for BTCUSD (DERIBIT) - replica",
                color="white", fontsize=15, fontweight="bold")
    figure.text(0.05, 0.92, f"Timestamp: {timestamp_label}", color="white", fontsize=11)
    figure.text(0.05, 0.895,
                f"Expiration: {settings['expiry_universe']} expiries "
                f"({len(settings['expirations'])})",
                color="white", fontsize=10)
    figure.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.05, 0.875),
                  ncol=3, frameon=False, fontsize=8, labelcolor=FOREGROUND_COLOR)
    figure.savefig(output_path, facecolor=BACKGROUND_COLOR, dpi=120)
    logger.info("Wrote %s", output_path)


def _draw_gex_panel(
    axes, title: str, expiration: str, exposure: Dict[str, Any],
    expiring_percent: Optional[float], spot: float,
) -> None:
    """One per-expiry Net GEX panel ($M per 1% move) with profile line and levels."""
    _style_axes(axes)
    levels = _chosen_levels(exposure["levels"])
    low, high = _display_window(spot, levels.values())
    rows = [row for row in exposure["strikes"] if low <= row["strike"] <= high]
    strikes = [row["strike"] for row in rows]
    net_values = [row["net_gex"] / MILLION for row in rows]
    height = _bar_height(strikes)
    axes.barh(strikes, net_values, height=height,
              color=[POSITIVE_COLOR if value >= 0 else NEGATIVE_COLOR for value in net_values])

    # GEX profile line: cumulative net GEX over strikes (the HVL candidate
    # drawn), rescaled so its largest |value| spans the largest |bar| --
    # MenthorQ draws its profile on the bar axis without stating units.
    cumulative = [row["cumulative_net_gex"] for row in rows]
    largest_bar = max((abs(value) for value in net_values), default=0.0)
    largest_cumulative = max((abs(value) for value in cumulative), default=0.0)
    handles = []
    if largest_bar > 0 and largest_cumulative > 0:
        factor = largest_bar / largest_cumulative
        axes.plot([value * factor for value in cumulative], strikes,
                  color=PROFILE_COLOR, linewidth=1.3)
        handles.append(Line2D([], [], color=PROFILE_COLOR,
                              label="GEX Profile (cumulative, rescaled)"))
    handles += _draw_level_lines(axes, levels, spot)
    handles += [Patch(facecolor=POSITIVE_COLOR, label="Positive GEX"),
                Patch(facecolor=NEGATIVE_COLOR, label="Negative GEX")]
    limit = largest_bar * 1.1 if largest_bar > 0 else 1.0
    axes.set_xlim(-limit, limit)
    axes.set_ylim(low - height, high + height)
    axes.axvline(0, color=FOREGROUND_COLOR, linewidth=0.6)
    axes.set_ylabel("Strike Price")
    axes.set_xlabel("Net GEX ($M per 1% move)")
    settlement = parse_expiry_settlement(expiration)
    expiring_text = (f"{expiring_percent:.2f}%" if expiring_percent is not None
                     else "not computed")
    axes.set_title(
        f"{title}\n{settlement.date().isoformat() if settlement else expiration}"
        f"   GEX Expiring: {expiring_text}",
        color="white", fontsize=10, loc="left",
    )
    axes.legend(handles=handles, loc="lower right", fontsize=6.5, frameon=False,
                labelcolor=FOREGROUND_COLOR)


def render_gex_panels_chart(
    replication: Dict[str, Any], output_path: Path, timestamp_label: str,
    expiring_candidate: str = "net_over_absolute_net_sum",
) -> None:
    """
    2x2 "NET GEX Multi Expirations" figure: first, next, highest and
    second-highest |net GEX| expiry (see ``select_panel_expirations``).
    """
    spot = replication["settings"]["index_price"]
    panels = replication["panels"][PANEL_RANKING_ABSOLUTE_NET]
    figure = Figure(figsize=(16, 15), facecolor=BACKGROUND_COLOR)
    figure.text(0.04, 0.965, "NET GEX Multi Expirations for BTCUSD (DERIBIT) - replica",
                color="white", fontsize=16, fontweight="bold")
    figure.text(0.04, 0.945,
                f"Timestamp: {timestamp_label}   expiry universe: "
                f"{replication['settings']['expiry_universe']}   gamma at: "
                f"{replication['settings']['gamma_underlying']}   "
                f"GEX Expiring % = {expiring_candidate}",
                color="white", fontsize=10)
    grid = figure.add_gridspec(2, 2, left=0.07, right=0.98, bottom=0.05, top=0.9,
                               hspace=0.25, wspace=0.2)
    for position, panel_name in enumerate(PANEL_NAMES):
        axes = figure.add_subplot(grid[position // 2, position % 2])
        expiration = panels.get(panel_name)
        if expiration is None:
            _style_axes(axes)
            axes.set_title(f"{PANEL_TITLES[panel_name]}: no expiry", color="white")
            continue
        _draw_gex_panel(
            axes, PANEL_TITLES[panel_name], expiration,
            replication["gex_by_expiration"][expiration],
            replication["gex_expiring_percent"][expiration][expiring_candidate],
            spot,
        )
    figure.savefig(output_path, facecolor=BACKGROUND_COLOR, dpi=110)
    logger.info("Wrote %s", output_path)
