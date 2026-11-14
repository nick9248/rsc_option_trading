"""
All price levels of the different sources on one chart: a lane per source, one marker per level, the spot as a line, and
shaded bands where several different sources put a level close together (confluence).

Pure: it only arranges ``LevelMark`` values it is given. Where the levels come from is ``LevelOverviewService``.
A confluence is a coincidence of methods, not a proof; the sources are not independent (several use the same open interest).
"""

import textwrap
from dataclasses import dataclass, field
from typing import Dict, List, Sequence

import plotly.graph_objects as go

BACKGROUND_COLOR = "#1b2540"
FOREGROUND_COLOR = "#e6e9f2"
GRID_COLOR = "#33406a"
KIND_COLORS: Dict[str, str] = {
    "resistance": "#3ddc97", "support": "#ff6b6b", "flip": "#ffe14a", "buy": "#10c8ff", "sell": "#e08bd0",
    "long_liquidation": "#ff9f43", "short_liquidation": "#a98bff",
}
KIND_LABELS: Dict[str, str] = {
    "resistance": "call wall / resistance", "support": "put wall / support", "flip": "gamma flip", "buy": "bought (taker)",
    "sell": "sold (taker)", "long_liquidation": "long liquidations", "short_liquidation": "short liquidations",
}
HEIGHT = 900
PRICE_WINDOW = 0.15      # the chart shows prices within this share of the spot


@dataclass(frozen=True)
class LevelMark:
    """One level of one source."""

    source: str     # the lane, e.g. "MenthorQ replica"
    name: str       # what it is, e.g. "call resistance"
    price: float
    kind: str       # a key of KIND_COLORS


@dataclass
class LevelOverview:
    """The collected levels of every source for one currency."""

    currency: str
    spot: float
    expiry: str
    marks: List[LevelMark]
    notes: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class Confluence:
    """A price band where at least ``minimum_sources`` different sources have a level."""

    low: float
    high: float
    marks: List[LevelMark]

    @property
    def sources(self) -> List[str]:
        """The distinct sources in the band, in order of appearance."""
        return list(dict.fromkeys(mark.source for mark in self.marks))


def find_confluences(marks: Sequence[LevelMark], width: float, minimum_sources: int = 3) -> List[Confluence]:
    """
    Bands of at most ``width`` price units (measured from the lowest mark of the band) that hold marks of at least
    ``minimum_sources`` different sources. Marks are taken from the lowest price up; a band closes when the next mark is
    further than ``width`` from its first mark, so every mark belongs to at most one band.

    Raises:
        ValueError: for a non-positive width or fewer than two required sources.
    """
    if width <= 0 or minimum_sources < 2:
        raise ValueError("width must be positive and at least two sources are needed for a confluence")
    ordered = sorted(marks, key=lambda mark: mark.price)
    bands: List[List[LevelMark]] = []
    for mark in ordered:
        if bands and mark.price - bands[-1][0].price <= width:
            bands[-1].append(mark)
        else:
            bands.append([mark])
    return [Confluence(band[0].price, band[-1].price, band) for band in bands
            if len({mark.source for mark in band}) >= minimum_sources]


def merged_labels(marks: Sequence[LevelMark]) -> Dict[LevelMark, str]:
    """
    The text next to each marker. Marks of one source at the same price would print over each other, so the first of them
    carries all names ("put wall / gamma flip 68,000") and the others carry no text.
    """
    groups: Dict[tuple, List[LevelMark]] = {}
    for mark in marks:
        groups.setdefault((mark.source, mark.price), []).append(mark)
    labels: Dict[LevelMark, str] = {}
    for group in groups.values():
        labels[group[0]] = f"{' / '.join(mark.name for mark in group)} {group[0].price:,.0f}"
        for mark in group[1:]:
            labels[mark] = ""
    return labels


def build_level_overview_figure(marks: Sequence[LevelMark], spot: float, confluences: Sequence[Confluence], title: str,
                                footer: str = "", price_window: float = PRICE_WINDOW) -> go.Figure:
    """
    The lane chart: x = source, y = price, spot line, confluence bands. Only prices within ``price_window`` (share of the
    spot) of the spot are drawn, so far out-of-the-money levels do not squash the chart; the footer says how many were left out.
    """
    outside = [mark for mark in marks if abs(mark.price - spot) > price_window * spot]
    marks = [mark for mark in marks if mark not in outside]
    if outside:
        footer += f" {len(outside)} levels beyond {price_window:.0%} of the spot are not drawn (" + ", ".join(
            f"{mark.name} {mark.price:,.0f}" for mark in outside) + ")."
    sources = list(dict.fromkeys(mark.source for mark in marks))
    labels = merged_labels(marks)
    figure = go.Figure()
    for kind, color in KIND_COLORS.items():
        chosen = [mark for mark in marks if mark.kind == kind]
        if not chosen:
            continue
        chosen_labels = [labels[mark] for mark in chosen]
        figure.add_scatter(
            x=[mark.source for mark in chosen], y=[mark.price for mark in chosen], mode="markers+text", name=KIND_LABELS[kind],
            text=chosen_labels, textposition="middle right",
            marker=dict(color=color, size=11, symbol="diamond", line=dict(color="white", width=0.6)),
            customdata=[f"{mark.name} {mark.price:,.0f}" for mark in chosen],
            hovertemplate="%{x}<br>%{customdata}<extra></extra>")
    for band in confluences:
        figure.add_hrect(y0=band.low, y1=band.high, fillcolor="white", opacity=0.10, line_width=0,
                         annotation_text=f"{len(band.sources)} sources", annotation_position="top left",
                         annotation_font=dict(size=10, color=FOREGROUND_COLOR))
    figure.add_hline(y=spot, line=dict(color="white", width=1.6), annotation_text=f"spot {spot:,.0f}",
                     annotation_position="bottom right", annotation_font=dict(color="white"))
    figure.update_layout(
        title=dict(text=f"<b>{title}</b>", x=0.01, font=dict(color=FOREGROUND_COLOR, size=16)), height=HEIGHT,
        paper_bgcolor=BACKGROUND_COLOR, plot_bgcolor=BACKGROUND_COLOR, font=dict(color=FOREGROUND_COLOR),
        xaxis=dict(type="category", categoryorder="array", categoryarray=sources, gridcolor=GRID_COLOR, side="top",
                   range=[-0.4, len(sources) - 1 + 0.9]),
        yaxis=dict(title="Price", gridcolor=GRID_COLOR, tickformat=",.0f", range=[spot * (1 - price_window), spot * (1 + price_window)]),
        legend=dict(orientation="h", yanchor="top", y=-0.06, xanchor="center", x=0.5),
        margin=dict(l=70, r=30, t=110, b=200),
        annotations=[dict(text="<br>".join(textwrap.wrap(footer, 190)), x=0.0, y=-0.13, xref="paper", yref="paper",
                          showarrow=False, xanchor="left", yanchor="top", align="left", font=dict(size=10))] if footer else [])
    return figure
