"""Liquidation recorder: OKX client, event recorder, snapshot service, job and scoring. No network, no database."""

from datetime import datetime, timedelta, timezone
from typing import Any, List, cast

import pytest

from coding.core.analytics.liquidation_heatmap.level_evaluation import (
    score_events,
    snapshot_before,
    summarise_scores,
)
from coding.core.analytics.liquidation_heatmap.level_snapshot import LevelSnapshot, RecordedLiquidation
from coding.core.analytics.liquidation_heatmap.liquidation_heatmap_model import STEP, PriceCandle
from coding.service.liquidation_heatmap import liquidation_recorder_job as job_module
from coding.service.liquidation_heatmap.heatmap_snapshot_service import HeatmapSnapshotService
from coding.service.liquidation_heatmap.liquidation_event_recorder import LiquidationEventRecorder
from coding.service.liquidation_heatmap.liquidation_recorder_job import LiquidationRecorderJob, start_liquidation_recorder_job
from coding.service.liquidation_heatmap.okx_liquidation_client import OkxDataError, OkxLiquidationClient

NOW = datetime(2026, 11, 13, 18, 25, 30, tzinfo=timezone.utc)


# ---- OKX client ----------------------------------------------------------------------------------------------

class Response:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class OkxSession:
    def __init__(self, liquidations, instruments=None):
        self.liquidations = liquidations
        self.instruments = instruments if instruments is not None else {"code": "0", "data": [
            {"instId": "BTC-USDT-SWAP", "ctVal": "0.01", "ctValCcy": "BTC"}]}
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return Response(self.instruments if "instruments" in url else self.liquidations)


def okx_page(*details, instrument="BTC-USDT-SWAP"):
    return {"code": "0", "data": [{"instId": instrument, "instType": "SWAP", "uly": "BTC-USDT", "details": list(details)}]}


def detail(ts_ms, side="sell", size="8", price="68868.4", pos_side="long"):
    return {"ts": str(ts_ms), "side": side, "sz": size, "bkPx": price, "posSide": pos_side, "bkLoss": "0", "time": str(ts_ms)}


def test_client_maps_side_converts_contracts_to_btc_and_orders_newest_first():
    session = OkxSession(okx_page(detail(1_000, side="buy", size="8", pos_side="short"),
                                  detail(2_000, side="sell", size="250", price="68000", pos_side="net")))
    events = OkxLiquidationClient(session=cast(Any, session)).fetch_page()
    assert [event.event_time.timestamp() for event in events] == [2.0, 1.0]
    assert (events[0].liquidated_side, events[0].price, events[0].size_btc, events[0].raw_size) == ("long", 68000.0, 2.5, 250.0)
    assert (events[1].liquidated_side, events[1].size_btc) == ("short", pytest.approx(0.08))
    assert events[0].usd == pytest.approx(2.5 * 68000)


def test_client_sends_the_cursor_as_after_and_skips_bad_rows(caplog):
    session = OkxSession(okx_page(detail(5_000), {"ts": "6000", "side": "sell"}, detail(7_000, size="0"),
                                  detail(8_000, side="hold")))
    events = OkxLiquidationClient(session=cast(Any, session)).fetch_page(earlier_than_ms=9_000)
    assert len(events) == 1 and caplog.text.count("skipped a row") == 3
    liquidation_call = [params for url, params in session.calls if "liquidation-orders" in url][0]
    assert liquidation_call["after"] == "9000" and liquidation_call["state"] == "filled"


@pytest.mark.parametrize("session", [OkxSession(Response({}, 451).payload), OkxSession({"code": "50011", "data": []}),
                                     OkxSession({"code": "0", "data": "x"})])
def test_client_raises_on_an_error_answer(session):
    with pytest.raises(OkxDataError):
        OkxLiquidationClient(session=cast(Any, session)).fetch_page()


def test_client_refuses_a_contract_that_is_not_in_btc():
    session = OkxSession(okx_page(detail(1_000)), instruments={"code": "0", "data": [
        {"instId": "BTC-USDT-SWAP", "ctVal": "100", "ctValCcy": "USD"}]})
    with pytest.raises(OkxDataError):
        OkxLiquidationClient(session=cast(Any, session)).fetch_page()


# ---- event recorder ------------------------------------------------------------------------------------------

def make_event(second, instrument="BTC-USDT-SWAP"):
    return RecordedLiquidation("okx", instrument, datetime(2026, 11, 13, 18, 0, tzinfo=timezone.utc) + timedelta(seconds=second),
                               "long", 68000.0, 0.1, 10.0)


class PagedClient:
    """Serves the events newest first, three per page, older than the cursor."""

    def __init__(self, events: List[RecordedLiquidation]):
        self.events, self.calls = sorted(events, key=lambda e: e.event_time, reverse=True), []

    def fetch_page(self, earlier_than_ms=None):
        self.calls.append(earlier_than_ms)
        older = [e for e in self.events if earlier_than_ms is None or e.event_time.timestamp() * 1000 < earlier_than_ms]
        return older[:3]


class EventRepository:
    def __init__(self):
        self.stored: List[RecordedLiquidation] = []

    def get_latest_liquidation_event_time(self, venue, instrument):
        return max((e.event_time for e in self.stored), default=None)

    def save_liquidation_events(self, events, collected_at):
        new = [e for e in events if e not in self.stored]
        self.stored.extend(new)
        return len(new)


def recorder(client, repository, pages=30):
    return LiquidationEventRecorder(cast(Any, client), cast(Any, repository), maximum_pages=pages, clock=lambda: NOW)


def test_first_run_reads_every_page_until_the_source_has_nothing_older():
    repository = EventRepository()
    assert recorder(PagedClient([make_event(second) for second in range(7)]), repository).record() == 7
    assert len(repository.stored) == 7


def test_a_later_run_stops_when_a_page_reaches_stored_events_and_stores_only_the_new_ones():
    repository = EventRepository()
    recorder(PagedClient([make_event(second) for second in range(4)]), repository).record()
    client = PagedClient([make_event(second) for second in range(9)])          # 5 new events (4..8) on top of the 4 stored
    assert recorder(client, repository).record() == 5
    assert len(repository.stored) == 9
    assert len(client.calls) == 2                                                # one page of 3 and one of 3 reaches 3 (stored)


def test_a_gap_is_warned_when_the_page_limit_is_reached_before_the_stored_events(caplog):
    repository = EventRepository()
    repository.stored.append(make_event(0))
    client = PagedClient([make_event(second) for second in range(1, 40)])
    assert recorder(client, repository, pages=2).record() == 6
    assert "may be missing" in caplog.text


def test_events_of_other_instruments_are_ignored():
    repository = EventRepository()
    assert recorder(PagedClient([make_event(1), make_event(2, instrument="ETH-USDT-SWAP")]), repository).record() == 1


# ---- snapshot service ----------------------------------------------------------------------------------------

class CandleLoader:
    def __init__(self, candles):
        self.candles, self.calls = candles, []

    def load_candles(self, symbol, start, end):
        self.calls.append((symbol, start, end))
        return [c for c in self.candles if start <= c.open_time < end]


class SnapshotRepository:
    def __init__(self):
        self.saved: List[dict] = []

    def save_liquidation_heatmap_snapshot(self, **kwargs):
        self.saved.append(kwargs)
        return True


def candle_series(end, days=12, price=70_000.0, drop_last=0):
    count = int(timedelta(days=days) / STEP)
    start = end - STEP * count
    return [PriceCandle(start + STEP * i, high=price + 20, low=price - 20, close=price, volume_btc=10.0, taker_buy_btc=6.0)
            for i in range(count - drop_last)]


def test_snapshot_stores_the_levels_around_the_last_close_at_the_candle_end():
    end = datetime(2026, 11, 13, 18, 25, tzinfo=timezone.utc)
    repository = SnapshotRepository()
    service = HeatmapSnapshotService(cast(Any, CandleLoader(candle_series(end))), cast(Any, repository), clock=lambda: NOW)
    assert service.snapshot() is True
    saved = repository.saved[0]
    assert saved["symbol"] == "BTCUSDT" and saved["as_of_utc"] == end and saved["computed_at"] == NOW
    assert saved["reference_price"] == 70_000.0 and saved["bin_size_usd"] == 100.0
    assert saved["settings"]["creation_source"] == "volume" and saved["settings"]["position_half_life_hours"] == 48.0
    prices = [level[0] for level in saved["levels"]]
    assert prices and all(abs(price - 70_000.0) <= 7_000 for price in prices)
    assert any(level[1] > 0 for level in saved["levels"]) and any(level[2] > 0 for level in saved["levels"])


def test_snapshot_is_not_stored_when_the_data_is_too_thin_or_too_old(caplog):
    end = datetime(2026, 11, 13, 18, 25, tzinfo=timezone.utc)
    repository = SnapshotRepository()
    thin = HeatmapSnapshotService(cast(Any, CandleLoader(candle_series(end, drop_last=400))), cast(Any, repository), clock=lambda: NOW)
    assert thin.snapshot() is False and "candles, no snapshot stored" in caplog.text
    old = HeatmapSnapshotService(cast(Any, CandleLoader(candle_series(end - timedelta(hours=2)))), cast(Any, repository),
                                 clock=lambda: NOW)
    assert old.snapshot() is False and "newest candle" in caplog.text
    assert repository.saved == []


# ---- job -------------------------------------------------------------------------------------------------------

class Scheduler:
    def __init__(self):
        self.jobs = {}

    def add_job(self, **kwargs):
        self.jobs[kwargs["id"]] = kwargs


def test_job_registers_both_jobs_and_never_raises(caplog):
    class Boom:
        def record(self):
            raise RuntimeError("x")

        def snapshot(self):
            raise RuntimeError("y")

    job = LiquidationRecorderJob(cast(Any, Boom()), cast(Any, Boom()))
    scheduler = Scheduler()
    job.register(scheduler)
    assert set(scheduler.jobs) == {"liquidation_events", "liquidation_snapshot"}
    job.run_events()
    job.run_snapshot()
    assert "event recording failed" in caplog.text and "snapshot failed" in caplog.text


def test_start_follows_the_switch_and_the_tables(monkeypatch):
    class Repo:
        def __init__(self, missing):
            self.missing = missing

        def missing_tables(self, names):
            return list(self.missing)

    monkeypatch.setattr(job_module, "load_dotenv", lambda **_: None)
    monkeypatch.delenv("LIQUIDATION_RECORDER_ENABLED", raising=False)
    assert start_liquidation_recorder_job(cast(Any, Repo([]))) is None
    monkeypatch.setenv("LIQUIDATION_RECORDER_ENABLED", "1")
    assert start_liquidation_recorder_job(cast(Any, Repo(["liquidation_events"]))) is None       # migration 034 missing
    assert start_liquidation_recorder_job(cast(Any, Repo([]))) is not None
    monkeypatch.setenv("LIQUIDATION_RECORDER_ENABLED", "maybe")
    assert start_liquidation_recorder_job(cast(Any, Repo([]))) is None                          # bad value never raises


# ---- scoring ---------------------------------------------------------------------------------------------------

def snapshot(computed_at, levels, reference=10_000.0):
    return LevelSnapshot("BTCUSDT", computed_at, computed_at, reference, 100.0, levels)


STRONG = [[9_700.0, 10.0, 0.0], [9_800.0, 50.0, 0.0], [9_900.0, 10.0, 0.0]]        # long peak at 9,800


def long_event(moment, price=9_800.0, usd_btc=1.0):
    return RecordedLiquidation("okx", "BTC-USDT-SWAP", moment, "long", price, usd_btc, 100.0)


def test_snapshot_before_uses_only_snapshots_computed_before_the_moment_and_recent_enough():
    t0 = datetime(2026, 11, 13, 12, 12, tzinfo=timezone.utc)
    snapshots = [snapshot(t0, STRONG), snapshot(t0 + timedelta(hours=1), STRONG)]
    assert snapshot_before(snapshots, t0 - timedelta(minutes=1)) is None
    assert snapshot_before(snapshots, t0 + timedelta(minutes=59)) is snapshots[0]
    assert snapshot_before(snapshots, t0 + timedelta(hours=1)) is snapshots[1]
    assert snapshot_before(snapshots, t0 + timedelta(hours=3, minutes=1)) is None          # newest is older than 2 h


def test_scoring_by_hand_with_a_stale_control():
    t0 = datetime(2026, 11, 13, 12, 12, tzinfo=timezone.utc)
    # Current snapshot: long levels 10/50/10 at 9,700/9,800/9,900 -> pool intensities 60, 70, 60 and eight zeros.
    # An event at 9,800 has intensity 70: 10 pool points below, 1 tie: (10 + 0.5) / 11 = 0.9545.
    # Stale snapshot (24 h earlier): the strong levels were elsewhere (10,300), so the same event has intensity 0:
    # 0 below, 8 ties: 0.5 * 8 / 11 = 0.3636.
    stale_levels = [[10_300.0, 50.0, 0.0]]
    snapshots = [snapshot(t0 - timedelta(hours=24), stale_levels), snapshot(t0, STRONG)]
    events = [long_event(t0 + timedelta(minutes=30)), long_event(t0 - timedelta(hours=30))]      # the second has no snapshot
    scored = score_events(events, snapshots)
    assert len(scored) == 1
    assert scored[0].percentile == pytest.approx(10.5 / 11) and scored[0].placebo_percentile == pytest.approx(4 / 11)
    summary = summarise_scores(scored)
    assert summary["long"]["events"] == 1 and summary["long"]["top_decile_share"] == 1.0
    assert summary["long"]["control_top_decile_share"] == 0.0 and summary["short"]["events"] == 0
    assert summary["all"]["mean_percentile"] == pytest.approx(10.5 / 11)
