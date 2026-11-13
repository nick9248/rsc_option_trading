"""Binance history loader (fake HTTP session, temporary cache) and the research script helpers. No network."""

import argparse
import io
import zipfile
from datetime import date, datetime, timedelta, timezone
from typing import Any, cast

import numpy as np
import pytest

from coding.core.analytics.liquidation_heatmap.liquidation_heatmap_model import HeatmapResult
from coding.service.liquidation_heatmap.binance_history_loader import BinanceHistoryError, BinanceHistoryLoader
from scripts.research_liquidation_heatmap import floor_to_step, parse_numbers, parse_utc, strongest_levels

DAY = datetime(2026, 11, 10, tzinfo=timezone.utc)


def archive_zip(day: str, values) -> bytes:
    lines = ["create_time,symbol,sum_open_interest,sum_open_interest_value"]
    for index, value in enumerate(values):
        stamp = datetime.strptime(day, "%Y-%m-%d") + timedelta(minutes=5 * index)
        lines.append(f"{stamp:%Y-%m-%d %H:%M:%S},BTCUSDT,{value},0")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"BTCUSDT-metrics-{day}.csv", "\n".join(lines))
    return buffer.getvalue()


class Response:
    def __init__(self, status=200, payload=None, content=b""):
        self.status_code, self.payload, self.content = status, payload, content

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        for fragment, response in self.routes.items():
            if fragment in url:
                return response
        return Response(404)


def test_archive_day_is_parsed_and_cached(tmp_path):
    session = FakeSession({"2026-11-10.zip": Response(content=archive_zip("2026-11-10", [100.0, 101.5, 102.0]))})
    loader = BinanceHistoryLoader(tmp_path, cast(Any, session))
    samples, notes = loader.load_open_interest("BTCUSDT", DAY, DAY + timedelta(minutes=10), today=date(2026, 11, 13))
    assert samples == {DAY: 100.0, DAY + timedelta(minutes=5): 101.5, DAY + timedelta(minutes=10): 102.0}
    assert notes == []
    loader.load_open_interest("BTCUSDT", DAY, DAY + timedelta(minutes=10), today=date(2026, 11, 13))
    assert len(session.calls) == 1                                          # second call reads the cache


def test_an_old_day_missing_from_the_archive_is_reported_not_filled(tmp_path):
    loader = BinanceHistoryLoader(tmp_path, cast(Any, FakeSession({})))
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    samples, notes = loader.load_open_interest("BTCUSDT", old, old + timedelta(hours=1), today=date(2026, 11, 13))
    assert samples == {} and notes == ["open interest of 2020-01-01 not available from the archive or REST"]


def test_today_comes_from_rest(tmp_path):
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    rows = [{"timestamp": int((start + timedelta(minutes=5 * index)).timestamp() * 1000),
             "sumOpenInterest": str(100 + index)} for index in range(3)]
    session = FakeSession({"openInterestHist": Response(payload=rows)})
    samples, notes = BinanceHistoryLoader(tmp_path, cast(Any, session)).load_open_interest(
        "BTCUSDT", start, start + timedelta(minutes=10), today=now.date())
    assert samples == {start: 100.0, start + timedelta(minutes=5): 101.0, start + timedelta(minutes=10): 102.0}
    assert notes == []


def test_candles_are_parsed_and_limited_to_the_window(tmp_path):
    def row(minutes):
        opened = DAY + timedelta(minutes=minutes)
        # open time, open, high, low, close, volume, close time, quote volume, trades, taker buy base, taker buy quote, ignore
        return [int(opened.timestamp() * 1000), "1", "12", "9", "10", "5", 0, "50", 7, "2", "20", "0"]
    session = FakeSession({"klines": Response(payload=[row(0), row(5), row(10)])})
    candles = BinanceHistoryLoader(tmp_path, cast(Any, session)).load_candles("BTCUSDT", DAY, DAY + timedelta(minutes=10))
    assert [candle.open_time for candle in candles] == [DAY, DAY + timedelta(minutes=5)]
    assert (candles[0].high, candles[0].low, candles[0].close) == (12.0, 9.0, 10.0)
    assert (candles[0].volume_btc, candles[0].taker_buy_btc) == (5.0, 2.0)


def test_a_binance_error_raises(tmp_path):
    with pytest.raises(BinanceHistoryError):
        BinanceHistoryLoader(tmp_path, cast(Any, FakeSession({"klines": Response(status=451)}))).load_candles(
            "BTCUSDT", DAY, DAY + timedelta(minutes=5))


def test_script_helpers():
    assert floor_to_step(datetime(2026, 11, 13, 14, 23, 41, tzinfo=timezone.utc)) == datetime(
        2026, 11, 13, 14, 20, tzinfo=timezone.utc)
    assert parse_utc("2026-11-13T14:20Z") == datetime(2026, 11, 13, 14, 20, tzinfo=timezone.utc)
    with pytest.raises(argparse.ArgumentTypeError):
        parse_utc("2026-11-13T14:20")
    assert parse_numbers("25, 50,100", int) == [25, 50, 100]


def test_strongest_levels_ranks_by_usd_and_skips_empty_bins():
    centers = np.array([100.0, 200.0, 300.0, 400.0])
    long_btc = np.array([[np.nan] * 4, [1.0, 0.0, 5.0, 0.0]], dtype=np.float32)
    short_btc = np.array([[np.nan] * 4, [0.0, 0.0, 0.0, 2.0]], dtype=np.float32)
    start = datetime(2026, 11, 13, tzinfo=timezone.utc)
    result = HeatmapResult([start, start + timedelta(minutes=5)], centers, long_btc, short_btc)
    levels = strongest_levels(result)
    assert [(level["price"], level["side"]) for level in levels] == [(300.0, "long"), (400.0, "short"), (100.0, "long")]
    assert levels[0]["usd"] == pytest.approx(1500.0)


def kline_csv(month_start, count, header=True, microseconds=False):
    lines = ["open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore"] if header else []
    for index in range(count):
        opened = int((month_start + timedelta(minutes=5 * index)).timestamp() * 1000)
        lines.append(f"{opened * 1000 if microseconds else opened},1,12,9,10,5,0,50,7,2,20,0")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("klines.csv", "\n".join(lines))
    return buffer.getvalue()


def test_long_candles_use_the_monthly_archive_for_full_months_and_rest_for_the_rest(tmp_path):
    month = datetime(2026, 9, 1, tzinfo=timezone.utc)
    october = datetime(2026, 10, 1, tzinfo=timezone.utc)
    rest_row = [int(october.timestamp() * 1000), "1", "12", "9", "10", "5", 0, "50", 7, "2", "20", "0"]
    session = FakeSession({"2026-09.zip": Response(content=kline_csv(month, 3, microseconds=True)),
                           "klines?": Response(payload=[rest_row])})
    session.routes["fapi/v1/klines"] = session.routes.pop("klines?")
    loader = BinanceHistoryLoader(tmp_path, cast(Any, session))
    candles = loader.load_candles_long("BTCUSDT", month, october + timedelta(minutes=5))
    assert [candle.open_time for candle in candles] == [month + timedelta(minutes=5 * i) for i in range(3)] + [october]
    assert candles[0].volume_btc == 5.0 and candles[0].taker_buy_btc == 2.0
    assert sum("zip" in call[0] for call in session.calls) == 1             # one archive month, the rest by REST


def test_a_month_missing_from_the_archive_falls_back_to_rest(tmp_path):
    month = datetime(2026, 9, 1, tzinfo=timezone.utc)
    rest_row = [int(month.timestamp() * 1000), "1", "12", "9", "10", "5", 0, "50", 7, "2", "20", "0"]
    session = FakeSession({"fapi/v1/klines": Response(payload=[rest_row])})
    candles = BinanceHistoryLoader(tmp_path, cast(Any, session)).load_candles_long(
        "BTCUSDT", month, datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert [candle.open_time for candle in candles] == [month]
