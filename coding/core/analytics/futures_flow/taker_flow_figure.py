"""
Interactive Plotly figure of the rolling 24-hour taker buy/sell ratio of one perpetual, on white paper.

Pure presentation: every number drawn comes from the arguments. A gap in the data is a gap in the line, and a UTC day
that is not complete (a missing hour) has no daily numbers in the hover, never an estimate. No trend line is drawn.
"""

from datetime import datetime, timedelta
from statistics import fmean, pstdev
from typing import List, Optional, Sequence, Tuple

import plotly.graph_objects as go

from coding.core.analytics.futures_flow.taker_flow import TakerFlowDay
from coding.core.analytics.report.figure_style import GRAY, INK, PAPER, RULE
from coding.core.analytics.report.section_fragments import NOT_COMPUTED, millions_text

MOVING_AVERAGE_HOURS = 168                 # 7 days
NEUTRAL_RATIO = 1.0
DAY_NOT_COMPLETE = "day not complete"
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"          # UTC without offset: plotly.js draws the text as written
BAND_FILL = "rgba(106, 111, 120, 0.16)"    # GRAY, light
MOVING_AVERAGE_COLOR = GRAY
FIGURE_HEIGHT = 560


def moving_average(points: Sequence[Tuple[datetime, float]],
                   window_hours: int = MOVING_AVERAGE_HOURS) -> List[Tuple[datetime, float]]:
    """
    Trailing mean of the rolling-ratio values: for each point, the mean of the points whose time lies in the last
    ``window_hours`` hours up to and including that point (time based, so a gap shortens the window instead of
    pulling in older values). Early points average fewer values; the first point is its own mean. It is a plain mean of
    the hourly values, not volume weighted.

    Args:
        points: ``rolling_ratio`` points (time, ratio), oldest first.
        window_hours: Length of the trailing window in hours.

    Returns:
        One (time, mean) per input point, same times.
    """
    window = timedelta(hours=window_hours)
    result = []
    for moment, _ in points:
        inside = [value for other, value in points if moment - window < other <= moment]
        result.append((moment, fmean(inside)))
    return result


def band(points: Sequence[Tuple[datetime, float]]) -> Optional[Tuple[float, float]]:
    """
    ``(mean - sd, mean + sd)`` of all the values (population standard deviation), or None with fewer than 2 points.
    """
    if len(points) < 2:
        return None
    values = [value for _, value in points]
    mean, deviation = fmean(values), pstdev(values)
    return mean - deviation, mean + deviation


def _with_gaps(points: Sequence[Tuple[datetime, float]]) -> Tuple[List[str], List[Optional[float]], List[bool]]:
    """
    The points as x (UTC text of the END of each window: hour start plus one hour), y, and a flag per entry telling it
    is a real point. Where two points are not consecutive hours a None entry is inserted so the line breaks there.
    """
    moments: List[str] = []
    values: List[Optional[float]] = []
    real: List[bool] = []
    previous: Optional[datetime] = None
    for hour_start, value in points:
        if previous is not None and hour_start - previous > timedelta(hours=1):
            moments.append((previous + timedelta(hours=1, minutes=30)).strftime(TIME_FORMAT))
            values.append(None)
            real.append(False)
        moments.append((hour_start + timedelta(hours=1)).strftime(TIME_FORMAT))
        values.append(value)
        real.append(True)
        previous = hour_start
    return moments, values, real


def _hover_day_numbers(hour_start: datetime, days_by_date: dict) -> List[str]:
    """``[day, daily ratio, daily net flow]`` texts of the UTC day of the window's last hour."""
    day = days_by_date.get(hour_start.date())
    if day is None:
        return [hour_start.date().isoformat(), DAY_NOT_COMPLETE, DAY_NOT_COMPLETE]
    ratio = f"{day.ratio:.3f}" if day.ratio is not None else NOT_COMPUTED
    return [day.day.isoformat(), ratio, millions_text(day.net_usd)]


def build_taker_flow_figure(label: str, rolling_points: Sequence[Tuple[datetime, float]],
                            days: Sequence[TakerFlowDay], note: str = "") -> go.Figure:
    """
    The figure: hourly rolling 24-hour ratio, its trailing 7-day mean, the +-1 standard deviation band of the whole window,
    a dashed 1.0 baseline, range buttons (1d, 3d, 7d, all) and a range slider. The hover of the ratio line adds the daily
    ratio and daily net taker flow of the UTC day of the window's last hour.

    Args:
        label: Asset label, e.g. "BTC" (used in the line name).
        rolling_points: ``rolling_ratio`` points (start of the last hour of each window, ratio), oldest first.
        days: Complete UTC days (a day with a missing hour is not in it).
        note: Source note shown under the chart (empty for none).

    Returns:
        A figure without a title (the report shows the heading).
    """
    days_by_date = {day.day: day for day in days}
    moments, values, real = _with_gaps(rolling_points)
    hour_starts = iter(hour_start for hour_start, _ in rolling_points)
    customdata = [_hover_day_numbers(next(hour_starts), days_by_date) if is_real else [None, None, None] for is_real in real]
    average_moments, average_values, _ = _with_gaps(moving_average(rolling_points))

    figure = go.Figure()
    limits = band(rolling_points)
    if limits is not None and moments:
        edges = [moments[0], moments[-1]]
        figure.add_trace(go.Scatter(x=edges, y=[limits[1]] * 2, mode="lines", line=dict(width=0), hoverinfo="skip",
                                    showlegend=False, legendgroup="band"))
        figure.add_trace(go.Scatter(x=edges, y=[limits[0]] * 2, mode="lines", line=dict(width=0), fill="tonexty",
                                    fillcolor=BAND_FILL, hoverinfo="skip", legendgroup="band",
                                    name="band: mean +- 1 standard deviation over the whole window"))
    figure.add_trace(go.Scatter(
        x=average_moments, y=average_values, mode="lines", line=dict(color=MOVING_AVERAGE_COLOR, width=1.6),
        name="7-day mean of the hourly values", hovertemplate="7-day mean %{y:.3f}<extra></extra>"))
    figure.add_trace(go.Scatter(
        x=moments, y=values, mode="lines", line=dict(color=INK, width=1.4), customdata=customdata,
        name=f"{label} rolling 24 h ratio",
        hovertemplate=("Rolling 24 h ratio %{y:.3f}<br>Day %{customdata[0]} (UTC): daily ratio %{customdata[1]}, "
                       "daily net flow %{customdata[2]}<extra></extra>")))

    figure.update_layout(
        height=FIGURE_HEIGHT, margin=dict(l=70, r=20, t=40, b=40), paper_bgcolor=PAPER, plot_bgcolor=PAPER,
        font=dict(color=INK), hovermode="x unified", legend=dict(orientation="h", x=0, y=1.12, font=dict(color=INK)),
        shapes=[dict(type="line", xref="paper", yref="y", x0=0, x1=1, y0=NEUTRAL_RATIO, y1=NEUTRAL_RATIO,
                     line=dict(color=GRAY, width=1, dash="dash"))],
        xaxis=dict(type="date", gridcolor=RULE, hoverformat="%Y-%m-%d %H:%M UTC",
                   rangeselector=dict(buttons=[dict(count=1, label="1d", step="day", stepmode="backward"),
                                               dict(count=3, label="3d", step="day", stepmode="backward"),
                                               dict(count=7, label="7d", step="day", stepmode="backward"),
                                               dict(step="all", label="all")],
                                      bgcolor=PAPER, activecolor=RULE, bordercolor=GRAY, borderwidth=1, font=dict(color=INK)),
                   rangeslider=dict(visible=True, thickness=0.08, bgcolor=PAPER, bordercolor=GRAY, borderwidth=1)),
        yaxis=dict(title=dict(text="Taker buy / sell ratio (rolling 24 h)"), gridcolor=RULE, zeroline=False))
    if note:
        figure.add_annotation(text=note, xref="paper", yref="paper", x=0, y=-0.32, showarrow=False, xanchor="left",
                              font=dict(color=GRAY, size=11))
    return figure
