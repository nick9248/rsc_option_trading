"""
The look shared by the charts of the dashboards: the paper palette (the page recolours it for the light and dark themes, see
unified_dashboard/dashboard.js) and one base layout. New charts (equity curves, distributions, ...) start from ``base_layout`` and use
these colours, so every chart of the unified dashboard looks alike and follows the theme switch.
"""

import plotly.graph_objects as go

GREEN = "#2F7D55"
RED = "#B8433A"
INK = "#101114"
GRAY = "#6A6F78"
RULE = "#E4E6EA"
AMBER = "#9A7B0A"
PAPER = "#FFFFFF"
CHART_HEIGHT = 360


def base_layout(figure: go.Figure, x_title: str, y_title: str, height: int = CHART_HEIGHT) -> go.Figure:
    """Axis titles, margins, grid and legend of a dashboard chart (colours are the paper palette); the figure is changed and returned."""
    figure.update_layout(height=height, margin=dict(l=70, r=20, t=30, b=50), xaxis_title=x_title, yaxis_title=y_title,
                         paper_bgcolor=PAPER, plot_bgcolor=PAPER, font=dict(color=INK), showlegend=False,
                         xaxis=dict(gridcolor=RULE, zeroline=False), yaxis=dict(gridcolor=RULE, zeroline=False))
    return figure
