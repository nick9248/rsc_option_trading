"""
Restyles the dark Plotly figures of the option-flow and dealer-level views for the printed-sheet look of the report: white
paper, ink-black text, light rules, and calmer colours that stay readable on white.

Pure presentation on a copy of the figure: only colours, fonts and the background change. Every value, label and hover text
stays exactly what the figure builder produced.
"""

from typing import Any, Dict, Optional

import plotly.graph_objects as go

PAPER = "#FFFFFF"
INK = "#101114"
GRAY = "#6A6F78"
RULE = "#E4E6EA"
NUMERAL_FONT = "Bahnschrift, Segoe UI, system-ui, sans-serif"
BODY_FONT = "Segoe UI Variable Text, Segoe UI, system-ui, sans-serif"

# Dark-theme colour (lower case) -> colour on paper. Colours not listed stay as they are.
PAPER_COLORS: Dict[str, str] = {
    # backgrounds, text and grid of both dark figures
    "#1b2540": PAPER, "#e6e9f2": INK, "#33406a": RULE, "white": INK, "#ffffff": INK,
    # dealer levels: positive / negative gamma, gamma flip
    "#3ddc97": "#2F7D55", "#ff6b6b": "#B8433A", "#ffe14a": INK,
    # option flow: screen part, then the blocked part (darker shade of the same hue)
    "#7dffb3": "#5FA77F", "#12a083": "#2C6B4A",     # calls bought
    "#10c8ff": "#6E95C8", "#f3b6f7": "#2F5486",     # calls sold
    "#ff7a59": "#E0925F",                           # puts bought (its blocked part: PUT_BUY_BLOCKED_ON_PAPER)
    "#c7608f": "#C27A9E", "#2f7cff": "#7A3557",     # puts sold
}
# The option-flow "put buy blocked" bar shares its dark colour #ffe14a with the dealer gamma-flip line (ink on paper);
# bars get this colour instead.
PUT_BUY_BLOCKED_ON_PAPER = "#9A5426"


def paper_color(color: Optional[str]) -> Optional[str]:
    """The paper colour of a dark-theme colour (unchanged when it has no mapping or is None)."""
    if not isinstance(color, str):
        return color
    return PAPER_COLORS.get(color.strip().lower(), color)


def _bar_color(color: Any) -> Any:
    """Bar colours: a single colour or a list of colours."""
    if isinstance(color, str) and color.strip().lower() == "#ffe14a":
        return PUT_BUY_BLOCKED_ON_PAPER
    if isinstance(color, (list, tuple)):
        return [paper_color(item) for item in color]
    return paper_color(color)


def apply_paper_style(figure: go.Figure) -> go.Figure:
    """
    A copy of ``figure`` in the report's paper style.

    Args:
        figure: A figure of ``build_strike_flow_figure`` or ``build_dealer_levels_figure``.

    Returns:
        A new figure; ``figure`` itself is not changed.
    """
    styled = go.Figure(figure)
    for trace in styled.data:
        marker = getattr(trace, "marker", None)
        if marker is not None and marker.color is not None:
            marker.color = _bar_color(marker.color)
    for shape in styled.layout.shapes:
        if shape.line is not None and shape.line.color is not None:
            shape.line.color = paper_color(shape.line.color)
    for annotation in styled.layout.annotations:
        annotation.font.color = paper_color(annotation.font.color) if annotation.font.color else INK
        annotation.font.family = BODY_FONT
    styled.update_layout(
        paper_bgcolor=PAPER, plot_bgcolor=PAPER,
        font=dict(color=INK, family=BODY_FONT, size=12),
        title=dict(font=dict(color=INK, family=BODY_FONT, size=15)),
        hoverlabel=dict(bgcolor=PAPER, bordercolor=INK, font=dict(color=INK, family=BODY_FONT)),
        legend=dict(font=dict(color=INK)),
    )
    styled.update_xaxes(gridcolor=RULE, zerolinecolor=INK, linecolor=GRAY, tickfont=dict(family=NUMERAL_FONT, color=INK))
    styled.update_yaxes(gridcolor=RULE, linecolor=GRAY, tickfont=dict(family=NUMERAL_FONT, color=INK))
    return styled
