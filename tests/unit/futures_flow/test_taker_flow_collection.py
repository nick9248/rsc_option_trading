"""Binance client parsing, collection service (backfill and gaps) and settings. No network, no database."""

from datetime import datetime, timedelta, timezone
from typing import Any, List, cast

import pytest

from coding.core.analytics.futures_flow.taker_flow import TakerFlowHour
from coding.service.futures_flow.binance_taker_flow_client import BinanceTakerFlowClient, TakerFlowDataError
from coding.service.futures_flow.taker_flow_collection_service import TakerFlowCollectionService
from coding.service.futures_flow.taker_flow_settings import (
    DEFAULT_BINANCE_FUTURES_BASE_URL,
    TakerFlowSettings,
    TakerFlowSettingsError,
)

NOW = datetime(2026, 11, 13, 14, 25, tzinfo=timezone.utc)


def kline(hour: datetime, total: float, taker_buy: float, trades: int = 100):
    """A Binance kline row with only the used fields filled (index 0, 7, 8, 10)."""
    row = [0] * 12
    row[0] = int(hour.timestamp() * 1000)
    row[7] = str(total)
    row[8] = trades
    row[10] = str(taker_buy)
    return row


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeSession:
    def __init__(self, response: FakeResponse):
        self.response = response
        self.calls: List[dict] = []

    def get(self, url, params=None, timeout=None):
        self.calls.append({"url": url, "params": params, "timeout": timeout})
        return self.response


def test_client_returns_closed_hours_only_and_derives_sell_volume():
    hour = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
    rows = [kline(hour, 1000.0, 400.0), kline(hour + timedelta(hours=1), 500.0, 250.0),
            kline(hour + timedelta(hours=2), 300.0, 100.0)]            # 14:00 is still running at NOW
    session = FakeSession(FakeResponse(rows))
    hours = BinanceTakerFlowClient("http://relay:8082/", session).fetch_closed_hours("BTCUSDT", hour, NOW)
    assert [h.hour_utc.hour for h in hours] == [12, 13]
    assert hours[0].taker_buy_usd == 400.0 and hours[0].taker_sell_usd == 600.0 and hours[0].trade_count == 100
    call = session.calls[0]
    assert call["url"] == "http://relay:8082/fapi/v1/klines"
    assert call["params"]["symbol"] == "BTCUSDT" and call["params"]["interval"] == "1h"
    assert call["params"]["startTime"] == int(hour.timestamp() * 1000)


def test_client_skips_implausible_rows_instead_of_inventing_values(caplog):
    hour = datetime(2026, 11, 13, 10, tzinfo=timezone.utc)
    rows = [kline(hour, 100.0, 150.0),                          # taker buy above total
            kline(hour + timedelta(hours=1), -5.0, 0.0),         # negative volume
            ["bad"],                                             # too short
            kline(hour + timedelta(hours=2), 100.0, 60.0)]
    hours = BinanceTakerFlowClient("http://x", FakeSession(FakeResponse(rows))).fetch_closed_hours("BTCUSDT", hour, NOW)
    assert [h.hour_utc.hour for h in hours] == [12]
    assert caplog.text.count("skipped") == 3


@pytest.mark.parametrize("response", [FakeResponse({"msg": "blocked"}, 451), FakeResponse({"code": -1}),
                                      FakeResponse(ValueError("no json"))])
def test_client_raises_on_a_block_or_unusable_answer(response):
    with pytest.raises(TakerFlowDataError):
        BinanceTakerFlowClient("http://x", FakeSession(response)).fetch_closed_hours("BTCUSDT", NOW, NOW)


class FakeClient:
    def __init__(self, hours_by_symbol=None, fail_symbols=()):
        self.hours_by_symbol = hours_by_symbol or {}
        self.fail_symbols = set(fail_symbols)
        self.requests = []

    def fetch_closed_hours(self, symbol, start_hour_utc, now):
        self.requests.append((symbol, start_hour_utc))
        if symbol in self.fail_symbols:
            raise TakerFlowDataError("blocked")
        return [h for h in self.hours_by_symbol.get(symbol, []) if h.hour_utc >= start_hour_utc]


class FakeRepository:
    def __init__(self, stored=None):
        self.stored = list(stored or [])

    def get_taker_flow_hours(self, symbol, first_hour, last_hour):
        return [h for h in self.stored if h.symbol == symbol and first_hour <= h.hour_utc <= last_hour]

    def save_taker_flow_hours(self, hours, collected_at):
        existing = {(h.symbol, h.hour_utc) for h in self.stored}
        new = [h for h in hours if (h.symbol, h.hour_utc) not in existing]
        self.stored.extend(new)
        return len(new)


def hours_range(symbol, last_hour, count):
    return [TakerFlowHour(symbol, last_hour - timedelta(hours=offset), 10.0, 20.0) for offset in range(count)][::-1]


def test_first_run_backfills_the_window_and_a_second_run_stores_nothing():
    last_closed = datetime(2026, 11, 13, 13, tzinfo=timezone.utc)
    client = FakeClient({"BTCUSDT": hours_range("BTCUSDT", last_closed, 24 * 40)})
    repository = FakeRepository()
    service = TakerFlowCollectionService(client, repository, symbols=["BTCUSDT"], backfill_days=30, clock=lambda: NOW)
    assert service.collect() == {"BTCUSDT": 24 * 30}
    assert min(h.hour_utc for h in repository.stored) == last_closed - timedelta(hours=24 * 30 - 1)
    assert service.collect() == {"BTCUSDT": 0}
    assert len(client.requests) == 1                      # nothing missing -> no request


def test_a_missed_hour_is_repaired_by_the_next_run():
    last_closed = datetime(2026, 11, 13, 13, tzinfo=timezone.utc)
    all_hours = hours_range("BTCUSDT", last_closed, 24 * 30)
    repository = FakeRepository([h for h in all_hours if h.hour_utc != last_closed - timedelta(hours=5)])
    client = FakeClient({"BTCUSDT": all_hours})
    service = TakerFlowCollectionService(client, repository, symbols=["BTCUSDT"], backfill_days=30, clock=lambda: NOW)
    assert service.collect() == {"BTCUSDT": 1}
    assert client.requests[0][1] == last_closed - timedelta(hours=5)


def test_one_failing_symbol_does_not_stop_the_other(caplog):
    last_closed = datetime(2026, 11, 13, 13, tzinfo=timezone.utc)
    client = FakeClient({"ETHUSDT": hours_range("ETHUSDT", last_closed, 24 * 30)}, fail_symbols=["BTCUSDT"])
    service = TakerFlowCollectionService(client, FakeRepository(), symbols=["BTCUSDT", "ETHUSDT"],
                                         backfill_days=30, clock=lambda: NOW)
    assert service.collect() == {"BTCUSDT": None, "ETHUSDT": 24 * 30}
    assert "BTCUSDT: collection failed" in caplog.text


def test_hours_the_source_does_not_have_are_warned_and_not_stored(caplog):
    last_closed = datetime(2026, 11, 13, 13, tzinfo=timezone.utc)
    available = [h for h in hours_range("BTCUSDT", last_closed, 24 * 30) if h.hour_utc != last_closed]
    service = TakerFlowCollectionService(FakeClient({"BTCUSDT": available}), FakeRepository(),
                                         symbols=["BTCUSDT"], backfill_days=30, clock=lambda: NOW)
    assert service.collect() == {"BTCUSDT": 24 * 30 - 1}
    assert "1 hour(s) not available from the source" in caplog.text


def test_settings_defaults_and_validation():
    assert TakerFlowSettings.from_environment({}) == TakerFlowSettings(False, DEFAULT_BINANCE_FUTURES_BASE_URL)
    on = TakerFlowSettings.from_environment({"TAKER_FLOW_ENABLED": "1",
                                             "BINANCE_FUTURES_BASE_URL": "http://65.20.101.75:8082/"})
    assert on.enabled and on.base_url == "http://65.20.101.75:8082"
    for bad in ({"TAKER_FLOW_ENABLED": "maybe"}, {"BINANCE_FUTURES_BASE_URL": "ftp://x"},
                {"BINANCE_FUTURES_BASE_URL": "http://x/fapi"}, {"BINANCE_FUTURES_BASE_URL": "nonsense"}):
        with pytest.raises(TakerFlowSettingsError):
            TakerFlowSettings.from_environment(bad)


def test_client_skips_rows_that_are_dicts_instead_of_crashing(caplog):
    rows = [{"openTime": 1}, kline(datetime(2026, 11, 13, 12, tzinfo=timezone.utc), 100.0, 60.0)]
    hours = BinanceTakerFlowClient("http://x", cast(Any, FakeSession(FakeResponse(rows)))).fetch_closed_hours(
        "BTCUSDT", datetime(2026, 11, 13, 12, tzinfo=timezone.utc), NOW)
    assert len(hours) == 1 and "skipped a malformed kline" in caplog.text


@pytest.mark.parametrize("bad_value", ["NaN", "Infinity", "-Infinity"])
def test_client_skips_non_finite_volumes(bad_value, caplog):
    hour = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)
    row = kline(hour, 100.0, 60.0)
    row[7] = bad_value
    hours = BinanceTakerFlowClient("http://x", cast(Any, FakeSession(FakeResponse([row])))).fetch_closed_hours(
        "BTCUSDT", hour, NOW)
    assert hours == [] and "implausible kline" in caplog.text
