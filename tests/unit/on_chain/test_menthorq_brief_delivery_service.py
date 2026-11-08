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


def test_not_computed_latest_record_evaluates_no_alert():
    service, _, telegram, _ = _delivery([])
    assert service.send_urgent_alerts(make_record(spot=None, hvl=None), FRIDAY_0910) == []
    assert telegram.sent == []


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
