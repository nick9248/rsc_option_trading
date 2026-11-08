"""
MenthorqMonitorService with an in-memory repository and a fake input loader
(no database): record building on a hand-computable chain, the daily-close
instants, the not-computed path, storage idempotence and reference lookup.
"""

import math
from datetime import date, datetime, timedelta, timezone

import pytest

from coding.core.analytics.menthorq_monitor.level_record import MODE_DAILY_CLOSE, MODE_LIVE
from coding.service.on_chain.menthorq_monitor.menthorq_monitor_service import (
    MenthorqMonitorService,
    daily_close_as_of,
    live_as_of,
)
from tests.fakes.fake_menthorq_monitor import (
    BASE_TIME,
    FAKE_CHAIN_SPOT as SPOT,
    FakeInputLoader,
    FakeMonitorRepository,
    make_record,
    make_record_without_levels,
)

AS_OF = BASE_TIME   # 2026-11-06 09:10 UTC


def _service(repository=None, loader=None):
    return MenthorqMonitorService(repository=repository or FakeMonitorRepository(),
                                  input_loader=loader or FakeInputLoader(),
                                  clock=lambda: AS_OF + timedelta(seconds=3))


def _hand_gamma(strike: float) -> float:
    """Black-Scholes gamma, r = 0, at spot 100,000, IV 0.5, to 27NOV26 08:00 UTC."""
    years = (datetime(2026, 11, 27, 8, tzinfo=timezone.utc) - AS_OF).total_seconds() / (365 * 86400)
    root = 0.5 * math.sqrt(years)
    d1 = (math.log(SPOT / strike) + 0.5 * 0.25 * years) / root
    return math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi) / (SPOT * root)


def test_live_record_levels_total_and_regime_from_a_hand_computable_chain():
    record = _service().compute_live_record(AS_OF)

    # Net GEX per strike = +/- gamma * OI * S^2 * 0.01 (calls +, puts -):
    # 110,000 call: gamma 2.540e-5 -> +254,0xx; 90,000 put: gamma 2.142e-5 -> -214,1xx.
    call_gex = _hand_gamma(110000.0) * 100.0 * SPOT * SPOT * 0.01
    put_gex = _hand_gamma(90000.0) * 100.0 * SPOT * SPOT * 0.01
    assert call_gex == pytest.approx(254000, rel=0.01)
    assert put_gex == pytest.approx(214000, rel=0.01)
    assert record.total_net_gex == pytest.approx(call_gex - put_gex, rel=1e-9)
    assert record.call_resistance == 110000.0          # only positive net GEX strike
    assert record.put_support == 90000.0               # only negative net GEX strike
    # cumulative from the lowest strike: 90,000 -> -214k, 110,000 -> +40k: minimum at 90,000
    assert record.hvl == 90000.0
    assert record.gamma_regime == "positive"           # 100,000 > 90,000
    assert record.mode == MODE_LIVE
    assert record.expirations == ["27NOV26"]
    assert record.panel_levels["first_expiration"]["expiration"] == "27NOV26"
    assert record.panel_levels["first_expiration"]["call_resistance"] == 110000.0
    assert record.panel_levels["next_expiration"]["expiration"] is None
    assert "panel next_expiration: no expiry with computed GEX" in record.not_computed_reasons
    assert record.snapshot_captured_at_utc == AS_OF - timedelta(minutes=21)


def test_live_as_of_truncates_to_the_minute_and_rejects_naive():
    assert live_as_of(datetime(2026, 11, 6, 9, 10, 42, 5, tzinfo=timezone.utc)) == AS_OF
    with pytest.raises(ValueError):
        live_as_of(datetime(2026, 11, 6, 9, 10))


def test_daily_close_uses_2359_spot_and_open_interest_from_0000_same_date():
    loader = FakeInputLoader()
    record = _service(loader=loader).compute_daily_close_record(date(2026, 11, 6))
    assert loader.calls == [(datetime(2026, 11, 6, 23, 59, tzinfo=timezone.utc),
                             datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc))]
    assert record.mode == MODE_DAILY_CLOSE
    assert record.as_of_utc == daily_close_as_of(date(2026, 11, 6))


def test_missing_inputs_give_a_stored_not_computed_record():
    repository = FakeMonitorRepository()
    record = _service(repository, FakeInputLoader(available=False)).run_live(AS_OF)
    assert record.spot is None and record.hvl is None and record.total_net_gex is None
    assert record.not_computed_reasons[0].startswith("inputs not available: No BTC")
    assert record.record_id == 1
    # a not-computed record is never used as a comparison reference
    later = make_record(as_of=AS_OF + timedelta(hours=1))
    assert _service(repository).find_reference(later, "previous") is None


def _daily_close(utc_date):
    return make_record(as_of=daily_close_as_of(utc_date), mode=MODE_DAILY_CLOSE)


def test_daily_close_is_computed_once():
    # Every earlier day of the 7-day window is stored; only yesterday (6 Nov) is missing.
    repository = FakeMonitorRepository([_daily_close(date(2026, 10, 31) + timedelta(days=offset))
                                        for offset in range(6)])
    service = _service(repository)
    now = datetime(2026, 11, 7, 0, 10, tzinfo=timezone.utc)
    first = service.run_daily_close_if_missing(now)
    assert [record.as_of_utc for record in first] == [datetime(2026, 11, 6, 23, 59, tzinfo=timezone.utc)]
    assert service.run_daily_close_if_missing(now + timedelta(hours=1)) == []


def test_a_three_day_gap_is_backfilled_oldest_first_with_point_in_time_inputs(caplog):
    """now = Mon 9 Nov 05:10 UTC. Window = the 7 ended days 2 Nov .. 8 Nov. Stored: 2..5 Nov;
    missing: 6, 7, 8 Nov. Each uses spot/IV at its own 23:59 and OI at its own 00:00; today
    (9 Nov, not ended) is never computed."""
    stored_days = [date(2026, 11, 2) + timedelta(days=offset) for offset in range(4)]
    repository = FakeMonitorRepository([_daily_close(day) for day in stored_days])
    loader = FakeInputLoader()
    service = _service(repository, loader)
    now = datetime(2026, 11, 9, 5, 10, tzinfo=timezone.utc)

    with caplog.at_level("INFO"):
        new_records = service.run_daily_close_if_missing(now)

    missing = [date(2026, 11, 6), date(2026, 11, 7), date(2026, 11, 8)]
    assert [record.as_of_utc for record in new_records] == [daily_close_as_of(day) for day in missing]
    assert loader.calls == [
        (datetime(2026, 11, 6, 23, 59, tzinfo=timezone.utc), datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc)),
        (datetime(2026, 11, 7, 23, 59, tzinfo=timezone.utc), datetime(2026, 11, 7, 0, 0, tzinfo=timezone.utc)),
        (datetime(2026, 11, 8, 23, 59, tzinfo=timezone.utc), datetime(2026, 11, 8, 0, 0, tzinfo=timezone.utc)),
    ]
    assert all(as_of < now for as_of, _ in loader.calls)
    assert "MenthorQ daily-close backfill: 3 computed (2026-11-06, 2026-11-07, 2026-11-08), 0 failed" in caplog.text
    assert service.run_daily_close_if_missing(now) == []


def test_one_failing_day_does_not_stop_the_other_days(caplog, monkeypatch):
    repository = FakeMonitorRepository([_daily_close(date(2026, 11, 2) + timedelta(days=offset))
                                        for offset in range(4)])
    loader = FakeInputLoader()
    real_load_inputs = loader.load_inputs

    def fail_on_7_november(currency, as_of, open_interest_as_of=None):
        if as_of.date() == date(2026, 11, 7):
            raise RuntimeError("database hiccup")
        return real_load_inputs(currency, as_of, open_interest_as_of)

    monkeypatch.setattr(loader, "load_inputs", fail_on_7_november)
    service = _service(repository, loader)
    with caplog.at_level("INFO"):
        new_records = service.run_daily_close_if_missing(datetime(2026, 11, 9, 5, 10, tzinfo=timezone.utc))

    assert [record.as_of_utc.date() for record in new_records] == [date(2026, 11, 6), date(2026, 11, 8)]
    assert "2 computed (2026-11-06, 2026-11-08), 1 failed (2026-11-07)" in caplog.text


def test_a_failure_of_yesterday_is_raised_after_the_other_days(monkeypatch):
    """now = Mon 9 Nov 05:10 UTC; yesterday (8 Nov) raises: 2..7 Nov are still computed, then
    the 8 Nov error is raised so the hourly job can report it."""
    repository = FakeMonitorRepository()
    loader = FakeInputLoader()
    real_load_inputs = loader.load_inputs

    def fail_on_8_november(currency, as_of, open_interest_as_of=None):
        if as_of.date() == date(2026, 11, 8):
            raise RuntimeError("database hiccup")
        return real_load_inputs(currency, as_of, open_interest_as_of)

    monkeypatch.setattr(loader, "load_inputs", fail_on_8_november)
    service = _service(repository, loader)
    with pytest.raises(RuntimeError, match="database hiccup"):
        service.run_daily_close_if_missing(datetime(2026, 11, 9, 5, 10, tzinfo=timezone.utc))
    assert sorted(record.as_of_utc.date() for record in repository.records) == [
        date(2026, 11, 2) + timedelta(days=offset) for offset in range(6)]


def test_a_stored_not_computed_daily_close_is_retried_and_replaced():
    """6 Nov was stored NOT computed (snapshots synced late); 31 Oct..5 Nov are computed.
    At 7 Nov 00:10 only 6 Nov is recomputed, and the computed record replaces the row."""
    computed_days = [_daily_close(date(2026, 10, 31) + timedelta(days=offset)) for offset in range(6)]
    not_computed = make_record(as_of=daily_close_as_of(date(2026, 11, 6)), mode=MODE_DAILY_CLOSE,
                               spot=None, hvl=None, call_resistance=None, put_support=None,
                               total_net_gex=None, snapshot_captured_at_utc=None,
                               not_computed_reasons=["inputs not available"])
    repository = FakeMonitorRepository(computed_days + [not_computed])
    loader = FakeInputLoader()
    service = _service(repository, loader)
    now = datetime(2026, 11, 7, 0, 10, tzinfo=timezone.utc)

    retried = service.run_daily_close_if_missing(now)

    assert loader.calls == [(datetime(2026, 11, 6, 23, 59, tzinfo=timezone.utc),
                             datetime(2026, 11, 6, 0, 0, tzinfo=timezone.utc))]
    assert [record.spot for record in retried] == [SPOT]
    assert retried[0].record_id == 7                       # the 7th row, replaced in place
    assert len(repository.records) == 7 and repository.records[6].spot == SPOT
    assert service.run_daily_close_if_missing(now + timedelta(hours=1)) == []   # computed now: done


def test_a_not_computed_retry_leaves_the_not_computed_row_unchanged():
    not_computed = make_record_without_levels(as_of=daily_close_as_of(date(2026, 11, 6)),
                                              mode=MODE_DAILY_CLOSE, spot=None,
                                              snapshot_captured_at_utc=None,
                                              not_computed_reasons=["first reason"])
    computed_days = [_daily_close(date(2026, 10, 31) + timedelta(days=offset)) for offset in range(6)]
    repository = FakeMonitorRepository(computed_days + [not_computed])
    service = _service(repository, FakeInputLoader(available=False))

    retried = service.run_daily_close_if_missing(datetime(2026, 11, 7, 0, 10, tzinfo=timezone.utc))

    assert [record.record_id for record in retried] == [None]
    assert repository.records[6].not_computed_reasons == ["first reason"]


class TestNotComputedDailyCloseRetry:
    """A stored not-computed daily close is recomputed only when a snapshot newer than
    the one it was last tried with exists at or before its 23:59 UTC (no hourly reload)."""

    NOW = datetime(2026, 11, 7, 0, 10, tzinfo=timezone.utc)
    DAY = date(2026, 11, 6)

    def _repository_with_one_not_computed_day(self, snapshot_captured_at_utc):
        computed_days = [_daily_close(date(2026, 10, 31) + timedelta(days=offset)) for offset in range(6)]
        not_computed = make_record_without_levels(
            as_of=daily_close_as_of(self.DAY), mode=MODE_DAILY_CLOSE, spot=None,
            snapshot_captured_at_utc=snapshot_captured_at_utc,
            not_computed_reasons=["snapshot 75 min older than 23:59 UTC (limit 60)"])
        return FakeMonitorRepository(computed_days + [not_computed])

    def test_no_newer_snapshot_than_the_stored_one_means_no_chain_load_and_an_info_line(self, caplog):
        caplog.set_level("INFO")
        repository = self._repository_with_one_not_computed_day(datetime(2026, 11, 6, 22, 44, tzinfo=timezone.utc))
        repository.option_chain_captures = [datetime(2026, 11, 6, 22, 44, tzinfo=timezone.utc),
                                            datetime(2026, 11, 7, 0, 5, tzinfo=timezone.utc)]  # after 23:59
        loader = FakeInputLoader()
        service = _service(repository, loader)

        assert service.run_daily_close_if_missing(self.NOW) == []
        assert loader.calls == []                          # no chain was loaded
        skip_lines = [record for record in caplog.records if "stays not computed" in record.message]
        assert len(skip_lines) == 1 and skip_lines[0].levelname == "INFO"
        assert "2026-11-06T22:44:00+00:00" in skip_lines[0].message
        assert not [record for record in caplog.records if record.levelname == "WARNING"]

    def test_a_newer_snapshot_at_or_before_23_59_triggers_one_retry(self):
        repository = self._repository_with_one_not_computed_day(datetime(2026, 11, 6, 22, 44, tzinfo=timezone.utc))
        repository.option_chain_captures = [datetime(2026, 11, 6, 23, 59, tzinfo=timezone.utc)]   # equal counts
        loader = FakeInputLoader()
        service = _service(repository, loader)

        retried = service.run_daily_close_if_missing(self.NOW)

        assert [record.as_of_utc for record in retried] == [daily_close_as_of(self.DAY)]
        assert retried[0].has_levels and repository.records[6].has_levels   # replaced
        assert len(loader.calls) == 1

    def test_a_retry_that_stays_not_computed_is_not_repeated_until_a_newer_snapshot(self):
        repository = self._repository_with_one_not_computed_day(datetime(2026, 11, 6, 22, 44, tzinfo=timezone.utc))
        repository.option_chain_captures = [datetime(2026, 11, 6, 23, 0, tzinfo=timezone.utc)]
        # the loader's own snapshot is 75 min old: the retry stays not computed
        loader = FakeInputLoader(snapshot_age=timedelta(minutes=75))
        service = _service(repository, loader)

        first = service.run_daily_close_if_missing(self.NOW)
        assert [record.record_id for record in first] == [None] and len(loader.calls) == 1
        # 01:10 and 02:10: still the 23:00 capture -> no reload
        assert service.run_daily_close_if_missing(self.NOW + timedelta(hours=1)) == []
        assert service.run_daily_close_if_missing(self.NOW + timedelta(hours=2)) == []
        assert len(loader.calls) == 1
        # a 23:30 capture arrives (late sync) -> one more attempt
        repository.option_chain_captures.append(datetime(2026, 11, 6, 23, 30, tzinfo=timezone.utc))
        assert len(service.run_daily_close_if_missing(self.NOW + timedelta(hours=3))) == 1
        assert len(loader.calls) == 2

    def test_a_row_without_snapshot_time_is_retried_once_per_process(self):
        repository = self._repository_with_one_not_computed_day(None)
        loader = FakeInputLoader(available=False)          # no snapshot at all
        service = _service(repository, loader)

        assert [record.record_id for record in service.run_daily_close_if_missing(self.NOW)] == [None]
        assert service.run_daily_close_if_missing(self.NOW + timedelta(hours=1)) == []
        assert len(loader.calls) == 1
        # a new process (new service) tries it once again
        restarted = _service(repository, loader)
        assert len(restarted.run_daily_close_if_missing(self.NOW + timedelta(hours=2))) == 1
        assert len(loader.calls) == 2


class TestDailyCloseInputAge:
    """Like the backtest: no input more than 60 min older than its instant
    (snapshot and index trade vs 23:59 UTC, open interest vs 00:00 UTC)."""

    def test_snapshot_exactly_60_min_old_is_computed(self):
        record = _service(loader=FakeInputLoader(snapshot_age=timedelta(minutes=60))) \
            .compute_daily_close_record(date(2026, 11, 6))
        assert record.spot == SPOT and record.snapshot_captured_at_utc == \
            datetime(2026, 11, 6, 22, 59, tzinfo=timezone.utc)

    def test_snapshot_61_min_old_is_not_computed_with_the_reason(self, caplog):
        # 23:59 - 22:58 = 61 min > 60
        record = _service(loader=FakeInputLoader(snapshot_age=timedelta(minutes=61))) \
            .compute_daily_close_record(date(2026, 11, 6))
        assert record.spot is None and record.hvl is None and record.mode == MODE_DAILY_CLOSE
        assert record.not_computed_reasons == ["snapshot 61 min older than 23:59 UTC (limit 60)"]
        assert "snapshot 61 min older than 23:59 UTC (limit 60)" in caplog.text

    def test_old_open_interest_and_index_trade_are_named_together(self):
        # OI captured 22:45 of 5 Nov = 75 min before 00:00 of 6 Nov; index trade 90 min 30 s before 23:59.
        loader = FakeInputLoader(open_interest_age=timedelta(minutes=75),
                                 index_age=timedelta(minutes=90, seconds=30))
        record = _service(loader=loader).compute_daily_close_record(date(2026, 11, 6))
        assert record.not_computed_reasons == [
            "open-interest snapshot 75 min older than 00:00 UTC (limit 60); "
            "index trade 90 min older than 23:59 UTC (limit 60)"]

    def test_the_live_record_keeps_its_own_stale_handling(self):
        # A 3 h old snapshot still gives a computed live record (the DATA STALE line flags it).
        record = _service(loader=FakeInputLoader(snapshot_age=timedelta(hours=3))).compute_live_record(AS_OF)
        assert record.spot == SPOT and record.not_computed_reasons[0].startswith("panel")

    def test_the_backtest_uses_the_same_constant(self):
        from coding.core.analytics.menthorq_replication_calculator import DAILY_CLOSE_MAXIMUM_INPUT_AGE
        from coding.service.on_chain import menthorq_levels_backtest_service
        assert menthorq_levels_backtest_service.DAILY_CLOSE_MAXIMUM_INPUT_AGE is DAILY_CLOSE_MAXIMUM_INPUT_AGE
        assert DAILY_CLOSE_MAXIMUM_INPUT_AGE == timedelta(minutes=60)


def test_storing_the_same_run_twice_keeps_one_row():
    repository = FakeMonitorRepository()
    service = _service(repository)
    assert service.run_live(AS_OF).record_id == 1
    assert service.run_live(AS_OF + timedelta(seconds=20)).record_id is None   # same minute
    assert len(repository.records) == 1


def test_reference_lookup_offsets_and_tolerances():
    latest = make_record(as_of=AS_OF)
    previous = make_record(as_of=AS_OF - timedelta(hours=1))
    day_ago_in_window = make_record(as_of=AS_OF - timedelta(hours=25))
    week_too_old = make_record(as_of=AS_OF - timedelta(days=8, hours=1))
    repository = FakeMonitorRepository([latest, previous, day_ago_in_window, week_too_old])
    service = _service(repository)

    assert service.find_reference(latest, "previous") is previous      # strictly before latest
    assert service.find_reference(latest, "24h") is day_ago_in_window  # 25 h ago, within 6 h
    assert service.find_reference(latest, "7d") is None                 # 8 d 1 h: beyond 24 h tolerance
    labels = [comparison.label for comparison in service.build_comparisons(latest)]
    assert labels == ["previous", "24h", "7d"]


def test_replayed_comparisons_recompute_earlier_instants_without_storing():
    repository = FakeMonitorRepository()
    loader = FakeInputLoader()
    service = _service(repository, loader)
    latest = service.compute_live_record(AS_OF)
    comparisons = service.build_replayed_comparisons(latest)
    assert [call[0] for call in loader.calls[1:]] == [
        AS_OF - timedelta(hours=1), AS_OF - timedelta(hours=24), AS_OF - timedelta(days=7)]
    assert all(comparison.reference is not None for comparison in comparisons)
    assert repository.records == []
