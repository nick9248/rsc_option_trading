"""
Delivery of urgent alerts and briefs with an in-memory repository and a
fake Telegram (nothing is sent): de-duplication, failed sends, weekly
schedule and catch-up, on-demand brief.
"""

from dataclasses import replace
from datetime import datetime, time, timedelta, timezone

from coding.core.analytics.menthorq_monitor.alert_rules import (
    ALERT_KEY_DATA_STALE,
    URGENT_ALERT_KEYS,
    AlertThresholds,
)
from coding.core.database.config import DatabaseConfig
from coding.service.on_chain.menthorq_monitor.brief_delivery_service import (
    DELIVERY_KEY_ON_DEMAND_BRIEF,
    DELIVERY_KEY_WEEKLY_BRIEF,
    MenthorqBriefDeliveryService,
    latest_weekly_brief_instant,
)
from coding.service.on_chain.menthorq_monitor.menthorq_monitor_service import MenthorqMonitorService
from coding.service.on_chain.menthorq_monitor.monitor_settings import MenthorqMonitorSettings
from tests.fakes.fake_menthorq_monitor import (
    BASE_TIME,
    FakeInputLoader,
    FakeMonitorRepository,
    FakeTelegram,
    make_record,
    make_record_without_levels,
)

FRIDAY_0910 = BASE_TIME   # 2026-11-06 is a Friday


def _delivery(records, accept=True, settings=None):
    repository = FakeMonitorRepository(records)
    telegram = FakeTelegram(accept=accept)
    monitor = MenthorqMonitorService(repository=repository, settings=settings or MenthorqMonitorSettings(),
                                     input_loader=FakeInputLoader())
    clock_value = {"now": FRIDAY_0910}
    service = MenthorqBriefDeliveryService(monitor, telegram, clock=lambda: clock_value["now"])
    return service, repository, telegram, clock_value


def _crossing_history():
    """Previous run above HVL, latest below; 24 h ago HVL 6,000 USD (6.38% of spot) lower."""
    previous = make_record(as_of=FRIDAY_0910 - timedelta(hours=1), spot=96000.0, hvl=95000.0)
    day_ago = make_record(as_of=FRIDAY_0910 - timedelta(hours=24), spot=96000.0, hvl=89000.0)
    latest = make_record(as_of=FRIDAY_0910, spot=94000.0, hvl=95000.0)
    return previous, day_ago, latest


def test_urgent_alerts_are_sent_once_then_held_by_the_cooldown():
    previous, day_ago, latest = _crossing_history()
    service, repository, telegram, clock = _delivery([previous, day_ago, latest])

    # HVL 89,000 -> 95,000 = 6,000 / 94,000 = 6.38% of spot > 5%; the crossing rule is off by default
    delivered = service.send_urgent_alerts(latest, FRIDAY_0910)
    assert delivered == ["hvl_move_24h"]
    assert len(telegram.sent) == 1 and telegram.sent[0]["parse_mode"] == "HTML"
    assert {d["alert_key"] for d in repository.deliveries} == set(delivered)

    # One hour later, same situation: nothing repeated within 12 h
    later = make_record(as_of=FRIDAY_0910 + timedelta(hours=1), spot=94000.0, hvl=95000.0)
    repository.records.append(later)
    clock["now"] = FRIDAY_0910 + timedelta(hours=1)
    assert service.send_urgent_alerts(later, clock["now"]) == []
    assert len(telegram.sent) == 1


def test_failed_send_is_not_recorded_so_the_next_run_retries():
    previous, day_ago, latest = _crossing_history()
    service, repository, telegram, _ = _delivery([previous, day_ago, latest], accept=False)
    assert service.send_urgent_alerts(latest, FRIDAY_0910) == []
    assert repository.deliveries == []
    telegram.accept = True
    assert service.send_urgent_alerts(latest, FRIDAY_0910) != []


NO_USABLE_DATA_ALERT = (
    "<b>URGENT | BTC MenthorQ monitor data is STALE</b>\n"
    "Record 2026-11-06 09:10 UTC | snapshot n/a\n"
    "Monitor has no usable data: inputs not available: No BTC option-chain snapshot at or before "
    "2026-11-06T09:10:00+00:00\n"
    "Movement alerts are paused until fresh data arrives.\n"
    "<i>Check the collector (snapshots). /mute 24h silences these alerts.</i>"
)


class TestNotComputedLatestRecord:
    """A latest live record without levels is treated like stale data: one data_stale alert
    with the not-computed reasons, movement alerts paused, same cooldown and mute."""

    def _no_inputs_record(self):
        return make_record_without_levels(
            spot=None, spot_time_utc=None, snapshot_captured_at_utc=None,
            open_interest_captured_at_utc=None,
            not_computed_reasons=["inputs not available: No BTC option-chain snapshot at or before "
                                  "2026-11-06T09:10:00+00:00"])

    def test_no_inputs_sends_the_data_stale_alert_with_the_reason(self, caplog):
        previous, day_ago, _ = _crossing_history()   # fresh history that would fire hvl_move_24h
        latest = self._no_inputs_record()
        service, repository, telegram, _ = _delivery([previous, day_ago, latest])
        assert service.send_urgent_alerts(latest, FRIDAY_0910) == ["data_stale"]
        assert [message["message"] for message in telegram.sent] == [NO_USABLE_DATA_ALERT]
        assert [delivery["alert_key"] for delivery in repository.deliveries] == ["data_stale"]
        assert any("movement alerts paused" in record.getMessage()
                   for record in caplog.records if record.levelname == "WARNING")

    def test_spot_without_levels_names_at_most_three_reasons(self):
        latest = make_record_without_levels(not_computed_reasons=[f"reason {number}" for number in range(1, 6)])
        service, _, telegram, _ = _delivery([latest])
        assert service.send_urgent_alerts(latest, FRIDAY_0910) == ["data_stale"]
        assert ("Monitor has no usable data: reason 1; reason 2; reason 3; and 2 more\n"
                in telegram.sent[0]["message"])

    def test_it_shares_the_data_stale_cooldown_and_mute(self):
        latest = self._no_inputs_record()
        service, repository, telegram, clock = _delivery([latest])
        repository.save_menthorq_alert_mute("BTC", FRIDAY_0910 + timedelta(hours=1), FRIDAY_0910)
        assert service.send_urgent_alerts(latest, FRIDAY_0910) == []          # muted: held back
        state = repository.get_menthorq_monitor_state("BTC")
        assert state is not None and state.last_suppressed_alert_keys == ["data_stale"]
        clock["now"] = FRIDAY_0910 + timedelta(hours=1)                     # mute over
        assert service.send_urgent_alerts(latest, clock["now"]) == ["data_stale"]
        clock["now"] = FRIDAY_0910 + timedelta(hours=12, minutes=59)        # 11 h 59 min later
        assert service.send_urgent_alerts(latest, clock["now"]) == []
        assert len(telegram.sent) == 1


def test_latest_weekly_brief_instant_by_hand():
    settings = MenthorqMonitorSettings()   # Friday 09:00 UTC
    utc = timezone.utc
    assert latest_weekly_brief_instant(datetime(2026, 11, 6, 9, 0, tzinfo=utc), settings) == \
        datetime(2026, 11, 6, 9, 0, tzinfo=utc)
    assert latest_weekly_brief_instant(datetime(2026, 11, 6, 8, 59, tzinfo=utc), settings) == \
        datetime(2026, 10, 30, 9, 0, tzinfo=utc)
    assert latest_weekly_brief_instant(datetime(2026, 11, 10, 12, 0, tzinfo=utc), settings) == \
        datetime(2026, 11, 6, 9, 0, tzinfo=utc)
    monday_noon = MenthorqMonitorSettings(weekly_brief_weekday=0, weekly_brief_time_utc=time(12, 0))
    assert latest_weekly_brief_instant(datetime(2026, 11, 6, 9, 0, tzinfo=utc), monday_noon) == \
        datetime(2026, 11, 2, 12, 0, tzinfo=utc)


def test_weekly_brief_sent_once_per_week_with_24h_catch_up():
    service, repository, telegram, _ = _delivery([make_record(as_of=FRIDAY_0910 - timedelta(hours=1))])
    assert service.send_weekly_brief_if_due(FRIDAY_0910) is True
    assert "Weekly brief" in telegram.sent[0]["message"]
    assert service.send_weekly_brief_if_due(FRIDAY_0910 + timedelta(hours=1)) is False   # already sent
    assert [d["alert_key"] for d in repository.deliveries] == [DELIVERY_KEY_WEEKLY_BRIEF]


def test_missed_weekly_brief_catches_up_within_24h_only():
    record = make_record(as_of=FRIDAY_0910 - timedelta(hours=1))
    service, _, _, _ = _delivery([record])
    assert service.send_weekly_brief_if_due(datetime(2026, 11, 7, 8, 59, tzinfo=timezone.utc)) is True
    service, _, _, _ = _delivery([record])
    assert service.send_weekly_brief_if_due(datetime(2026, 11, 7, 9, 1, tzinfo=timezone.utc)) is False


def test_weekly_brief_not_due_before_its_time():
    service, _, telegram, _ = _delivery([make_record(as_of=FRIDAY_0910 - timedelta(hours=1))])
    assert service.send_weekly_brief_if_due(datetime(2026, 11, 6, 8, 59, tzinfo=timezone.utc)) is False
    assert telegram.sent == []


def test_on_demand_brief_without_records_says_so():
    service, repository, telegram, _ = _delivery([])
    assert service.send_on_demand_brief(FRIDAY_0910) is True
    assert "No live record is stored yet." in telegram.sent[0]["message"]
    assert repository.deliveries[0]["alert_key"] == DELIVERY_KEY_ON_DEMAND_BRIEF


def test_thresholds_come_from_the_settings_when_not_given():
    previous, day_ago, latest = _crossing_history()
    settings = replace(MenthorqMonitorSettings(),
                       alert_thresholds=AlertThresholds(hvl_move_percent_of_spot=None,
                                                        spot_crosses_hvl=True))
    service, _, _, _ = _delivery([previous, day_ago, latest], settings=settings)
    assert service.thresholds == settings.alert_thresholds
    assert service.send_urgent_alerts(latest, FRIDAY_0910) == ["spot_crossed_below_hvl"]


def test_muted_alerts_are_not_sent_but_recorded_and_resume_after_the_mute():
    previous, day_ago, latest = _crossing_history()
    service, repository, telegram, clock = _delivery([previous, day_ago, latest])
    repository.save_menthorq_alert_mute("BTC", FRIDAY_0910 + timedelta(hours=1), FRIDAY_0910)

    assert service.send_urgent_alerts(latest, FRIDAY_0910) == []
    assert telegram.sent == [] and repository.deliveries == []          # nothing sent, no cooldown
    state = repository.get_menthorq_monitor_state("BTC")
    assert state is not None
    assert state.last_suppressed_alert_keys == ["hvl_move_24h"]
    assert state.last_suppressed_at == FRIDAY_0910

    # At the mute end exactly (now == muted_until) alerts are sent again
    clock["now"] = FRIDAY_0910 + timedelta(hours=1)
    assert service.send_urgent_alerts(latest, FRIDAY_0910 + timedelta(hours=1)) == ["hvl_move_24h"]
    assert len(telegram.sent) == 1


class TestFirstEventAgainstThe24HourBaseline:
    """No event of the family was ever delivered, so the baseline is the state about 24 h ago.
    Net GEX: 2026-11-05 09:10 +5M (the 24 h reference), 11-06 08:10 +5M, 09:10 -1M (flip)."""

    def _setup(self, accept=True):
        previous, day_ago, latest = _crossing_history()
        latest = replace(latest, total_net_gex=-1e6)
        service, repository, telegram, clock = _delivery([previous, day_ago, latest], accept=accept)
        service.thresholds = AlertThresholds(hvl_move_percent_of_spot=None)   # only the sign flip on
        return service, repository, telegram, clock, latest

    @staticmethod
    def _run_at(service, repository, clock, hours_after, total_net_gex):
        clock["now"] = FRIDAY_0910 + timedelta(hours=hours_after)
        record = make_record(as_of=clock["now"], spot=94000.0, hvl=95000.0, total_net_gex=total_net_gex)
        repository.records.append(record)
        return service.send_urgent_alerts(record, clock["now"])

    def test_first_ever_flip_during_a_mute_is_sent_when_the_mute_ends(self):
        service, repository, telegram, clock, latest = self._setup()
        repository.save_menthorq_alert_mute("BTC", FRIDAY_0910 + timedelta(hours=1), FRIDAY_0910)
        assert service.send_urgent_alerts(latest, FRIDAY_0910) == []          # muted
        state = repository.get_menthorq_monitor_state("BTC")
        assert state is not None and state.last_suppressed_alert_keys == ["net_gex_turned_negative"]
        # 10:10, mute over: 24 h reference = newest record at or before 11-05 10:10 = 11-05 09:10
        # (+5M, positive); now -1M (negative) differs. vs the previous run (-1M) no flip -> note.
        assert self._run_at(service, repository, clock, 1, -1e6) == ["net_gex_turned_negative"]
        assert ("Total net GEX turned NEGATIVE (earlier, not reported yet; about 24h ago: positive)."
                in telegram.sent[-1]["message"])
        # reported now: the delivered-state rule takes over, the next run sends nothing
        assert self._run_at(service, repository, clock, 2, -1e6) == []
        assert len(telegram.sent) == 1

    def test_first_ever_flip_whose_send_failed_is_sent_at_the_next_run(self):
        service, repository, telegram, clock, latest = self._setup(accept=False)
        assert service.send_urgent_alerts(latest, FRIDAY_0910) == []          # send failed
        assert repository.deliveries == []
        telegram.accept = True
        assert self._run_at(service, repository, clock, 1, -1e6) == ["net_gex_turned_negative"]

    def test_flip_and_flip_back_before_it_was_reported_sends_nothing(self):
        service, repository, telegram, clock, latest = self._setup()
        repository.save_menthorq_alert_mute("BTC", FRIDAY_0910 + timedelta(hours=1), FRIDAY_0910)
        assert service.send_urgent_alerts(latest, FRIDAY_0910) == []          # muted
        # 10:10: back to +3M. vs the previous run (-1M) that is a flip to positive, but the
        # 24 h baseline is positive too: nothing new to report
        assert self._run_at(service, repository, clock, 1, 3e6) == []
        assert telegram.sent == []

    def test_hvl_regime_none_at_the_chain_edge_does_not_hide_a_crossing(self):
        """07:10 spot 96,000 above HVL 95,000 (positive); 08:10 HVL None (chain edge: regime
        None); 09:10 spot 94,000 below HVL 95,000 (negative). No record is 24 h old, so the
        reference is the oldest within 24 h (07:10, positive): crossed below. The previous-run
        rule alone (08:10 None -> negative) saw no crossing."""
        two_hours_ago = make_record(as_of=FRIDAY_0910 - timedelta(hours=2), spot=96000.0, hvl=95000.0)
        at_chain_edge = make_record(as_of=FRIDAY_0910 - timedelta(hours=1), spot=96000.0, hvl=None)
        latest = make_record(as_of=FRIDAY_0910, spot=94000.0, hvl=95000.0)
        assert at_chain_edge.has_levels and at_chain_edge.gamma_regime is None
        service, _, _, _ = _delivery([two_hours_ago, at_chain_edge, latest])
        service.thresholds = AlertThresholds(hvl_move_percent_of_spot=None, net_gex_sign_change=False,
                                             spot_crosses_hvl=True)
        assert service.send_urgent_alerts(latest, FRIDAY_0910) == ["spot_crossed_below_hvl"]

    def test_without_any_earlier_stored_record_the_previous_run_rule_applies(self):
        """Only the latest record is stored (first hour of a new deployment): no baseline and
        no previous run, so nothing fires."""
        latest = make_record(as_of=FRIDAY_0910, total_net_gex=-1e6)
        service, _, telegram, _ = _delivery([latest])
        service.thresholds = AlertThresholds(hvl_move_percent_of_spot=None, spot_crosses_hvl=True)
        assert service.send_urgent_alerts(latest, FRIDAY_0910) == []
        assert telegram.sent == []


class TestEventAlertsAgainstTheLastReportedState:
    """Net GEX: positive was last REPORTED (net_gex_turned_positive delivered 2 days ago).
    08:10 +5M, 09:10 -1M (flip), 10:10 still -1M (no flip vs the previous run)."""

    def _setup(self, accept=True):
        previous, day_ago, latest = _crossing_history()
        previous = replace(previous, total_net_gex=5e6)
        latest = replace(latest, total_net_gex=-1e6)
        service, repository, telegram, clock = _delivery([previous, day_ago, latest], accept=accept)
        service.thresholds = AlertThresholds(hvl_move_percent_of_spot=None)   # only the sign flip on
        repository.save_menthorq_alert_deliveries("BTC", ["net_gex_turned_positive"],
                                                  FRIDAY_0910 - timedelta(days=2), "earlier alert")
        return service, repository, telegram, clock, latest

    @staticmethod
    def _run_at(service, repository, clock, hours_after, total_net_gex):
        clock["now"] = FRIDAY_0910 + timedelta(hours=hours_after)
        record = make_record(as_of=clock["now"], spot=94000.0, hvl=95000.0, total_net_gex=total_net_gex)
        repository.records.append(record)
        return service.send_urgent_alerts(record, clock["now"])

    def test_the_last_reported_state_is_read_from_the_delivery_log(self):
        service, repository, _, _, _ = self._setup()
        assert service.last_reported_event_states() == {"net_gex_sign": "positive"}
        repository.save_menthorq_alert_deliveries("BTC", ["spot_crossed_below_hvl"], FRIDAY_0910, "m")
        repository.save_menthorq_alert_deliveries("BTC", ["net_gex_turned_negative"],
                                                  FRIDAY_0910 - timedelta(days=1), "m")
        assert service.last_reported_event_states() == {"net_gex_sign": "negative", "hvl_regime": "negative"}

    def test_a_flip_whose_send_failed_is_sent_at_the_next_run(self):
        service, repository, telegram, clock, latest = self._setup(accept=False)
        assert service.send_urgent_alerts(latest, FRIDAY_0910) == []          # send failed
        telegram.accept = True
        assert self._run_at(service, repository, clock, 1, -1e6) == ["net_gex_turned_negative"]
        assert ("Total net GEX turned NEGATIVE (earlier, not reported yet; last reported: positive)."
                in telegram.sent[-1]["message"])
        # reported now: the next run (still negative) sends nothing
        assert self._run_at(service, repository, clock, 2, -1e6) == []
        assert service.last_reported_event_states() == {"net_gex_sign": "negative"}

    def test_a_flip_during_a_mute_is_sent_after_the_mute(self):
        service, repository, telegram, clock, latest = self._setup()
        repository.save_menthorq_alert_mute("BTC", FRIDAY_0910 + timedelta(hours=1), FRIDAY_0910)
        assert service.send_urgent_alerts(latest, FRIDAY_0910) == []          # muted
        assert telegram.sent == []
        # 10:10: the mute ended at 10:10 exactly; -1M still differs from the reported positive
        assert self._run_at(service, repository, clock, 1, -1e6) == ["net_gex_turned_negative"]
        assert len(telegram.sent) == 1

    def test_a_flip_that_reverted_before_it_was_reported_sends_nothing(self):
        service, repository, telegram, clock, latest = self._setup()
        repository.save_menthorq_alert_mute("BTC", FRIDAY_0910 + timedelta(hours=1), FRIDAY_0910)
        assert service.send_urgent_alerts(latest, FRIDAY_0910) == []          # muted
        # 10:10: back to +3M. vs the previous run that is a flip to positive, but positive is
        # what the owner was last told, so there is nothing new to report
        assert self._run_at(service, repository, clock, 1, 3e6) == []
        assert telegram.sent == []

    def test_a_fresh_flip_still_carries_the_change_and_no_note(self):
        service, _, telegram, _, latest = self._setup()
        assert service.send_urgent_alerts(latest, FRIDAY_0910) == ["net_gex_turned_negative"]
        message = telegram.sent[-1]["message"]
        # 5M -> -1M = -6M
        assert "Total net GEX turned NEGATIVE: net GEX change -$6.00M." in message
        assert "not reported yet" not in message

    def test_hvl_crossing_family_uses_its_own_reported_state(self):
        # Spot 96,000 above HVL 95,000 at 08:10 -> 94,000 below at 09:10 (crossed below).
        previous, day_ago, latest = _crossing_history()
        service, repository, telegram, clock = _delivery([previous, day_ago, latest], accept=False)
        service.thresholds = AlertThresholds(hvl_move_percent_of_spot=None, net_gex_sign_change=False,
                                             spot_crosses_hvl=True)
        repository.save_menthorq_alert_deliveries("BTC", ["spot_crossed_above_hvl"],
                                                  FRIDAY_0910 - timedelta(days=1), "m")
        assert service.send_urgent_alerts(latest, FRIDAY_0910) == []          # send failed
        telegram.accept = True
        assert self._run_at(service, repository, clock, 1, 5e6) == ["spot_crossed_below_hvl"]


def test_unreadable_mute_state_sends_the_alert_and_logs(caplog):
    previous, day_ago, latest = _crossing_history()
    service, repository, telegram, _ = _delivery([previous, day_ago, latest])
    repository.fail_state = True
    assert service.send_urgent_alerts(latest, FRIDAY_0910) == ["hvl_move_24h"]
    assert "mute state not readable" in caplog.text


def test_mute_does_not_silence_the_briefs():
    service, repository, telegram, _ = _delivery([make_record(as_of=FRIDAY_0910 - timedelta(hours=1))])
    repository.save_menthorq_alert_mute("BTC", FRIDAY_0910 + timedelta(days=3), FRIDAY_0910)
    assert service.send_on_demand_brief(FRIDAY_0910) is True
    assert service.send_weekly_brief_if_due(FRIDAY_0910) is True
    assert len(telegram.sent) == 2


def test_weekly_brief_from_a_record_stored_days_ago_carries_the_stale_line():
    # Record 2026-11-06 09:10 (snapshot 08:50, fresh then); collector down until Friday 13th 09:10.
    service, _, telegram, clock = _delivery([make_record()])
    clock["now"] = FRIDAY_0910 + timedelta(days=7)
    assert service.send_weekly_brief_if_due(clock["now"]) is True
    # now - snapshot = 7 d 20 min
    assert ("<b>DATA STALE:</b> snapshot is 7 d 0 h 20 min older than now (2026-11-13 09:10 UTC)"
            in telegram.sent[-1]["message"])


def test_weekly_brief_from_a_fresh_record_has_no_stale_line():
    service, _, telegram, _ = _delivery([make_record()])
    assert service.send_weekly_brief_if_due(FRIDAY_0910) is True
    assert "DATA STALE" not in telegram.sent[-1]["message"]


def _stale_crossing_history():
    """The crossing history, but the latest record's snapshot is 3 h before its data time (06:10)."""
    previous, day_ago, latest = _crossing_history()
    latest.snapshot_captured_at_utc = FRIDAY_0910 - timedelta(hours=3)
    return previous, day_ago, latest


STALE_ALERT = (
    "<b>URGENT | BTC MenthorQ monitor data is STALE</b>\n"
    "Record 2026-11-06 09:10 UTC | snapshot 2026-11-06 06:10 UTC\n"
    "Snapshot is 3 h 0 min older than the data time; levels may be out of date.\n"
    "Movement alerts are paused until fresh data arrives.\n"
    "<i>Check the collector (snapshots). /mute 24h silences these alerts.</i>"
)


class TestStaleDataAlert:
    def test_stale_record_sends_only_the_stale_alert_and_logs_a_warning(self, caplog):
        # Fresh, this history fires hvl_move_24h (6.38% of spot); stale (3 h > 120 min) it must not.
        previous, day_ago, latest = _stale_crossing_history()
        service, repository, telegram, _ = _delivery([previous, day_ago, latest])
        assert service.send_urgent_alerts(latest, FRIDAY_0910) == ["data_stale"]
        assert [message["message"] for message in telegram.sent] == [STALE_ALERT]
        assert [delivery["alert_key"] for delivery in repository.deliveries] == ["data_stale"]
        warnings = [record for record in caplog.records if record.levelname == "WARNING"]
        assert any("movement alerts paused" in record.getMessage() for record in warnings)

    def test_record_stored_long_ago_is_stale_against_now(self):
        # Snapshot 08:50, fresh at its data time 09:10; evaluated 3 h later (12:10) -> 3 h 20 min old.
        previous, day_ago, latest = _crossing_history()
        service, _, telegram, _ = _delivery([previous, day_ago, latest])
        later = FRIDAY_0910 + timedelta(hours=3)
        assert service.send_urgent_alerts(latest, later) == ["data_stale"]
        assert ("Snapshot is 3 h 20 min older than now (2026-11-06 12:10 UTC); levels may be out of date."
                in telegram.sent[0]["message"])

    def test_fresh_record_sends_the_normal_alerts(self):
        previous, day_ago, latest = _crossing_history()
        service, _, telegram, _ = _delivery([previous, day_ago, latest])
        assert service.send_urgent_alerts(latest, FRIDAY_0910) == ["hvl_move_24h"]
        assert "STALE" not in telegram.sent[0]["message"]

    def test_stale_alert_has_the_12h_cooldown(self):
        previous, day_ago, latest = _stale_crossing_history()
        service, _, telegram, clock = _delivery([previous, day_ago, latest])
        assert service.send_urgent_alerts(latest, FRIDAY_0910) == ["data_stale"]
        # 11 h 59 min later: on cooldown, nothing sent (and still no movement alert)
        clock["now"] = FRIDAY_0910 + timedelta(hours=11, minutes=59)
        assert service.send_urgent_alerts(latest, clock["now"]) == []
        assert len(telegram.sent) == 1
        # exactly 12 h later: sent again
        clock["now"] = FRIDAY_0910 + timedelta(hours=12)
        assert service.send_urgent_alerts(latest, clock["now"]) == ["data_stale"]
        assert len(telegram.sent) == 2

    def test_muted_stale_alert_is_held_back_and_recorded(self):
        previous, day_ago, latest = _stale_crossing_history()
        service, repository, telegram, _ = _delivery([previous, day_ago, latest])
        repository.save_menthorq_alert_mute("BTC", FRIDAY_0910 + timedelta(hours=1), FRIDAY_0910)
        assert service.send_urgent_alerts(latest, FRIDAY_0910) == []
        assert telegram.sent == [] and repository.deliveries == []
        state = repository.get_menthorq_monitor_state("BTC")
        assert state is not None and state.last_suppressed_alert_keys == ["data_stale"]

    def test_stale_key_is_an_urgent_key_so_status_lists_it(self):
        assert ALERT_KEY_DATA_STALE == "data_stale" and ALERT_KEY_DATA_STALE in URGENT_ALERT_KEYS


def test_weekly_brief_from_two_threads_at_once_is_sent_exactly_once():
    """The scheduled weekly job and the hourly catch-up run in different scheduler threads.
    The send takes a while (0.2 s), so without the lock both pass the 'not yet delivered'
    check before either records the delivery."""
    import threading
    import time as wall_time

    service, repository, telegram, _ = _delivery([make_record()])
    real_send = telegram.send

    def slow_send(message, parse_mode=None):
        wall_time.sleep(0.2)
        return real_send(message, parse_mode)

    telegram.send = slow_send  # type: ignore[method-assign]
    start = threading.Barrier(2)
    results = []

    def run():
        start.wait()
        results.append(service.send_weekly_brief_if_due(FRIDAY_0910))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert sorted(results) == [False, True]
    assert len(telegram.sent) == 1
    assert [d["alert_key"] for d in repository.deliveries] == [DELIVERY_KEY_WEEKLY_BRIEF]


def test_urgent_keys_are_recorded_together_or_not_at_all():
    """Two alerts in one message: HVL 89,000 -> 95,000 = 6.38% of spot, and the net GEX
    sign flip (+5M previous -> -1M latest)."""
    previous, day_ago, latest = _crossing_history()
    previous = replace(previous, total_net_gex=5e6)
    latest = replace(latest, total_net_gex=-1e6)
    service, repository, telegram, _ = _delivery([previous, day_ago, latest])
    repository.fail_deliveries = True
    # Telegram accepted it, so it counts as delivered; no row is partially recorded.
    assert service.send_urgent_alerts(latest, FRIDAY_0910) == ["hvl_move_24h", "net_gex_turned_negative"]
    assert len(telegram.sent) == 1 and repository.deliveries == []

    repository.fail_deliveries = False
    service, repository, telegram, _ = _delivery([previous, day_ago, latest])
    assert service.send_urgent_alerts(latest, FRIDAY_0910) == ["hvl_move_24h", "net_gex_turned_negative"]
    assert len({d["delivered_at"] for d in repository.deliveries}) == 1
    assert [d["alert_key"] for d in repository.deliveries] == ["hvl_move_24h", "net_gex_turned_negative"]


class TestDeliveryRowNotSaved:
    """Telegram accepted the message, then the delivery write fails: logged at ERROR, never
    raised, and remembered in memory so the message is not resent while the process runs."""

    def test_weekly_brief_is_not_resent_every_hour(self, caplog):
        service, repository, telegram, clock = _delivery([make_record()])
        repository.fail_deliveries = True
        with caplog.at_level("INFO"):
            assert service.send_weekly_brief_if_due(FRIDAY_0910) is True
        errors = [record for record in caplog.records if record.levelname == "ERROR"]
        assert len(errors) == 1
        assert "['weekly_brief'] delivered by Telegram but the delivery row was NOT saved" in errors[0].getMessage()
        assert repository.deliveries == []
        # The hourly catch-up of the next 23 h: the delivery (09:10) is after the 09:00 instant.
        for hours in range(1, 24):
            clock["now"] = FRIDAY_0910 + timedelta(hours=hours)
            assert service.send_weekly_brief_if_due(clock["now"]) is False
        assert len(telegram.sent) == 1

    def test_urgent_alert_is_held_by_the_cooldown(self, caplog):
        previous, day_ago, latest = _crossing_history()
        service, repository, telegram, clock = _delivery([previous, day_ago, latest])
        repository.fail_deliveries = True
        assert service.send_urgent_alerts(latest, FRIDAY_0910) == ["hvl_move_24h"]
        assert "delivery row was NOT saved" in caplog.text
        # The next hourly run (snapshot 08:50, now 10:10: 80 min, still fresh): with no row and
        # no memory the alert would be sent again; the remembered delivery holds the cooldown.
        clock["now"] = FRIDAY_0910 + timedelta(hours=1)
        assert service.send_urgent_alerts(latest, clock["now"]) == []
        assert len(telegram.sent) == 1
        assert service.last_delivery("hvl_move_24h") == FRIDAY_0910

    def test_on_demand_brief_does_not_raise(self, caplog):
        service, repository, telegram, _ = _delivery([make_record()])
        repository.fail_deliveries = True
        assert service.send_on_demand_brief(FRIDAY_0910) is True
        assert len(telegram.sent) == 1 and repository.deliveries == []
        assert "['on_demand_brief'] delivered by Telegram but the delivery row was NOT saved" in caplog.text

    def test_a_new_process_has_no_memory(self):
        # The limit of the fix: a restart forgets the unsaved delivery (no row exists).
        service, repository, _, _ = _delivery([make_record()])
        repository.fail_deliveries = True
        service.send_weekly_brief_if_due(FRIDAY_0910)
        restarted = MenthorqBriefDeliveryService(service.monitor_service, FakeTelegram())
        assert restarted.last_delivery(DELIVERY_KEY_WEEKLY_BRIEF) is None


class OperationalError(Exception):
    """Stand-in for a database driver error class."""


class TestMonitorErrorAlert:
    """The hourly computation raised: one monitor_error alert, cooldown and mute as usual."""

    def test_one_alert_with_class_scrubbed_message_and_no_traceback(self, monkeypatch):
        monkeypatch.setattr("coding.service.on_chain.menthorq_monitor.brief_delivery_service.DatabaseConfig",
                            lambda: DatabaseConfig(password="s3cret"))
        service, repository, telegram, _ = _delivery([])
        error = OperationalError("could not connect: password s3cret rejected, token fake-token")
        assert service.send_monitor_error_alert(error, FRIDAY_0910) is True
        # "s3cret" -> "<database password>", the FakeTelegram token "fake-token" -> "<bot token>",
        # both then escaped (< -> &lt;, > -> &gt;)
        assert [sent["message"] for sent in telegram.sent] == [
            "<b>Monitor error:</b> the hourly computation failed: OperationalError: "
            "could not connect: password &lt;database password&gt; rejected, token &lt;bot token&gt;. "
            "Levels and alerts are paused until it succeeds."]
        assert telegram.sent[0]["parse_mode"] == "HTML"
        assert "Traceback" not in telegram.sent[0]["message"]
        assert [delivery["alert_key"] for delivery in repository.deliveries] == ["monitor_error"]
        assert "monitor_error" in URGENT_ALERT_KEYS

    def test_message_is_cut_to_200_characters_before_escaping(self):
        service, _, telegram, _ = _delivery([])
        service.send_monitor_error_alert(RuntimeError("<" * 250), FRIDAY_0910)
        # 250 "<" cut to 200, each then escaped to "&lt;"
        assert telegram.sent[0]["message"] == (
            "<b>Monitor error:</b> the hourly computation failed: RuntimeError: " + "&lt;" * 200
            + ". Levels and alerts are paused until it succeeds.")

    def test_cooldown_is_12_hours(self):
        service, _, telegram, _ = _delivery([])
        assert service.send_monitor_error_alert(RuntimeError("down"), FRIDAY_0910) is True
        assert service.send_monitor_error_alert(
            RuntimeError("down"), FRIDAY_0910 + timedelta(hours=11, minutes=59)) is False
        assert service.send_monitor_error_alert(
            RuntimeError("down"), FRIDAY_0910 + timedelta(hours=12)) is True
        assert len(telegram.sent) == 2

    def test_muted_alert_is_held_back_and_recorded(self):
        service, repository, telegram, _ = _delivery([])
        repository.save_menthorq_alert_mute("BTC", FRIDAY_0910 + timedelta(hours=1), FRIDAY_0910)
        assert service.send_monitor_error_alert(RuntimeError("down"), FRIDAY_0910) is False
        assert telegram.sent == [] and repository.deliveries == []
        state = repository.get_menthorq_monitor_state("BTC")
        assert state is not None and state.last_suppressed_alert_keys == ["monitor_error"]

    def test_database_down_gives_one_alert_per_cooldown_from_memory(self, monkeypatch, caplog):
        service, repository, telegram, _ = _delivery([])

        def raise_database_down(*_arguments, **_keywords):
            raise RuntimeError("database down")
        monkeypatch.setattr(repository, "get_last_menthorq_alert_delivery", raise_database_down)
        repository.fail_deliveries = True
        assert service.send_monitor_error_alert(RuntimeError("database down"), FRIDAY_0910) is True
        assert service.send_monitor_error_alert(
            RuntimeError("database down"), FRIDAY_0910 + timedelta(hours=1)) is False
        assert len(telegram.sent) == 1
        assert "monitor-error cooldown from this process's memory only" in caplog.text

    def test_a_raising_alert_path_is_swallowed_and_logged(self, monkeypatch, caplog):
        service, _, telegram, _ = _delivery([])

        def raise_on_send(*_arguments, **_keywords):
            raise RuntimeError("sender broken")
        monkeypatch.setattr(telegram, "send", raise_on_send)
        assert service.send_monitor_error_alert(RuntimeError("down"), FRIDAY_0910) is False
        assert "the monitor-error alert itself failed" in caplog.text
