"""The interactive taker flow figure (arithmetic, gaps, hover, layout) and the service's figure_data. No database."""

import json
from datetime import date, datetime, timedelta, timezone
from statistics import pstdev
from typing import Any, cast

import pytest

from coding.core.analytics.futures_flow.taker_flow import TakerFlowDay, TakerFlowHour, complete_days, rolling_ratio
from coding.core.analytics.futures_flow.taker_flow_figure import (
    DAY_NOT_COMPLETE, band, build_taker_flow_figure, moving_average)
from coding.service.futures_flow.taker_flow_chart_service import TakerFlowChartService

START = datetime(2026, 11, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 11, 13, 14, 20, tzinfo=timezone.utc)
LAST_CLOSED = datetime(2026, 11, 13, 13, tzinfo=timezone.utc)


def points(values, first=START):
    return [(first + timedelta(hours=index), value) for index, value in enumerate(values)]


def trace(figure, name_part):
    return next(item for item in figure.data if name_part in (item.name or ""))


# ---------------------------------------------------------------- arithmetic

def test_moving_average_is_the_mean_of_the_points_in_the_last_window():
    average = moving_average(points([1.0, 2.0, 3.0, 4.0]), window_hours=3)
    assert [value for _, value in average] == [1.0, 1.5, 2.0, 3.0]          # (1), (1,2), (1,2,3), (2,3,4)


def test_moving_average_window_is_time_based_so_a_gap_shortens_it():
    series = [(START, 1.0), (START + timedelta(hours=1), 3.0), (START + timedelta(hours=10), 5.0)]
    average = moving_average(series, window_hours=3)
    assert average[2] == (START + timedelta(hours=10), 5.0)                    # the older points are outside 3 hours


def test_moving_average_default_window_is_seven_days():
    series = points([1.0] + [3.0] * 168)                                       # the 1.0 is 168 hours before the last point
    assert moving_average(series)[-1][1] == pytest.approx(3.0)
    assert moving_average(series)[-2][1] == pytest.approx((1.0 + 3.0 * 167) / 168)


def test_band_is_mean_plus_and_minus_one_population_standard_deviation():
    values = [1.0, 2.0, 3.0, 4.0]
    low, high = cast(Any, band(points(values)))
    assert low == pytest.approx(2.5 - pstdev(values)) and high == pytest.approx(2.5 + pstdev(values))
    assert pstdev(values) == pytest.approx(1.118034, abs=1e-6)                 # sqrt(1.25)


def test_band_needs_two_points():
    assert band([]) is None and band(points([1.0])) is None


# ---------------------------------------------------------------- figure

def day(value_date, buy, sell):
    return TakerFlowDay(value_date, buy, sell)


def test_ratio_line_breaks_where_an_hour_is_missing():
    series = [(START, 1.0), (START + timedelta(hours=1), 1.1), (START + timedelta(hours=4), 1.2)]
    ratio = trace(build_taker_flow_figure("BTC", series, []), "rolling 24 h ratio")
    assert list(ratio.y) == [1.0, 1.1, None, 1.2]
    assert list(ratio.x)[0] == "2026-11-01 01:00:00"                           # plotted at the END of the window


def test_hover_customdata_is_the_daily_ratio_and_net_flow_of_that_utc_day():
    days = [day(date(2026, 11, 1), 90e6, 100e6), day(date(2026, 11, 2), 120e6, 100e6)]
    series = [(START + timedelta(hours=5), 0.9), (START + timedelta(hours=30), 1.2), (START + timedelta(hours=60), 1.0)]
    ratio = trace(build_taker_flow_figure("BTC", series, days), "rolling 24 h ratio")
    custom = [list(row) for row in ratio.customdata if row[0] is not None]        # skip the break markers
    assert len(custom) == 3
    assert custom[0] == ["2026-11-01", "0.900", "-10.0M USD"]
    assert custom[1] == ["2026-11-02", "1.200", "20.0M USD"]
    assert custom[2] == ["2026-11-03", DAY_NOT_COMPLETE, DAY_NOT_COMPLETE]     # 11-03 is not in the complete days


def test_hover_matches_complete_days_of_real_shaped_hours():
    hours = [TakerFlowHour("BTCUSDT", START + timedelta(hours=index), 100.0 + index, 100.0) for index in range(60)]
    days = complete_days(hours)
    figure = build_taker_flow_figure("BTC", rolling_ratio(hours), days)
    ratio = trace(figure, "rolling 24 h ratio")
    first_day = days[0]
    assert list(ratio.customdata[0])[1] == f"{first_day.ratio:.3f}"
    assert list(ratio.customdata[0])[2] == f"{first_day.net_usd / 1e6:,.1f}M USD"


def test_moving_average_and_band_traces_are_drawn():
    figure = build_taker_flow_figure("BTC", points([1.0, 2.0, 3.0, 4.0]), [])
    average = trace(figure, "7-day mean")
    assert list(average.y) == [1.0, 1.5, 2.0, 2.5]
    lower = trace(figure, "band: mean +- 1 standard deviation over the whole window")
    assert lower.fill == "tonexty" and list(lower.y) == [pytest.approx(2.5 - pstdev([1, 2, 3, 4]))] * 2
    upper = next(item for item in figure.data if item.legendgroup == "band" and item is not lower)
    assert list(upper.y) == [pytest.approx(2.5 + pstdev([1, 2, 3, 4]))] * 2


def test_a_single_point_has_no_band():
    figure = build_taker_flow_figure("BTC", points([1.0]), [])
    assert not any(item.legendgroup == "band" for item in figure.data)


def test_baseline_is_a_dashed_line_at_one_and_there_is_no_title():
    figure = build_taker_flow_figure("BTC", points([1.0, 1.1]), [])
    (shape,) = figure.layout.shapes
    assert shape.y0 == shape.y1 == 1.0 and shape.line.dash == "dash"
    assert figure.layout.title.text is None


def test_range_buttons_slider_and_axis_title():
    figure = build_taker_flow_figure("BTC", points([1.0, 1.1]), [])
    buttons = figure.layout.xaxis.rangeselector.buttons
    assert [button.label for button in buttons] == ["1d", "3d", "7d", "all"]
    assert [button.count for button in buttons[:3]] == [1, 3, 7] and buttons[3].step == "all"
    assert figure.layout.xaxis.type == "date" and figure.layout.xaxis.rangeslider.visible
    assert figure.layout.yaxis.title.text == "Taker buy / sell ratio (rolling 24 h)"


def test_white_paper_and_no_dollar_sign_in_any_text():
    days = [day(date(2026, 11, 1), 90e6, 100e6)]
    figure = build_taker_flow_figure("BTC", points([1.0, 1.1, 1.2], START + timedelta(hours=5)), days, "Source: test.")
    assert figure.layout.paper_bgcolor == "#FFFFFF" and figure.layout.plot_bgcolor == "#FFFFFF"
    assert "$" not in json.dumps(figure.to_plotly_json())


def test_no_points_gives_an_empty_but_valid_figure():
    figure = build_taker_flow_figure("BTC", [], [])
    assert trace(figure, "rolling 24 h ratio").y is None or len(trace(figure, "rolling 24 h ratio").y) == 0


# ---------------------------------------------------------------- service

def hours_for(symbol, days, buy, sell, last_hour=LAST_CLOSED):
    return [TakerFlowHour(symbol, last_hour - timedelta(hours=offset), buy, sell) for offset in range(24 * days)][::-1]


class FakeRepository:
    def __init__(self, hours):
        self.hours = hours

    def get_taker_flow_hours(self, symbol, first_hour, last_hour):
        return [h for h in self.hours if h.symbol == symbol and first_hour <= h.hour_utc <= last_hour]


def service_for(hours):
    return TakerFlowChartService(cast(Any, FakeRepository(hours)), clock=lambda: NOW)


def test_figure_data_has_hours_points_days_and_newest_hour_and_writes_nothing(tmp_path):
    hours = hours_for("BTCUSDT", 10, 90.0, 100.0)
    data = service_for(hours).figure_data("BTCUSDT")
    assert data.label == "BTC" and len(data.hours) == 240 and data.newest_hour_utc == LAST_CLOSED
    assert data.rolling_points == rolling_ratio(hours) and data.days == complete_days(hours)
    assert data.warnings == [] and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("variant", ["gap", "empty", "stale"])
def test_figure_data_warnings_are_those_of_generate(tmp_path, variant):
    if variant == "gap":
        hours = hours_for("BTCUSDT", 10, 90.0, 100.0)
        del hours[30]
    elif variant == "empty":
        hours = []
    else:
        hours = hours_for("BTCUSDT", 9, 90.0, 100.0, last_hour=LAST_CLOSED - timedelta(hours=10))
    service = TakerFlowChartService(cast(Any, FakeRepository(hours)), symbols=("BTCUSDT",), clock=lambda: NOW)
    assert service.figure_data("BTCUSDT").warnings == service.generate(tmp_path).warnings
    assert service.figure_data("BTCUSDT").warnings                              # each variant really warns


def test_figure_data_without_hours_has_no_newest_hour():
    data = service_for([]).figure_data("BTCUSDT")
    assert data.newest_hour_utc is None and data.rolling_points == [] and data.days == []
