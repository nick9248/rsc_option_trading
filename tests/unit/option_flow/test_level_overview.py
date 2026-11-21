"""Level overview: confluence bands, merged labels, the strikes chosen from the flow."""

from datetime import datetime, timedelta, timezone

import pytest

from coding.core.analytics.option_flow.level_overview import (
    LevelMark, build_level_overview_figure, find_confluences, merged_labels)
from coding.core.analytics.option_flow.strike_flow import OptionTrade, aggregate_by_strike
from coding.service.option_flow.level_overview_service import (
    LevelOverviewService, top_strikes)

NOW = datetime(2026, 11, 13, 21, 25, tzinfo=timezone.utc)


def mark(source, price, kind="support", name="level"):
    return LevelMark(source, name, price, kind)


def test_confluence_needs_enough_different_sources_inside_the_band():
    marks = [mark("A", 68_000), mark("B", 68_300), mark("C", 68_600), mark("A", 80_000), mark("B", 80_100)]
    bands = find_confluences(marks, width=1_000, minimum_sources=3)
    assert len(bands) == 1 and (bands[0].low, bands[0].high) == (68_000, 68_600) and bands[0].sources == ["A", "B", "C"]
    assert find_confluences(marks, width=1_000, minimum_sources=4) == []


def test_two_marks_of_the_same_source_do_not_make_a_confluence():
    assert find_confluences([mark("A", 68_000), mark("A", 68_100), mark("B", 68_200)], 1_000, 3) == []


def test_a_band_is_measured_from_its_first_mark_and_marks_belong_to_one_band():
    marks = [mark("A", 100), mark("B", 150), mark("C", 190), mark("A", 260), mark("B", 300), mark("C", 330)]
    bands = find_confluences(marks, width=100, minimum_sources=3)
    assert [(b.low, b.high) for b in bands] == [(100, 190), (260, 330)]


def test_confluence_rejects_bad_arguments():
    with pytest.raises(ValueError):
        find_confluences([], 0.0)
    with pytest.raises(ValueError):
        find_confluences([], 10.0, minimum_sources=1)


def test_marks_of_one_source_at_one_price_share_one_label():
    first, second, other = mark("A", 68_000, name="put wall"), mark("A", 68_000, "flip", "gamma flip"), mark("B", 68_000, name="x")
    labels = merged_labels([first, second, other])
    assert labels[first] == "put wall / gamma flip 68,000" and labels[second] == "" and labels[other] == "x 68,000"


def test_levels_far_from_the_spot_are_not_drawn_but_named_in_the_footer():
    marks = [mark("A", 68_000, name="near"), mark("A", 120_000, name="far")]
    figure = build_level_overview_figure(marks, 68_000.0, [], "t", "footer")
    assert [y for trace in figure.data for y in trace.y] == [68_000]
    assert "far 120,000" in figure.layout.annotations[-1].text and "1 levels beyond 15%" in figure.layout.annotations[-1].text


def test_top_strikes_sum_screen_and_blocked_notional_of_one_type_and_side():
    flow = aggregate_by_strike([
        OptionTrade("27NOV26", 70_000.0, "P", "buy", 2.0, False, 70_000.0), OptionTrade("27NOV26", 70_000.0, "P", "buy", 3.0, True, 70_000.0),
        OptionTrade("27NOV26", 72_000.0, "P", "buy", 1.0, False, 70_000.0), OptionTrade("27NOV26", 75_000.0, "C", "buy", 9.0, False, 70_000.0)])
    assert top_strikes(flow, "P", "buy", 5) == [(70_000.0, 5.0 * 70_000.0), (72_000.0, 1.0 * 70_000.0)]       # contracts x the index 70,000
    assert top_strikes(flow, "P", "buy", 1) == [(70_000.0, 5.0 * 70_000.0)]
    assert top_strikes(flow, "P", "sell", 3) == []


class FakeRepository:
    def __init__(self, flow_trades, newest=NOW - timedelta(hours=1)):
        self.flow_trades, self.newest = flow_trades, newest

    def get_option_trades_between(self, currency, start_ts, end_ts, expiration=None):
        return self.flow_trades

    def get_latest_trade_time(self, currency):
        return self.newest

    def get_first_block_trade_time(self, currency):
        return None


def test_flow_levels_are_skipped_with_a_note_when_there_are_no_trades():
    service = LevelOverviewService(FakeRepository([]), clock=lambda: NOW)
    marks, notes = [], []
    service._add_flow_levels("BTC", 24, 2, marks, notes)
    assert marks == [] and "no trades" in notes[0]
    trades = [OptionTrade("27NOV26", 70_000.0, "C", "sell", 2.0, False, 70_000.0)]
    service = LevelOverviewService(FakeRepository(trades), clock=lambda: NOW)
    service._add_flow_levels("BTC", 24, 2, marks, notes)
    assert [(m.kind, m.price) for m in marks] == [("sell", 70_000.0)] and marks[0].name.startswith("calls sold")
