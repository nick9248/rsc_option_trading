"""
Interactive Plotly figures of the MenthorQ replica for the report (the printed-sheet look is applied by the report with
``apply_paper_style``): IV x OI per strike, net GEX panels, net DEX and IV x OI of one expiry.

Pure presentation of a ``replicate_menthorq`` result: every number drawn comes from that dict. The only arithmetic is the unit
conversion the PNG renderer also does (GEX bars in "gamma x OI x spot, USD" = stored net GEX / (spot x 0.01)), sums of the
call and put part for the hover, and the choice of the strike window. A value that was not computed is not drawn and is
named "not computed" in the hover, never shown as 0. No "$" sign: every amount is written in USD.

Horizontal bars, strikes on the vertical axis, as in MenthorQ's charts. Each level (call resistance, put support, HVL) and the
spot are a labelled dashed line over the strike axis. HVL is a point on the strike axis (the lowest running sum of net GEX),
not a re-priced gamma flip; its hover says so.
"""

from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from coding.core.analytics.menthorq_replication_calculator import (
    DEFAULT_GEX_EXPIRING_CANDIDATE, GEX_PER_ONE_PERCENT_MOVE, IMPLIED_VOLATILITY_UNIT_DECIMAL, IV_OI_AGGREGATION_SUM,
    PANEL_FIRST_EXPIRATION, PANEL_HIGHEST_GEX, PANEL_NAMES, PANEL_NEXT_EXPIRATION, PANEL_RANKING_ABSOLUTE_NET,
    PANEL_SECOND_HIGHEST_GEX, parse_expiry_settlement, select_drawn_levels)

DISPLAY_LOW_MULTIPLIER = 0.75
DISPLAY_HIGH_MULTIPLIER = 1.35
LINE_HOVER_POINTS = 9            # points along a dashed line, so the hover works anywhere on it

POSITIVE_COLOR = "#2F7D55"
NEGATIVE_COLOR = "#B8433A"
CALL_COLOR = "#2F7D55"
PUT_COLOR = "#C8742A"
CALL_RESISTANCE_COLOR = "#B8433A"
PUT_SUPPORT_COLOR = "#2F7D55"
HVL_COLOR = "#9A7B0A"
SPOT_COLOR = "#6A6F78"
GEX_PROFILE_COLOR = "#C9A400"
DEX_PROFILE_COLOR = "#D9822B"

HVL_NOTE = ("HVL is a point on the strike axis: the strike where the running sum of net GEX (added from the lowest strike "
            "up) is lowest. It is not a gamma flip re-priced for a change of spot.")
PANEL_TITLES = {
    PANEL_FIRST_EXPIRATION: "First expiration",
    PANEL_NEXT_EXPIRATION: "Next expiration",
    PANEL_HIGHEST_GEX: "Highest GEX expiration",
    PANEL_SECOND_HIGHEST_GEX: "2nd highest GEX expiration",
}

# (key in the levels dict, label, colour, hover note, label position)
_LEVEL_STYLES: List[Tuple[str, str, str, str, str]] = [
    ("call_resistance", "Call resistance", CALL_RESISTANCE_COLOR,
     "Call resistance: the strike with the highest positive net GEX.", "top"),
    ("put_support", "Put support", PUT_SUPPORT_COLOR, "Put support: the strike with the lowest (most negative) net GEX.",
     "bottom"),
    ("hvl", "HVL", HVL_COLOR, HVL_NOTE, "top"),
]


def display_window(spot: float, level_strikes: Iterable[Optional[float]]) -> Tuple[float, float]:
    """Strike range shown: 0.75 to 1.35 times the spot, widened to include every level that is computed."""
    levels = [strike for strike in level_strikes if strike is not None]
    return min([spot * DISPLAY_LOW_MULTIPLIER] + levels), max([spot * DISPLAY_HIGH_MULTIPLIER] + levels)


def _number_text(value: Optional[float], decimals: int = 0) -> str:
    """'1,234' (or 'not computed' for None)."""
    return "not computed" if value is None else f"{value:,.{decimals}f}"


def _million_text(value: Optional[float]) -> str:
    """'12.3M USD' (or 'not computed')."""
    return "not computed" if value is None else f"{value / 1e6:,.1f}M USD"


def _add_level_lines(figure: go.Figure, levels: Mapping[str, Optional[float]], spot: float, x_low: float, x_high: float,
                     row: Optional[int] = None, column: Optional[int] = None) -> None:
    """The spot and every computed level as a labelled dashed line from ``x_low`` to ``x_high`` (a level of None is not drawn)."""
    step = (x_high - x_low) / (LINE_HOVER_POINTS - 1)
    xs = [x_low + step * index for index in range(LINE_HOVER_POINTS)]
    entries = [(name, levels.get(key), color, note, position) for key, name, color, note, position in _LEVEL_STYLES]
    entries.append(("Spot", spot, SPOT_COLOR, "Spot: the index price.", "bottom"))
    for name, value, color, note, position in entries:
        if value is None:
            continue
        text = f"{name} {value:,.0f} USD<br>{note}"
        figure.add_trace(go.Scatter(x=xs, y=[value] * len(xs), mode="lines", line=dict(color=color, width=1.4, dash="dash"),
                                    hoverinfo="text", hovertext=text, showlegend=False, name=name), row=row, col=column)
        figure.add_annotation(x=x_high, y=value, text=f"{name} {value:,.0f}", showarrow=False, xanchor="right",
                              yanchor=position, font=dict(size=10, color=color), row=row, col=column)


def _style_layout(figure: go.Figure, height: int) -> None:
    """Shared layout: no title (the report shows the heading), closest hover, legend above the plot."""
    figure.update_layout(height=height, hovermode="closest", margin=dict(l=70, r=30, t=50, b=60), bargap=0.15,
                         legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0.0))


def iv_times_open_interest_figure(by_strike: Mapping[float, Mapping[str, Optional[float]]],
                                  levels: Mapping[str, Optional[float]], spot: float, currency: str) -> go.Figure:
    """
    IV x OI per strike: calls as filled bars, puts as outlined bars, over the strike window of ``display_window``.

    Args:
        by_strike: ``{strike: {"call": value or None, "put": value or None}}`` (IV as a decimal times open interest in coins).
        levels: Drawn levels ``call_resistance`` / ``put_support`` / ``hvl`` (None is not drawn).
        spot: Index price.
        currency: "BTC" or "ETH", for the axis title.
    """
    low, high = display_window(spot, levels.values())
    strikes = [strike for strike in sorted(by_strike) if low <= strike <= high]
    hover: List[str] = []
    for strike in strikes:
        call, put = by_strike[strike].get("call"), by_strike[strike].get("put")
        parts = [value for value in (call, put) if value is not None]
        total = sum(parts) if parts else None
        hover.append(f"Strike {strike:,.0f} USD<br>Calls {_number_text(call, 2)}<br>Puts {_number_text(put, 2)}<br>"
                     f"Total {_number_text(total, 2)}<br>(IV decimal x OI in {currency})")
    figure = go.Figure()
    for side, name, fill, line in (("call", "IV x OI calls", CALL_COLOR, CALL_COLOR), ("put", "IV x OI puts", "rgba(0,0,0,0)", PUT_COLOR)):
        drawn = [(strike, by_strike[strike].get(side), text) for strike, text in zip(strikes, hover)
                 if by_strike[strike].get(side) is not None]
        figure.add_trace(go.Bar(x=[value for _, value, _ in drawn], y=[strike for strike, _, _ in drawn], orientation="h",
                                name=name, marker=dict(color=fill, line=dict(color=line, width=1.2)),
                                hoverinfo="text", hovertext=[text for _, _, text in drawn]))
    largest = max((value for strike in strikes for value in by_strike[strike].values() if value is not None), default=0.0)
    x_high = largest * 1.05 if largest > 0 else 1.0
    _add_level_lines(figure, levels, spot, 0.0, x_high)
    _style_layout(figure, 760)
    figure.update_xaxes(title_text=f"IV x OI (IV decimal x open interest in {currency})", range=[0, x_high])
    figure.update_yaxes(title_text="Strike (USD)", tickformat=",.0f", range=[low, high])
    figure.update_layout(barmode="overlay")
    return figure


def replication_iv_times_open_interest_figure(replication: Dict[str, Any], currency: str) -> go.Figure:
    """The IV x OI figure of a ``replicate_menthorq`` result (IV decimal, summed over its expiries, its drawn levels)."""
    by_strike = replication["iv_times_open_interest"]["by_candidate"][f"{IMPLIED_VOLATILITY_UNIT_DECIMAL}_{IV_OI_AGGREGATION_SUM}"]
    levels = select_drawn_levels(replication["iv_times_open_interest"]["levels"])
    return iv_times_open_interest_figure(by_strike, levels, replication["settings"]["index_price"], currency)


def _panel_title(panel_name: str, expiration: str, exposure: Dict[str, Any], expiring_percent: Optional[float]) -> str:
    """'First expiration, 27NOV26 (2026-11-27)<br>GEX expiring 12.34 %, net ..., gross ...'."""
    settlement = parse_expiry_settlement(expiration)
    day = f" ({settlement.date().isoformat()})" if settlement else ""
    expiring = "not computed" if expiring_percent is None else f"{expiring_percent:.2f} %"
    return (f"{PANEL_TITLES[panel_name]}, {expiration}{day}<br>GEX expiring {expiring}, net "
            f"{_million_text(exposure['total_net_gex'])}, gross {_million_text(exposure['total_gross_gex'])} per 1 % move")


def net_gex_panels_figure(replication: Dict[str, Any], currency: str) -> go.Figure:
    """
    One figure with up to four panels (2 x 2): the first expiry, the next expiry, the expiry with the highest and the one with
    the second highest absolute net GEX (``replication["panels"]``). Bars are net GEX per strike in MenthorQ's unit, gamma x OI
    x spot in USD (the stored net GEX per 1 % move divided by spot x 0.01); the hover also gives the call and put part and the
    stored unit. The profile line is the running sum of net GEX, rescaled to the largest bar (an approximation of MenthorQ's
    profile, no unit of its own). Each panel has its own expiry's levels and the spot.
    """
    spot = replication["settings"]["index_price"]
    panels = replication["panels"][PANEL_RANKING_ABSOLUTE_NET]
    scale = spot * GEX_PER_ONE_PERCENT_MOVE
    titles = []
    for panel_name in PANEL_NAMES:
        expiration = panels.get(panel_name)
        titles.append(f"{PANEL_TITLES[panel_name]}: no expiry" if expiration is None else _panel_title(
            panel_name, expiration, replication["gex_by_expiration"][expiration],
            replication["gex_expiring_percent"][expiration][DEFAULT_GEX_EXPIRING_CANDIDATE]))
    figure = make_subplots(rows=2, cols=2, subplot_titles=titles, horizontal_spacing=0.09, vertical_spacing=0.12)
    for annotation in figure.layout.annotations:
        annotation.font.size = 11
    for position, panel_name in enumerate(PANEL_NAMES):
        expiration = panels.get(panel_name)
        row, column = position // 2 + 1, position % 2 + 1
        figure.update_xaxes(title_text="Net GEX (gamma x OI x spot, USD)", row=row, col=column)
        figure.update_yaxes(title_text="Strike (USD)", tickformat=",.0f", row=row, col=column)
        if expiration is None:
            continue
        exposure = replication["gex_by_expiration"][expiration]
        levels = select_drawn_levels(exposure["levels"])
        low, high = display_window(spot, levels.values())
        rows = [item for item in exposure["strikes"] if low <= item["strike"] <= high and item["net_gex"] is not None]
        strikes = [item["strike"] for item in rows]
        values = [item["net_gex"] / scale for item in rows]
        hover = [f"Strike {item['strike']:,.0f} USD<br>Net GEX {value:,.0f} USD (gamma x OI x spot)<br>"
                 f"Call part {item['call_gex'] / scale:,.0f} USD, put part {item['put_gex'] / scale:,.0f} USD<br>"
                 f"Stored unit: net {item['net_gex']:,.0f} USD per 1 % move" for item, value in zip(rows, values)]
        figure.add_trace(go.Bar(x=values, y=strikes, orientation="h", showlegend=False, hoverinfo="text", hovertext=hover,
                                marker=dict(color=[POSITIVE_COLOR if value >= 0 else NEGATIVE_COLOR for value in values])),
                         row=row, col=column)
        largest_bar = max((abs(value) for value in values), default=0.0)
        cumulative = [item["cumulative_net_gex"] for item in rows]
        largest_cumulative = max((abs(value) for value in cumulative), default=0.0)
        if largest_bar > 0 and largest_cumulative > 0:
            factor = largest_bar / largest_cumulative
            figure.add_trace(go.Scatter(
                x=[value * factor for value in cumulative], y=strikes, mode="lines", showlegend=False,
                line=dict(color=GEX_PROFILE_COLOR, width=1.5), hoverinfo="text",
                hovertext=[f"GEX profile at {strike:,.0f} USD: running sum of net GEX {value:,.0f} USD per 1 % move "
                           "(drawn rescaled to the largest bar)" for strike, value in zip(strikes, cumulative)]),
                row=row, col=column)
        limit = largest_bar * 1.1 if largest_bar > 0 else 1.0
        _add_level_lines(figure, levels, spot, -limit, limit, row, column)
        figure.update_xaxes(range=[-limit, limit], row=row, col=column)
        figure.update_yaxes(range=[low, high], row=row, col=column)
    _style_layout(figure, 980)
    figure.update_layout(margin=dict(l=70, r=30, t=70, b=50))
    return figure


def net_dex_figure(replication: Dict[str, Any], currency: str) -> go.Figure:
    """
    Net DEX per strike (bars, USD: delta at the expiry's forward x OI x index price), the DEX profile and the GEX profile (running
    sums rescaled to the largest bar, no unit of their own), the all-expiry levels (from net GEX, not from DEX) and the spot.
    """
    spot = replication["settings"]["index_price"]
    net_dex = replication["net_dex_all_expiries"]
    levels = net_dex["levels"]
    low, high = display_window(spot, levels.values())
    rows = [row for row in net_dex["strikes"] if low <= row["strike"] <= high]
    bar_rows = [row for row in rows if row["net_dex"] is not None]
    figure = go.Figure()
    figure.add_trace(go.Bar(
        x=[row["net_dex"] for row in bar_rows], y=[row["strike"] for row in bar_rows], orientation="h", name="Net DEX",
        marker=dict(color=[POSITIVE_COLOR if row["net_dex"] >= 0 else NEGATIVE_COLOR for row in bar_rows]),
        hoverinfo="text",
        hovertext=[f"Strike {row['strike']:,.0f} USD<br>Net DEX {row['net_dex']:,.0f} USD<br>Running sum "
                   f"{_number_text(row['cumulative_net_dex'])} USD" for row in bar_rows]))
    shown = [row["net_dex"] for row in bar_rows]
    for key, name, color, meaning in (("dex_profile", "DEX profile", DEX_PROFILE_COLOR, "running sum of net DEX"),
                                      ("gex_profile", "GEX profile", GEX_PROFILE_COLOR, "running sum of net GEX")):
        profile_rows = [row for row in rows if row[key] is not None]
        if not profile_rows:
            continue
        figure.add_trace(go.Scatter(
            x=[row[key] for row in profile_rows], y=[row["strike"] for row in profile_rows], mode="lines", name=name,
            line=dict(color=color, width=1.5), hoverinfo="text",
            hovertext=[f"{name} at {row['strike']:,.0f} USD: {meaning}, drawn rescaled to the largest bar "
                       f"({row[key]:,.0f})" for row in profile_rows]))
        shown += [row[key] for row in profile_rows]
    largest = max((abs(value) for value in shown), default=0.0)
    limit = largest * 1.1 if largest > 0 else 1.0
    _add_level_lines(figure, levels, spot, -limit, limit)
    _style_layout(figure, 760)
    figure.update_xaxes(title_text="Net DEX (delta at forward x OI x index price, USD)", range=[-limit, limit])
    figure.update_yaxes(title_text="Strike (USD)", tickformat=",.0f", range=[low, high])
    return figure


def expiry_iv_times_open_interest_figure(expiry_result: Dict[str, Any], spot: float, currency: str) -> go.Figure:
    """The IV x OI figure of one expiry (``compute_expiry_iv_times_open_interest``) with that expiry's own levels."""
    levels = expiry_result["levels"] or {}
    return iv_times_open_interest_figure(expiry_result["by_strike"], levels, spot, currency)
