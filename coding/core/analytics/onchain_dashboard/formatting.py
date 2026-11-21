"""
Text and figure-data helpers of the on-chain dashboard. Only formatting: a missing value is written as "not computed"
(never as zero), and values that cannot be drawn or written as JSON (infinity, NaN) never reach a figure.
"""

import math
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import plotly.graph_objects as go

from coding.core.analytics.report.report_page import format_utc
from coding.core.analytics.report.section_fragments import NOT_COMPUTED

NO_CALLS_TEXT = "no calls"
GREEN = "#2F7D55"
RED = "#B8433A"
BLUE = "#2F5486"
ORANGE = "#E0925F"
PURPLE = "#7A3557"
GRAY = "#6A6F78"


def is_usable(value: Any) -> bool:
    """True for a real, finite number."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def clean(value: Any) -> Optional[float]:
    """The value as a float, or None when it is missing, NaN or infinite (a figure leaves a gap there)."""
    return float(value) if is_usable(value) else None


def clean_all(values: Iterable[Any]) -> List[Optional[float]]:
    """``clean`` for each value."""
    return [clean(value) for value in values]


def number_text(value: Optional[float], decimals: int = 2, suffix: str = "", signed: bool = False) -> str:
    """A number with thousands separators; "not computed" when missing or not finite."""
    if not is_usable(value):
        return NOT_COMPUTED
    return f"{value:{'+' if signed else ''},.{decimals}f}{suffix}"


def percent_text(value: Optional[float], decimals: int = 1) -> str:
    """A value already in percent (37.5 -> "37.5%"); "not computed" when missing."""
    return number_text(value, decimals, "%")


def fraction_percent_text(value: Optional[float], decimals: int = 2) -> str:
    """A decimal fraction as percent (0.585 -> "58.50%"); "not computed" when missing."""
    return number_text(None if not is_usable(value) else value * 100, decimals, "%")


def strike_text(value: Optional[float]) -> str:
    """A strike or price level without decimals; "not computed" when missing."""
    return number_text(value, 0)


def usd_text(value: Optional[float], signed: bool = True) -> str:
    """USD with thousands separators and no decimals; "not computed" when missing."""
    return number_text(value, 0, " USD", signed=signed)


def ratio_text(value: Optional[float], infinite_text: str = NO_CALLS_TEXT) -> str:
    """A ratio; infinity (nothing in the denominator) is written as ``infinite_text``, missing as "not computed"."""
    if isinstance(value, float) and math.isinf(value):
        return infinite_text
    return number_text(value, 2)


def has_open_interest(ratio) -> bool:
    """False for an expiry nobody has opened a position in (new listing): its put/call ratio is then undefined, not 0."""
    return (ratio.total_call_oi + ratio.total_put_oi) > 0


def count_text(value: Optional[float]) -> str:
    """A whole number; "not computed" when missing."""
    return number_text(value, 0)


def yes_no(value: Optional[bool]) -> str:
    """"yes" / "no"; "not computed" when missing."""
    if value is None:
        return NOT_COMPUTED
    return "yes" if value else "no"


def text_or_missing(value: Optional[str]) -> str:
    """The text, or "not computed" for None or an empty string."""
    return value if value else NOT_COMPUTED


def empty_figure(message: str) -> go.Figure:
    """A figure without data that says why (a switcher view needs something to draw, and a stale plot must not stay)."""
    figure = go.Figure()
    figure.update_xaxes(visible=False)
    figure.update_yaxes(visible=False)
    figure.add_annotation(text=message, x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False, font=dict(size=15))
    figure.update_layout(height=260, margin=dict(l=20, r=20, t=20, b=20))
    return figure


def has_values(*series: Iterable[Optional[float]]) -> bool:
    """True when at least one series holds a real number."""
    return any(value is not None for values in series for value in values)


def add_markers(figure: go.Figure, markers: Dict[str, Optional[float]], colors: Dict[str, str]) -> None:
    """Vertical dashed lines with a label for each marker that has a position (missing ones are left out)."""
    shown = 0
    for label, position in markers.items():
        if position is None:
            continue
        shown += 1
        figure.add_shape(type="line", x0=position, x1=position, y0=0, y1=1, xref="x", yref="paper",
                         line=dict(color=colors.get(label, GRAY), width=1.5, dash="dash"))
        figure.add_annotation(x=position, y=1, xref="x", yref="paper", text=label, showarrow=False, yanchor="bottom", yshift=14 * (shown % 2),
                              font=dict(size=11, color=colors.get(label, GRAY)))


def as_utc(instant: datetime) -> datetime:
    """
    The instant as UTC-aware. A naive instant is read as UTC: the services store their freshness timestamps as naive
    UTC values (see ``OnChainAnalysisService._build_normalized_metrics``), and the result's own time is UTC-aware.
    """
    return instant.replace(tzinfo=timezone.utc) if instant.tzinfo is None else instant


def utc_text(instant: Optional[datetime]) -> str:
    """An instant as "2026-11-14 10:29 UTC"; "time not known" when missing."""
    return "time not known" if instant is None else format_utc(as_utc(instant))
