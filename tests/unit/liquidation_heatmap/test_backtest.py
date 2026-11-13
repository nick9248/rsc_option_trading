"""Liquidation heatmap backtest pieces: sweep by hand, statistics, daily prices, CryptoQuant client, day rows. No network."""

from datetime import date, datetime, timedelta, timezone
from typing import Any, cast

import numpy as np
import pytest

from coding.core.analytics.liquidation_heatmap.liquidation_backtest import (
    CANDLES_PER_DAY,
    DayRow,
    daily_price_stats,
    partial_spearman,
    spearman,
    swept_levels_usd,
)
from coding.core.analytics.liquidation_heatmap.liquidation_heatmap_model import STEP, PriceCandle
from coding.service.liquidation_heatmap.cryptoquant_liquidation_client import (
    CryptoQuantError,
    load_daily_liquidations,
    read_token,
)
from scripts.backtest_liquidation_heatmap import format_value, summarise


def test_sweep_counts_long_levels_the_low_reached_and_short_levels_the_high_reached():
    centers = np.array([9_000.0, 9_500.0, 10_500.0, 11_000.0])
    long_btc = np.array([2.0, 3.0, 0.0, 0.0])
    short_btc = np.array([0.0, 0.0, 4.0, 5.0])
    # open 10,000, low 9,400, high 10,800: long 9,500 (3 BTC) is swept, 9,000 is not; short 10,500 (4 BTC) is, 11,000 is not.
    long_usd, short_usd = swept_levels_usd(long_btc, short_btc, centers, 10_000.0, 9_400.0, 10_800.0)
    assert long_usd == pytest.approx(3 * 9_500)
    assert short_usd == pytest.approx(4 * 10_500)
    assert swept_levels_usd(long_btc, short_btc, centers, 10_000.0, 10_000.0, 10_000.0) == (0.0, 0.0)   # flat day


def test_spearman_is_one_for_the_same_order_and_undefined_for_constants():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40])[0] == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1])[0] == pytest.approx(-1.0)
    assert spearman([1, 1, 1], [1, 2, 3]) == (None, None)
    assert spearman([1, 2], [1, 2]) == (None, None)


def test_partial_spearman_with_a_constant_control_is_the_plain_correlation():
    x, y = [1, 2, 3, 4, 5, 6], [2, 1, 4, 3, 6, 5]
    partial, _ = partial_spearman(x, y, [7, 7, 7, 7, 7, 7])
    assert partial == pytest.approx(spearman(x, y)[0])


def test_partial_spearman_is_undefined_when_the_control_explains_everything():
    values = [1, 2, 3, 4, 5, 6]
    assert partial_spearman([3, 1, 4, 2, 6, 5], [2, 1, 4, 3, 6, 5], values)[0] is not None
    assert partial_spearman(values, [3, 1, 4, 2, 6, 5], values) == (None, None)     # x is the control: nothing left of x


def test_partial_spearman_removes_what_the_control_explains():
    # x and y are both the control plus opposite wiggles: raw rank correlation is high, but after removing the
    # control only the wiggles are left and they are negatively related.
    control = [1, 2, 3, 4, 5, 6, 7, 8]
    wiggle = [0.4, -0.4, 0.4, -0.4, 0.4, -0.4, 0.4, -0.4]
    x = [c + w * 3 for c, w in zip(control, wiggle)]
    y = [c - w * 3 for c, w in zip(control, wiggle)]
    raw, _ = spearman(x, y)
    partial, _ = partial_spearman(x, y, control)
    assert raw is not None and partial is not None and raw > 0.5 and partial < -0.5


def candles_for_day(day_start, close=100.0, volume=2.0, count=CANDLES_PER_DAY):
    return [PriceCandle(day_start + STEP * index, high=close + 1, low=close - 1 - (index == 5) * 3, close=close,
                        volume_btc=volume, taker_buy_btc=1.0) for index in range(count)]


def test_daily_price_stats_use_only_complete_days_with_a_known_open():
    day_one = datetime(2026, 11, 1, tzinfo=timezone.utc)
    day_two = day_one + timedelta(days=1)
    day_three = day_two + timedelta(days=1)
    candles = (candles_for_day(day_one) + candles_for_day(day_two, close=110.0)
               + candles_for_day(day_three, count=100))                     # day three is incomplete
    stats = daily_price_stats(candles)
    assert set(stats) == {date(2026, 11, 2)}                                # day one has no candle before it
    day = stats[date(2026, 11, 2)]
    assert day.open_price == 100.0 and day.high == 111.0 and day.low == 110.0 - 1 - 3
    assert day.volume_usd == pytest.approx(288 * 2.0 * 110.0)


def test_summarise_gives_a_perfect_rank_correlation_for_a_perfect_predictor():
    rows = [DayRow(date(2026, 11, 1 + index), predicted_long_usd=float(index + 1), predicted_short_usd=float(10 - index),
                   drop_percent=float(index % 3), rise_percent=float((index + 1) % 3), volume_usd=100.0 + index,
                   previous_volume_usd=100.0, real_long_usd=float(index + 1) * 10, real_short_usd=float(10 - index) * 10)
            for index in range(8)]
    summary = summarise(rows)
    assert summary["long"]["heatmap_rho"] == pytest.approx(1.0)
    assert summary["short"]["heatmap_rho"] == pytest.approx(1.0)
    assert summary["long"]["days"] == 8
    assert format_value(None) == "n/a" and format_value(0.5) == "+0.50"


class Response:
    def __init__(self, status=200, payload=None):
        self.status_code, self.payload = status, payload

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class Session:
    def __init__(self, response):
        self.response, self.calls = response, []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params, headers))
        return self.response


def test_client_parses_days_skips_incomplete_rows_and_sends_the_key_only_as_a_header():
    payload = {"result": {"data": [
        {"date": "2026-11-12", "long_liquidations_usd": 58.6e6, "short_liquidations_usd": 3.5e6},
        {"date": "2026-11-11", "long_liquidations_usd": 9.3e6},                       # incomplete: skipped
    ]}}
    session = Session(Response(payload=payload))
    days = load_daily_liquidations("SECRET", date(2026, 11, 1), date(2026, 11, 12), cast(Any, session))
    assert days == {date(2026, 11, 12): (58.6e6, 3.5e6)}
    url, params, headers = session.calls[0]
    assert headers == {"Authorization": "Bearer SECRET"} and "SECRET" not in str(params)
    assert params["from"] == "20261101" and params["to"] == "20261112" and params["window"] == "day"


@pytest.mark.parametrize("response", [Response(403, {}), Response(200, {"unexpected": 1}), Response(200, ValueError("x"))])
def test_client_errors_never_contain_the_key(response):
    with pytest.raises(CryptoQuantError) as error:
        load_daily_liquidations("SECRET", date(2026, 11, 1), date(2026, 11, 2), cast(Any, Session(response)))
    assert "SECRET" not in str(error.value)


def test_read_token_requires_the_variable(monkeypatch, tmp_path):
    monkeypatch.delenv("crypto_quant_token", raising=False)
    monkeypatch.setattr("coding.service.liquidation_heatmap.cryptoquant_liquidation_client.load_dotenv", lambda **_: None)
    with pytest.raises(CryptoQuantError):
        read_token()
    monkeypatch.setenv("crypto_quant_token", " 'abc' ")
    assert read_token() == "abc"


def test_client_sends_no_date_range_by_default():
    session = Session(Response(payload={"result": {"data": []}}))
    assert load_daily_liquidations("SECRET", session=cast(Any, session)) == {}
    params = session.calls[0][1]
    assert "from" not in params and "to" not in params and params["limit"] == 100000
