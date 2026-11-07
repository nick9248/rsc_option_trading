"""
Delivery of urgent alerts and briefs with an in-memory repository and a
fake Telegram (nothing is sent): de-duplication, failed sends, weekly
schedule and catch-up, on-demand brief.
"""

from datetime import datetime, time, timedelta, timezone

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
    """Previous run above HVL, latest below; 24 h ago HVL 4% of spot lower."""
    previous = make_record(as_of=FRIDAY_0910 - timedelta(hours=1), spot=96000.0, hvl=95000.0)
    day_ago = make_record(as_of=FRIDAY_0910 - timedelta(hours=24), spot=96000.0, hvl=91000.0)
    latest = make_record(as_of=FRIDAY_0910, spot=94000.0, hvl=95000.0)
    return previous, day_ago, latest


def test_urgent_alerts_are_sent_once_then_held_by_the_cooldown():
    previous, day_ago, latest = _crossing_history()
    service, repository, telegram, clock = _delivery([previous, day_ago, latest])

    # HVL 91,000 -> 95,000 = 4,000 / 94,000 = 4.26% of spot > 3%; spot crossed below HVL
    delivered = service.send_urgent_alerts(latest, FRIDAY_0910)
    assert sorted(delivered) == ["hvl_move_24h", "spot_crossed_below_hvl"]
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
