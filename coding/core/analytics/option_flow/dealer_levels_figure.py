"""
Plotly figure of the flow-based dealer levels of one expiry: two panels side by side on the same strike axis, the flow-based
(inferred) dealer GEX on the left and the standard open-interest based GEX on the right. Bars are USD millions per 1 % move of
the spot: green = positive (dealers long gamma at that strike, they dampen moves), red = negative (dealers short gamma, they
amplify moves). The spot is a white line; each panel has its own call wall, put wall and gamma flip lines.

Pure presentation: every number drawn comes from ``DealerFlowLevels``.
"""

import textwrap
from typing import List, Optional

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from coding.core.analytics.option_flow.dealer_levels import DealerFlowLevels

BACKGROUND_COLOR = "#1b2540"
FOREGROUND_COLOR = "#e6e9f2"
GRID_COLOR = "#33406a"
POSITIVE_COLOR = "#3ddc97"
NEGATIVE_COLOR = "#ff6b6b"
FLIP_COLOR = "#ffe14a"
HEIGHT = 900


def bar_widths(strikes: List[float]) -> List[float]:
    """Bar thickness per strike: 80 % of the distance to the nearest neighbouring strike (strikes are not evenly spaced)."""
    widths = []
    for index, strike in enumerate(strikes):
        gaps = [abs(strike - strikes[other]) for other in (index - 1, index + 1) if 0 <= other < len(strikes)]
        widths.append(0.8 * min(gaps) if gaps else 800.0)
    return widths


def visible_range(levels: DealerFlowLevels, share: float = 0.02) -> Optional[List[float]]:
    """
    Strike range worth drawing: from the lowest to the highest strike whose bar is at least ``share`` of the largest bar of
    its panel (far out-of-the-money strikes are tiny and only squash the chart), widened to include the spot and every level.
    None when there are no rows.
    """
    rows = levels.rows
    if not rows:
        return None
    largest_flow = max(abs(row.flow_gex) for row in rows) or 1.0
    largest_oi = max((abs(row.oi_gex) for row in rows if row.oi_gex is not None), default=0.0) or 1.0
    significant = [row.strike for row in rows
                   if abs(row.flow_gex) >= share * largest_flow or (row.oi_gex is not None and abs(row.oi_gex) >= share * largest_oi)]
    anchors = [levels.index_price] + [value for value in (levels.flow_call_wall, levels.flow_put_wall, levels.flow_gamma_flip,
                                                          levels.oi_call_wall, levels.oi_put_wall, levels.oi_gamma_flip)
                                      if value is not None]
    values = significant + anchors
    padding = 0.5 * min(bar_widths([row.strike for row in rows]) or [1000.0]) / 0.8 + 500.0
    return [min(values) - padding, max(values) + padding]


def _level_line(figure: go.Figure, column: int, strike: Optional[float], label: str, color: str, dash: str) -> None:
    """A dashed horizontal line at a level, with its label (nothing when the level is not computed)."""
    if strike is None:
        return
    figure.add_hline(y=strike, line=dict(color=color, width=1.4, dash=dash), row=1, col=column,
                     annotation_text=f"{label} {strike:,.0f}", annotation_position="top left" if column == 1 else "top right",
                     annotation_font=dict(color=color, size=11))


def build_dealer_levels_figure(levels: DealerFlowLevels) -> go.Figure:
    """Build the two-panel figure. Missing OI-based values are left out (not drawn as zero)."""
    rows = levels.rows
    strikes = [row.strike for row in rows]
    widths = dict(zip(strikes, bar_widths(strikes)))
    figure = make_subplots(
        rows=1, cols=2, shared_yaxes=True, horizontal_spacing=0.03,
        subplot_titles=("Flow-based dealer GEX (from the trades)", "Standard OI-based GEX (dealers long calls, short puts)"))

    flow_values = [row.flow_gex / 1e6 for row in rows]
    figure.add_bar(
        y=strikes, x=flow_values, orientation="h", width=[widths[strike] for strike in strikes], name="Flow-based GEX",
        marker=dict(color=[POSITIVE_COLOR if value >= 0 else NEGATIVE_COLOR for value in flow_values]),
        customdata=[[row.dealer_net_calls, row.dealer_net_puts, row.call_open_interest or 0.0, row.put_open_interest or 0.0]
                    for row in rows],
        hovertemplate=("Strike %{y:,.0f}<br>Flow-based GEX: $%{x:,.2f}M per 1%<br>"
                       "Dealer position calls: %{customdata[0]:,.1f} contracts (open interest %{customdata[2]:,.0f})<br>"
                       "Dealer position puts: %{customdata[1]:,.1f} contracts (open interest %{customdata[3]:,.0f})<extra></extra>"),
        row=1, col=1)
    oi_rows = [row for row in rows if row.oi_gex is not None]
    oi_values = [(row.oi_gex or 0.0) / 1e6 for row in oi_rows]
    figure.add_bar(
        y=[row.strike for row in oi_rows], x=oi_values, orientation="h", width=[widths[row.strike] for row in oi_rows],
        name="OI-based GEX", marker=dict(color=[POSITIVE_COLOR if value >= 0 else NEGATIVE_COLOR for value in oi_values]),
        customdata=[[row.call_open_interest or 0.0, row.put_open_interest or 0.0] for row in oi_rows],
        hovertemplate=("Strike %{y:,.0f}<br>OI-based GEX: $%{x:,.2f}M per 1%<br>"
                       "Open interest calls %{customdata[0]:,.0f}, puts %{customdata[1]:,.0f}<extra></extra>"),
        row=1, col=2)

    for column in (1, 2):
        figure.add_hline(y=levels.index_price, line=dict(color="white", width=1.6), row=1, col=column,
                         annotation_text=f"spot {levels.index_price:,.0f}",
                         annotation_position="bottom left" if column == 1 else "bottom right",
                         annotation_font=dict(color="white", size=11))
    _level_line(figure, 1, levels.flow_call_wall, "call wall", POSITIVE_COLOR, "dash")
    _level_line(figure, 1, levels.flow_put_wall, "put wall", NEGATIVE_COLOR, "dash")
    _level_line(figure, 1, levels.flow_gamma_flip, "gamma flip", FLIP_COLOR, "dot")
    _level_line(figure, 2, levels.oi_call_wall, "call wall", POSITIVE_COLOR, "dash")
    _level_line(figure, 2, levels.oi_put_wall, "put wall", NEGATIVE_COLOR, "dash")
    _level_line(figure, 2, levels.oi_gamma_flip, "gamma flip", FLIP_COLOR, "dot")

    verdict = "PASSED" if levels.gate_passed else f"NOT PASSED ({levels.gate_reason})"
    footer = "<br>".join(textwrap.wrap(f"{levels.currency} {levels.expiry}. Flow since {levels.flow_start:%Y-%m-%d} UTC, {levels.trades_used} trades; chain "
              f"snapshot {levels.chain_captured_at:%Y-%m-%d %H:%M} UTC. Plausibility check (dealer position <= open interest): "
              f"{verdict}. Dealer = the other side of every taker; no ground truth for either view.", 190))
    figure.update_layout(
        title=dict(text=f"<b>{levels.currency} {levels.expiry} dealer gamma levels: flow-based vs open-interest based</b>",
                   x=0.01, font=dict(color=FOREGROUND_COLOR, size=16)),
        height=HEIGHT, showlegend=False, paper_bgcolor=BACKGROUND_COLOR, plot_bgcolor=BACKGROUND_COLOR,
        font=dict(color=FOREGROUND_COLOR), margin=dict(l=70, r=30, t=90, b=110), hovermode="closest", bargap=0.05,
        annotations=list(figure.layout.annotations) + [dict(
            text=footer, x=0.0, y=-0.075, xref="paper", yref="paper", showarrow=False, xanchor="left", yanchor="top",
            align="left", font=dict(size=10))])
    figure.update_xaxes(title_text="USD million per 1 % move", gridcolor=GRID_COLOR, zeroline=True, zerolinecolor=FOREGROUND_COLOR)
    figure.update_yaxes(title_text="Strike", gridcolor=GRID_COLOR, tickformat=",.0f", range=visible_range(levels), row=1, col=1)
    figure.update_yaxes(gridcolor=GRID_COLOR, row=1, col=2)
    return figure
