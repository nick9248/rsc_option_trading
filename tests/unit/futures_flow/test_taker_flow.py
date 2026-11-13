"""Taker flow calculator: ratios, complete days, gaps, rolling ratio and the brief summary (hand-checked numbers)."""

from datetime import date, datetime, timedelta, timezone

import pytest

from coding.core.analytics.futures_flow.taker_flow import (
    TakerFlowHour,
    complete_days,
    missing_hours,
    rolling_ratio,
    start_of_utc_hour,
    summarise,
    taker_buy_sell_ratio,
)

DAY_START = datetime(2026, 11, 1, tzinfo=timezone.utc)


def make_hours(first: datetime, count: int, buy: float, sell: float, symbol: str = "BTCUSDT"):
    return [TakerFlowHour(symbol, first + timedelta(hours=offset), buy, sell) for offset in range(count)]


def test_ratio_is_buy_over_sell_and_none_without_sell_volume():
    assert taker_buy_sell_ratio(90.0, 100.0) == pytest.approx(0.9)
    assert taker_buy_sell_ratio(10.0, 0.0) is None
    assert TakerFlowHour("BTCUSDT", DAY_START, 5.0, 0.0).ratio is None


def test_start_of_hour_requires_timezone():
    assert start_of_utc_hour(datetime(2026, 11, 1, 5, 59, 59, tzinfo=timezone.utc)) == datetime(
        2026, 11, 1, 5, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        start_of_utc_hour(datetime(2026, 11, 1, 5, 30))


def test_complete_days_sums_volumes_and_drops_days_with_a_missing_hour():
    day_one = make_hours(DAY_START, 24, 10.0, 20.0)                       # complete
    day_two = make_hours(DAY_START + timedelta(days=1), 23, 10.0, 20.0)   # one hour missing
    days = complete_days(day_one + day_two)
    assert len(days) == 1
    assert days[0].day == date(2026, 11, 1)
    assert days[0].taker_buy_usd == 240.0 and days[0].taker_sell_usd == 480.0
    assert days[0].ratio == pytest.approx(0.5) and days[0].net_usd == -240.0


def test_duplicate_hours_do_not_complete_a_day():
    assert complete_days(make_hours(DAY_START, 12, 1.0, 1.0) * 2) == []


def test_missing_hours_lists_gaps_only():
    present = [DAY_START + timedelta(hours=offset) for offset in (0, 1, 3)]
    assert missing_hours(present, DAY_START, DAY_START + timedelta(hours=4)) == [
        DAY_START + timedelta(hours=2), DAY_START + timedelta(hours=4)]


def test_rolling_ratio_has_no_point_where_the_window_has_a_gap():
    hours = make_hours(DAY_START, 60, 1.0, 2.0)          # hours 0..59
    del hours[10]                                        # hour 10 missing
    points = rolling_ratio(hours)
    # A window ending at hour e covers e-23..e. It is complete only when it does not contain hour 10:
    # e >= 34 (window 11..34). Ends 34..59 give 26 points; before that there are none.
    assert [end for end, _ in points] == [DAY_START + timedelta(hours=offset) for offset in range(34, 60)]
    assert all(ratio == pytest.approx(0.5) for _, ratio in points)


def test_summary_without_data_says_why():
    summary = summarise("BTCUSDT", [])
    assert summary.last_24_hours_ratio is None and summary.seven_day_ratio is None
    assert summary.not_computed == ["BTCUSDT: no hourly data stored"]


def test_summary_numbers_over_eight_days_by_hand():
    # 8 full UTC days: day k (k = 0..7) has buy = 100 + k, sell = 100 each hour.
    hours = []
    for day in range(8):
        hours += make_hours(DAY_START + timedelta(days=day), 24, 100.0 + day, 100.0)
    summary = summarise("BTCUSDT", hours)
    last = summary.last_complete_day
    assert last is not None and last.day == date(2026, 11, 8)
    assert last.ratio == pytest.approx(1.07)                       # day 7: 107 / 100
    # 7-day window = days 1..7: buy sum = (101+...+107) * 24, sell sum = 700 * 24
    assert summary.seven_day_days_used == 7
    assert summary.seven_day_ratio == pytest.approx((101 + 102 + 103 + 104 + 105 + 106 + 107) / 700)
    assert summary.highest_day is not None and summary.highest_day.day == date(2026, 11, 8)
    assert summary.lowest_day is not None and summary.lowest_day.day == date(2026, 11, 2)
    assert summary.last_24_hours_ratio == pytest.approx(1.07)
    assert summary.not_computed == []


def test_summary_reports_missing_hours_and_short_weeks():
    hours = make_hours(DAY_START, 24 * 3, 100.0, 100.0)
    del hours[-5]                                       # a hole inside the last 24 hours
    summary = summarise("ETHUSDT", hours)
    assert summary.last_24_hours_ratio is None
    assert summary.seven_day_days_used == 2             # the third day lost an hour
    assert any("last 24 hours ratio not computed, 1 of 24 hours missing" in reason for reason in summary.not_computed)
    assert any("only 2 of 7 days are complete" in reason for reason in summary.not_computed)
    # A short week is never shown as a 7-day number.
    assert summary.seven_day_ratio is None and summary.highest_day is None and summary.lowest_day is None
    assert summary.last_complete_day is not None


def test_zero_sell_volume_is_reported_not_silent():
    summary = summarise("BTCUSDT", make_hours(DAY_START, 24, 5.0, 0.0))
    assert summary.last_24_hours_ratio is None
    assert "last 24 hours ratio not computed, no sell volume" in " ".join(summary.not_computed)
