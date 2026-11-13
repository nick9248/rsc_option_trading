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

    def __init__(self, events: List[RecordedLiquidation], size: int = 3):
        self.events, self.calls, self.size = sorted(events, key=lambda e: e.event_time, reverse=True), [], size

    def fetch_page(self, earlier_than_ms=None):
        self.calls.append(earlier_than_ms)
        older = [e for e in self.events if earlier_than_ms is None or e.event_time.timestamp() * 1000 < earlier_than_ms]
        return older[:self.size]


class EventRepository:
    def __init__(self):
        self.stored: List[RecordedLiquidation] = []

    def get_latest_liquidation_event_time(self, venue, instrument):
        return max((e.event_time for e in self.stored), default=None)

    def save_liquidation_events(self, events, collected_at):
        self.save_calls = getattr(self, "save_calls", []) + [list(events)]
        new = []
        for e in events:
            if e not in self.stored and e not in new:          # like the table: a duplicate is ignored
                new.append(e)
        self.stored.extend(new)
        return len(new)


def recorder(client, repository, pages=30, overlap=timedelta(0), page_size=3):
    return LiquidationEventRecorder(cast(Any, client), cast(Any, repository), maximum_pages=pages, clock=lambda: NOW,
                                    pause_seconds=0.0, sleep=lambda seconds: None, safety_overlap=overlap,
                                    page_size=page_size)


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
    assert len(client.calls) == 3                                                # the page reaching event 3 (stored) ends it


def test_a_gap_is_warned_when_the_page_limit_is_reached_before_the_stored_events(caplog):
    repository = EventRepository()
    repository.stored.append(make_event(0))
    client = PagedClient([make_event(second) for second in range(1, 40)])
    assert recorder(client, repository, pages=2).record() == 5
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
    # Long pool (9,500 .. 10,000, 6 points). Current snapshot, long levels 10/50/10 at 9,700/9,800/9,900: pool intensities
    # 0, 10, 60, 70, 60, 10. An event at 9,800 has 70: 5 points below, 1 tie: (5 + 0.5) / 6 = 0.9167.
    # Stale snapshot (24 h earlier) with its long level at 9,550: pool intensities 50, 50, 0, 0, 0, 0; the same event has 0:
    # none below, 4 ties: 0.5 * 4 / 6 = 0.3333.
    stale_levels = [[9_550.0, 50.0, 0.0]]
    snapshots = [snapshot(t0 - timedelta(hours=24), stale_levels), snapshot(t0, STRONG)]
    events = [long_event(t0 + timedelta(minutes=30)), long_event(t0 - timedelta(hours=30))]      # the second has no snapshot
    result = score_events(events, snapshots, price_shift=0.0)
    assert len(result.scored) == 1 and result.without_snapshot == 1 and result.outside_pool == 0
    assert result.scored[0].percentile == pytest.approx(5.5 / 6)
    assert result.scored[0].placebo_percentile == pytest.approx(2 / 6)
    summary = summarise_scores(result.scored)
    assert summary["long"]["events"] == 1 and summary["long"]["top_decile_share"] == 1.0
    assert summary["long"]["control_top_decile_share"] == 0.0 and summary["short"]["events"] == 0
    assert summary["long"]["paired_events"] == 1 and summary["long"]["paired_mean_percentile"] == pytest.approx(5.5 / 6)
    assert summary["long"]["control_mean_percentile"] == pytest.approx(2 / 6)


def test_events_outside_the_pool_are_counted_separately_from_missing_snapshots():
    t0 = datetime(2026, 11, 13, 12, 12, tzinfo=timezone.utc)
    result = score_events([long_event(t0 + timedelta(minutes=5), price=10_400.0),      # a long above the reference
                           long_event(t0 + timedelta(hours=5))],                       # no recent snapshot
                          [snapshot(t0, STRONG)], price_shift=0.0)
    assert result.scored == [] and result.outside_pool == 1 and result.without_snapshot == 1


# ---- review fixes ------------------------------------------------------------------------------------------------

class RawPageClient:
    """Pages of a fixed list; each page is a list of events of any instrument, newest first."""

    def __init__(self, pages):
        self.pages, self.cursors = list(pages), []

    def fetch_page(self, earlier_than_ms=None):
        self.cursors.append(earlier_than_ms)
        return self.pages.pop(0) if self.pages else []


def test_a_page_without_the_wanted_instrument_does_not_end_the_run_or_hide_older_events():
    other = [make_event(second, instrument="ETH-USDT-SWAP") for second in (9, 8, 7)]
    wanted = [make_event(second) for second in (3, 2, 1)]
    repository = EventRepository()
    assert recorder(RawPageClient([other, wanted]), repository).record() == 3
    assert len(repository.stored) == 3


def test_the_cursor_is_inclusive_so_events_of_the_same_millisecond_are_read_again():
    first = [make_event(5), make_event(4)]
    client = RawPageClient([first, [make_event(4), make_event(3)], []])
    repository = EventRepository()
    recorder(client, repository).record()
    oldest_ms = int(make_event(4).event_time.timestamp() * 1000)
    assert client.cursors[1] == oldest_ms + 1                                  # +1 ms: the same-millisecond orders come again
    assert len(repository.stored) == 3                                         # the repeated event is stored once


def test_a_page_made_of_one_single_millisecond_still_makes_progress():
    same_ms = [make_event(5), make_event(5)]
    client = RawPageClient([same_ms, same_ms, [make_event(4)], []])
    recorder(client, EventRepository(), page_size=2).record()
    first_ms = int(make_event(5).event_time.timestamp() * 1000)
    assert client.cursors[1] == first_ms + 1 and client.cursors[2] == first_ms    # second page repeated: step strictly past it


def test_the_recorder_pauses_between_pages_but_not_after_the_last():
    sleeps = []
    client = PagedClient([make_event(second) for second in range(7)])
    LiquidationEventRecorder(cast(Any, client), cast(Any, EventRepository()), clock=lambda: NOW,
                             pause_seconds=0.5, sleep=sleeps.append).record()
    assert sleeps and all(value == 0.5 for value in sleeps) and len(sleeps) == len(client.calls) - 1


def test_the_price_of_an_event_is_moved_toward_the_trigger_price_before_scoring():
    # Realistic scale: reference 70,000, so the 0.4 % shift is 280 USD, more than the 100 USD matching window.
    # A long's pool is 66,500 .. 70,000 (36 points), a short's 70,000 .. 73,500 (36 points).
    t0 = datetime(2026, 11, 13, 12, 12, tzinfo=timezone.utc)
    when = t0 + timedelta(minutes=5)
    # A long with its trigger at the strong level 68,000 is reported at its bankruptcy price 67,730 (68,000 / 1.004).
    # Shifted: 67,730 * 1.004 = 68,000.9, intensity 50 (3 pool points have it): (33 below + 0.5 * 3 ties) / 36 = 0.9583.
    # Not shifted: 67,730 is 270 USD from the level, intensity 0, tied with 33 pool points: 0.5 * 33 / 36 = 0.4583.
    long_event_at_bankruptcy = RecordedLiquidation("okx", "BTC-USDT-SWAP", when, "long", 67_730.0, 1.0, 100.0)
    long_snapshot = snapshot(t0, [[68_000.0, 50.0, 0.0]], reference=70_000.0)
    assert score_events([long_event_at_bankruptcy], [long_snapshot]).scored[0].percentile == pytest.approx(34.5 / 36)
    assert score_events([long_event_at_bankruptcy], [long_snapshot], price_shift=0.0).scored[0].percentile == pytest.approx(16.5 / 36)
    # A short is moved the other way: bankruptcy 72,300 * 0.996 = 72,010.8, near the strong short level 72,000.
    short_event = RecordedLiquidation("okx", "BTC-USDT-SWAP", when, "short", 72_300.0, 1.0, 100.0)
    short_snapshot = snapshot(t0, [[72_000.0, 0.0, 50.0]], reference=70_000.0)
    assert score_events([short_event], [short_snapshot]).scored[0].percentile == pytest.approx(34.5 / 36)


def test_the_control_is_compared_on_the_same_events_only():
    t0 = datetime(2026, 11, 13, 12, 12, tzinfo=timezone.utc)
    # Reference of the stale snapshot is far away, so events near the current price fall outside its pool: no control
    # score for them. The paired numbers must then cover only events that have both scores.
    snapshots = [snapshot(t0 - timedelta(hours=24), STRONG, reference=14_000.0), snapshot(t0, STRONG)]
    events = [long_event(t0 + timedelta(minutes=10)), long_event(t0 + timedelta(minutes=20))]
    summary = summarise_scores(score_events(events, snapshots, price_shift=0.0).scored)
    assert summary["long"]["events"] == 2 and summary["long"]["paired_events"] == 0
    assert summary["long"]["paired_mean_percentile"] is None and summary["long"]["control_mean_percentile"] is None


def test_a_failed_page_stores_nothing_and_the_next_run_starts_from_the_same_place():
    class FailsOnSecondPage(PagedClient):
        def fetch_page(self, earlier_than_ms=None):
            if len(self.calls) == 1:
                self.calls.append(earlier_than_ms)
                raise OkxDataError("HTTP 429")
            return super().fetch_page(earlier_than_ms)

    repository = EventRepository()
    events = [make_event(second) for second in range(7)]
    with pytest.raises(OkxDataError):
        recorder(FailsOnSecondPage(events), repository).record()
    assert repository.stored == []                                             # not even the first page was kept
    assert recorder(PagedClient(events), repository).record() == 7             # nothing is lost: the next run reads it all


def test_a_run_saves_once_oldest_first():
    repository = EventRepository()
    recorder(PagedClient([make_event(second) for second in range(7)]), repository).record()
    assert len(repository.save_calls) == 1
    times = [event.event_time for event in repository.save_calls[0]]
    assert times == sorted(times)


def test_the_liquidation_recorder_does_not_depend_on_the_taker_flow_switch(monkeypatch):
    class Repo:
        def missing_tables(self, names):
            return []

    monkeypatch.setattr(job_module, "load_dotenv", lambda **_: None)
    monkeypatch.setenv("LIQUIDATION_RECORDER_ENABLED", "1")
    monkeypatch.setenv("TAKER_FLOW_ENABLED", "garbage")                        # an unrelated bad setting
    monkeypatch.setenv("BINANCE_FUTURES_BASE_URL", "http://65.20.101.75:8082")
    assert start_liquidation_recorder_job(cast(Any, Repo())) is not None
    monkeypatch.setenv("BINANCE_FUTURES_BASE_URL", "http://65.20.101.75:8082/fapi")     # its own URL must still be valid
    assert start_liquidation_recorder_job(cast(Any, Repo())) is None


def test_the_safety_overlap_picks_up_an_order_that_appeared_late():
    repository = EventRepository()
    recorder(PagedClient([make_event(second) for second in range(0, 40, 10)], size=2), repository, page_size=2).record()
    late = make_event(25)                                       # appears later, with a time BEFORE the newest stored (30 s)
    served = [make_event(second) for second in range(0, 40, 10)] + [late, make_event(40)]
    # Pages of 2, newest first: [40, 30], [25, 20], [10, 0]. Without overlap the run stops at the first page (it reaches the
    # stored 30 s event) and the late order is never read.
    assert recorder(PagedClient(served, size=2), repository, overlap=timedelta(0), page_size=2).record() == 1
    assert late not in repository.stored
    # With a 30 s overlap it reads on to the page that reaches 0 s and finds the late order.
    assert recorder(PagedClient(served, size=2), repository, overlap=timedelta(seconds=30), page_size=2).record() == 1
    assert late in repository.stored


def test_a_short_page_that_repeats_ends_the_run_without_a_warning(caplog):
    client = RawPageClient([[make_event(5), make_event(4), make_event(3)], [make_event(3), make_event(2)],
                            [make_event(2)], [make_event(2)]])
    recorder(client, EventRepository()).record()
    assert "shares the millisecond" not in caplog.text


def test_a_full_page_of_one_millisecond_is_warned(caplog):
    same_ms = [make_event(5), make_event(5), make_event(5)]                      # a full page (page_size 3), all one millisecond
    client = RawPageClient([same_ms, same_ms, [make_event(4)], []])
    recorder(client, EventRepository()).record()
    assert "shares the millisecond" in caplog.text


def test_the_snapshot_is_stamped_after_it_was_computed():
    end = datetime(2026, 11, 13, 18, 25, tzinfo=timezone.utc)
    readings = iter([NOW, NOW + timedelta(seconds=7)])                           # before / after the computation
    repository = SnapshotRepository()
    service = HeatmapSnapshotService(cast(Any, CandleLoader(candle_series(end))), cast(Any, repository),
                                     clock=lambda: next(readings))
    assert service.snapshot() is True
    assert repository.saved[0]["computed_at"] == NOW + timedelta(seconds=7)
