"""
Monitor settings from the environment, job registration on APScheduler (not
started), step isolation of the hourly run, and the import graph (no
matplotlib on the collector path).
"""

import subprocess
import sys
from datetime import time, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

from coding.service.on_chain.menthorq_monitor.menthorq_monitor_job import (
    COMMAND_POLL_JOB_ID,
    HOURLY_JOB_ID,
    WEEKLY_BRIEF_JOB_ID,
    MenthorqMonitorJob,
    build_menthorq_monitor_job,
    start_menthorq_monitor_job,
)
from coding.core.analytics.menthorq_monitor.alert_rules import AlertThresholds
from coding.service.on_chain.menthorq_monitor.brief_formatter import DEFAULT_STALE_SNAPSHOT_LIMIT
from coding.service.on_chain.menthorq_monitor.command_formatter import BOT_COMMANDS
from coding.service.on_chain.menthorq_monitor.monitor_settings import (
    MINIMUM_COMMAND_POLL_SECONDS,
    MenthorqMonitorSettings,
)
from coding.service.on_chain.menthorq_monitor.telegram_command_listener import LONG_POLL_SECONDS
from coding.service.on_chain.menthorq_monitor.brief_delivery_service import MenthorqBriefDeliveryService
from coding.service.on_chain.menthorq_monitor.menthorq_monitor_service import MenthorqMonitorService
from tests.fakes.fake_menthorq_monitor import BASE_TIME, FakeInputLoader, FakeMonitorRepository, FakeTelegram

REPOSITORY_ROOT = Path(__file__).parents[3]


def test_defaults_when_nothing_is_set():
    settings = MenthorqMonitorSettings.from_environment({})
    assert settings == MenthorqMonitorSettings()
    assert settings.weekly_brief_day_name == "fri"
    assert settings.weekly_brief_time_utc == time(9, 0)
    assert settings.expiry_universe == "monthly"
    # All three switches are opt-in (the production .env sets them): a local collector run
    # writes no monitor row into the synced mirror and never talks to the bot.
    assert settings.monitor_enabled is False
    assert settings.alerts_enabled is False and settings.commands_enabled is False


def test_the_three_switches_are_read_from_the_environment():
    settings = MenthorqMonitorSettings.from_environment(
        {"MENTHORQ_MONITOR_ENABLED": "1", "MENTHORQ_ALERTS_ENABLED": "1", "MENTHORQ_COMMANDS_ENABLED": "1"})
    assert settings.monitor_enabled is True
    assert settings.alerts_enabled is True and settings.commands_enabled is True
    assert MenthorqMonitorSettings.from_environment({"MENTHORQ_MONITOR_ENABLED": "0"}).monitor_enabled is False


def test_values_are_read_from_the_environment():
    settings = MenthorqMonitorSettings.from_environment({
        "MENTHORQ_WEEKLY_BRIEF_DAY": "Mon", "MENTHORQ_WEEKLY_BRIEF_TIME_UTC": "07:30",
        "MENTHORQ_LIVE_RUN_MINUTE": "5", "MENTHORQ_ALERTS_ENABLED": "0",
        "MENTHORQ_EXPIRY_UNIVERSE": "all", "MENTHORQ_COMMAND_POLL_SECONDS": "30",
    })
    assert settings.weekly_brief_weekday == 0 and settings.weekly_brief_time_utc == time(7, 30)
    assert settings.live_run_minute == 5 and settings.alerts_enabled is False
    assert settings.expiry_universe == "all" and settings.command_poll_seconds == 30


@pytest.mark.parametrize("name, value", [
    ("MENTHORQ_WEEKLY_BRIEF_DAY", "friday"), ("MENTHORQ_WEEKLY_BRIEF_TIME_UTC", "9am"),
    ("MENTHORQ_LIVE_RUN_MINUTE", "60"), ("MENTHORQ_ALERTS_ENABLED", "maybe"),
    ("MENTHORQ_EXPIRY_UNIVERSE", "weekly"), ("MENTHORQ_COMMAND_POLL_SECONDS", "1"),
    ("MENTHORQ_ALERT_HVL_MOVE_PERCENT", "five"), ("MENTHORQ_ALERT_HVL_MOVE_PERCENT", "0"),
    ("MENTHORQ_ALERT_CALL_RESISTANCE_MOVE_PERCENT", "-10"),
    ("MENTHORQ_ALERT_PUT_SUPPORT_MOVE_PERCENT", "101"),
    ("MENTHORQ_ALERT_SPOT_NEAR_WALL_PERCENT", "1%"), ("MENTHORQ_ALERT_SPOT_CROSSES_HVL", "sometimes"),
    ("MENTHORQ_ALERT_NET_GEX_SIGN_CHANGE", "2"),
    ("MENTHORQ_STALE_SNAPSHOT_MINUTES", "9"), ("MENTHORQ_STALE_SNAPSHOT_MINUTES", "2h"),
])
def test_invalid_values_raise_instead_of_falling_back(name, value):
    with pytest.raises(ValueError, match=name):
        MenthorqMonitorSettings.from_environment({name: value})


def test_command_poll_minimum_is_the_long_poll_wait_plus_two_seconds():
    # LONG_POLL_SECONDS = 10 -> minimum 12; 11 would make APScheduler skip runs.
    assert LONG_POLL_SECONDS == 10 and MINIMUM_COMMAND_POLL_SECONDS == 12
    assert MenthorqMonitorSettings.from_environment(
        {"MENTHORQ_COMMAND_POLL_SECONDS": "12"}).command_poll_seconds == 12
    for too_short in ("11", "5"):
        with pytest.raises(ValueError, match=r"MENTHORQ_COMMAND_POLL_SECONDS=\d+ is outside 12\.\.None"):
            MenthorqMonitorSettings.from_environment({"MENTHORQ_COMMAND_POLL_SECONDS": too_short})


@pytest.mark.parametrize("value", ["09:00+02:00", "09:00Z", "09:00+00:00", "07:30-05:00"])
def test_weekly_brief_time_with_an_offset_is_refused(value):
    with pytest.raises(ValueError, match="UTC only, format HH:MM"):
        MenthorqMonitorSettings.from_environment({"MENTHORQ_WEEKLY_BRIEF_TIME_UTC": value})


def test_stale_snapshot_limit_default_minimum_and_value():
    default = MenthorqMonitorSettings.from_environment({})
    assert default.stale_snapshot_minutes == 120
    assert default.stale_snapshot_limit == timedelta(minutes=120) == DEFAULT_STALE_SNAPSHOT_LIMIT
    minimum = MenthorqMonitorSettings.from_environment({"MENTHORQ_STALE_SNAPSHOT_MINUTES": "10"})
    assert minimum.stale_snapshot_limit == timedelta(minutes=10)
    assert MenthorqMonitorSettings.from_environment(
        {"MENTHORQ_STALE_SNAPSHOT_MINUTES": " 600 "}).stale_snapshot_minutes == 600


def test_alert_rule_defaults_on_and_off():
    thresholds = MenthorqMonitorSettings.from_environment({}).alert_thresholds
    assert thresholds == AlertThresholds()
    # ON: HVL > 5%, Call Resistance > 10%, Put Support > 5%, net GEX sign change
    assert (thresholds.hvl_move_percent_of_spot, thresholds.call_resistance_move_percent_of_spot,
            thresholds.put_support_move_percent_of_spot, thresholds.net_gex_sign_change) ==         (5.0, 10.0, 5.0, True)
    # OFF: spot crosses HVL, spot near a wall
    assert thresholds.spot_crosses_hvl is False and thresholds.spot_near_wall_percent is None


def test_alert_rules_are_read_from_the_environment():
    thresholds = MenthorqMonitorSettings.from_environment({
        "MENTHORQ_ALERT_HVL_MOVE_PERCENT": "3.5", "MENTHORQ_ALERT_CALL_RESISTANCE_MOVE_PERCENT": "off",
        "MENTHORQ_ALERT_PUT_SUPPORT_MOVE_PERCENT": " 7 ", "MENTHORQ_ALERT_NET_GEX_SIGN_CHANGE": "0",
        "MENTHORQ_ALERT_SPOT_CROSSES_HVL": "1", "MENTHORQ_ALERT_SPOT_NEAR_WALL_PERCENT": "1",
    }).alert_thresholds
    assert thresholds == AlertThresholds(
        hvl_move_percent_of_spot=3.5, call_resistance_move_percent_of_spot=None,
        put_support_move_percent_of_spot=7.0, net_gex_sign_change=False, spot_crosses_hvl=True,
        spot_near_wall_percent=1.0)


def test_built_job_uses_the_settings_thresholds_and_all_commands():
    settings = MenthorqMonitorSettings.from_environment({"MENTHORQ_ALERT_SPOT_CROSSES_HVL": "1",
                                                         "MENTHORQ_MONITOR_ENABLED": "1",
                                                         "MENTHORQ_COMMANDS_ENABLED": "1"})
    telegram = FakeTelegram()
    job = build_menthorq_monitor_job(repository=MagicMock(), settings=settings, telegram=telegram)
    assert job is not None and job.command_listener is not None
    assert job.delivery_service.thresholds.spot_crosses_hvl is True
    assert set(job.command_listener._handlers) == {"/brief", "/levels", "/changes", "/status",
                                                   "/mute", "/help"}
    job.command_listener.register_command_menu()
    assert telegram.registered_commands == [list(BOT_COMMANDS)]
    assert [command for command, _ in BOT_COMMANDS] == ["brief", "levels", "changes", "status",
                                                        "mute", "help"]


def test_default_settings_build_no_job_and_log_one_info_line_naming_the_variable(caplog):
    repository = MagicMock()
    with caplog.at_level("INFO"):
        job = build_menthorq_monitor_job(repository=repository, telegram=FakeTelegram(),
                                         settings=MenthorqMonitorSettings.from_environment({}))
    assert job is None
    lines = [record for record in caplog.records if "MenthorQ" in record.getMessage()]
    assert len(lines) == 1 and lines[0].levelname == "INFO"
    assert "MenthorQ monitor OFF" in lines[0].getMessage()
    assert "set MENTHORQ_MONITOR_ENABLED=1" in lines[0].getMessage()
    assert repository.mock_calls == []          # nothing read or written


ALL_ON = MenthorqMonitorSettings(monitor_enabled=True, alerts_enabled=True, commands_enabled=True)
MONITOR_ONLY = MenthorqMonitorSettings(monitor_enabled=True)


def test_monitor_on_with_telegram_defaults_registers_only_the_hourly_records_job(caplog):
    job = build_menthorq_monitor_job(
        repository=MagicMock(), telegram=FakeTelegram(),
        settings=MenthorqMonitorSettings.from_environment({"MENTHORQ_MONITOR_ENABLED": "1"}))
    assert job is not None and job.command_listener is None
    scheduler = BackgroundScheduler(timezone=timezone.utc)
    with caplog.at_level("INFO"):
        job.register(scheduler)
    assert {scheduled.id for scheduled in scheduler.get_jobs()} == {HOURLY_JOB_ID}
    off_lines = [record for record in caplog.records if "Telegram OFF" in record.getMessage()]
    assert len(off_lines) == 1 and off_lines[0].levelname == "INFO"
    assert "MENTHORQ_ALERTS_ENABLED=1" in off_lines[0].getMessage()
    assert "MENTHORQ_COMMANDS_ENABLED=1" in off_lines[0].getMessage()


def test_only_the_switched_off_part_is_named(caplog):
    job = build_menthorq_monitor_job(repository=MagicMock(), telegram=FakeTelegram(),
                                     settings=MenthorqMonitorSettings(monitor_enabled=True,
                                                                      alerts_enabled=True))
    assert job is not None
    with caplog.at_level("INFO"):
        job.register(BackgroundScheduler(timezone=timezone.utc))
    off_lines = [record.getMessage() for record in caplog.records if "Telegram OFF" in record.getMessage()]
    assert len(off_lines) == 1
    assert "MENTHORQ_COMMANDS_ENABLED=1" in off_lines[0] and "MENTHORQ_ALERTS_ENABLED" not in off_lines[0]


def test_both_switches_on_log_no_off_line(caplog):
    job = build_menthorq_monitor_job(repository=MagicMock(), settings=ALL_ON, telegram=FakeTelegram())
    assert job is not None
    with caplog.at_level("INFO"):
        job.register(BackgroundScheduler(timezone=timezone.utc))
    assert "Telegram OFF" not in caplog.text


def test_commands_enabled_without_owner_chat_schedule_no_polling_and_warn_once(caplog):
    with caplog.at_level("INFO"):
        job = build_menthorq_monitor_job(repository=MagicMock(), settings=ALL_ON,
                                         telegram=FakeTelegram(chat_id=""))
        assert job is not None and job.command_listener is None
        scheduler = BackgroundScheduler(timezone=timezone.utc)
        job.register(scheduler)
        for _ in range(3):
            job.poll_commands()
    assert COMMAND_POLL_JOB_ID not in {scheduled.id for scheduled in scheduler.get_jobs()}
    warnings = [record for record in caplog.records if record.levelname == "WARNING"]
    assert len(warnings) == 1 and "no owner chat id" in warnings[0].getMessage()
    assert "Telegram OFF" not in caplog.text      # the switch is on; the warning names the cause


def test_owner_user_id_is_read_and_validated():
    assert MenthorqMonitorSettings.from_environment({}).owner_user_id is None
    assert MenthorqMonitorSettings.from_environment({"MENTHORQ_OWNER_USER_ID": " 111 "}).owner_user_id == 111
    for invalid in ("abc", "-5", "0", "1.5"):
        with pytest.raises(ValueError, match="MENTHORQ_OWNER_USER_ID"):
            MenthorqMonitorSettings.from_environment({"MENTHORQ_OWNER_USER_ID": invalid})


def test_group_chat_without_owner_user_id_schedules_no_polling_and_warns_once(caplog):
    with caplog.at_level("INFO"):
        job = build_menthorq_monitor_job(repository=MagicMock(), settings=ALL_ON,
                                         telegram=FakeTelegram(chat_id="-1001234"))
        assert job is not None and job.command_listener is None
        scheduler = BackgroundScheduler(timezone=timezone.utc)
        job.register(scheduler)
    assert COMMAND_POLL_JOB_ID not in {scheduled.id for scheduled in scheduler.get_jobs()}
    warnings = [record for record in caplog.records if record.levelname == "WARNING"]
    assert len(warnings) == 1 and "MENTHORQ_OWNER_USER_ID" in warnings[0].getMessage()


def test_group_chat_with_owner_user_id_schedules_polling():
    settings = MenthorqMonitorSettings(monitor_enabled=True, alerts_enabled=True, commands_enabled=True,
                                       owner_user_id=111)
    job = build_menthorq_monitor_job(repository=MagicMock(), settings=settings,
                                     telegram=FakeTelegram(chat_id="-1001234"))
    assert job is not None and job.command_listener is not None


def test_jobs_registered_with_utc_cron_triggers():
    job = build_menthorq_monitor_job(repository=MagicMock(), settings=ALL_ON,
                                     telegram=FakeTelegram())
    assert job is not None
    scheduler = BackgroundScheduler(timezone=timezone.utc)
    job.register(scheduler)
    jobs = {scheduled.id: scheduled for scheduled in scheduler.get_jobs()}
    assert set(jobs) == {HOURLY_JOB_ID, WEEKLY_BRIEF_JOB_ID, COMMAND_POLL_JOB_ID}
    hourly = {field.name: str(field) for field in jobs[HOURLY_JOB_ID].trigger.fields}
    assert hourly["minute"] == "10" and hourly["hour"] == "*"
    weekly = {field.name: str(field) for field in jobs[WEEKLY_BRIEF_JOB_ID].trigger.fields}
    assert (weekly["day_of_week"], weekly["hour"], weekly["minute"]) == ("fri", "9", "0")
    assert str(jobs[HOURLY_JOB_ID].trigger.timezone) == "UTC"
    assert jobs[HOURLY_JOB_ID].max_instances == 1


def test_commands_disabled_registers_no_polling():
    job = build_menthorq_monitor_job(repository=MagicMock(), telegram=FakeTelegram(),
                                     settings=MONITOR_ONLY)
    assert job is not None
    scheduler = BackgroundScheduler(timezone=timezone.utc)
    job.register(scheduler)
    assert COMMAND_POLL_JOB_ID not in {scheduled.id for scheduled in scheduler.get_jobs()}


def test_hourly_steps_are_isolated():
    monitor = MagicMock()
    monitor.run_live.side_effect = RuntimeError("database down")
    delivery = MagicMock()
    job = MenthorqMonitorJob(ALL_ON, monitor, delivery, clock=lambda: BASE_TIME)
    job.run_hourly()                                   # does not raise
    monitor.run_daily_close_if_missing.assert_called_once_with(BASE_TIME)
    delivery.send_urgent_alerts.assert_not_called()    # no live record -> no alert evaluation
    delivery.send_weekly_brief_if_due.assert_called_once_with(BASE_TIME)


def test_alerts_disabled_sends_nothing():
    monitor, delivery = MagicMock(), MagicMock()
    job = MenthorqMonitorJob(MONITOR_ONLY, monitor, delivery,
                             clock=lambda: BASE_TIME)
    job.run_hourly()
    job.run_weekly_brief()
    monitor.run_live.assert_called_once()
    delivery.send_urgent_alerts.assert_not_called()
    delivery.send_weekly_brief_if_due.assert_not_called()


class TestMonitorErrorAlertFromTheHourlyRun:
    """run_live (or yesterday's daily close) raising sends one monitor_error alert."""

    def test_live_failure_sends_one_alert_naming_the_error(self):
        monitor, delivery = MagicMock(), MagicMock()
        error = RuntimeError("database down")
        monitor.run_live.side_effect = error
        MenthorqMonitorJob(ALL_ON, monitor, delivery, clock=lambda: BASE_TIME).run_hourly()
        delivery.send_monitor_error_alert.assert_called_once_with(error, BASE_TIME)

    def test_both_failing_send_one_alert_with_the_live_error(self):
        monitor, delivery = MagicMock(), MagicMock()
        live_error, daily_error = RuntimeError("live"), RuntimeError("daily")
        monitor.run_live.side_effect = live_error
        monitor.run_daily_close_if_missing.side_effect = daily_error
        MenthorqMonitorJob(ALL_ON, monitor, delivery, clock=lambda: BASE_TIME).run_hourly()
        delivery.send_monitor_error_alert.assert_called_once_with(live_error, BASE_TIME)

    def test_daily_close_failure_alone_sends_the_alert_and_the_live_alerts_still_run(self):
        monitor, delivery = MagicMock(), MagicMock()
        daily_error = RuntimeError("daily")
        monitor.run_daily_close_if_missing.side_effect = daily_error
        MenthorqMonitorJob(ALL_ON, monitor, delivery, clock=lambda: BASE_TIME).run_hourly()
        delivery.send_monitor_error_alert.assert_called_once_with(daily_error, BASE_TIME)
        delivery.send_urgent_alerts.assert_called_once()

    def test_no_error_or_alerts_disabled_sends_no_error_alert(self):
        monitor, delivery = MagicMock(), MagicMock()
        MenthorqMonitorJob(ALL_ON, monitor, delivery, clock=lambda: BASE_TIME).run_hourly()
        delivery.send_monitor_error_alert.assert_not_called()
        monitor.run_live.side_effect = RuntimeError("down")
        MenthorqMonitorJob(MONITOR_ONLY, monitor, delivery, clock=lambda: BASE_TIME).run_hourly()
        delivery.send_monitor_error_alert.assert_not_called()

    def test_a_raising_alert_path_does_not_stop_the_other_steps(self):
        monitor, delivery = MagicMock(), MagicMock()
        monitor.run_live.side_effect = RuntimeError("down")
        delivery.send_monitor_error_alert.side_effect = RuntimeError("alert broken")
        MenthorqMonitorJob(ALL_ON, monitor, delivery, clock=lambda: BASE_TIME).run_hourly()
        delivery.send_weekly_brief_if_due.assert_called_once_with(BASE_TIME)

    def test_real_delivery_with_a_raising_live_record_sends_one_alert(self, monkeypatch):
        repository, telegram = FakeMonitorRepository(), FakeTelegram()
        monitor = MenthorqMonitorService(repository=repository, settings=ALL_ON,
                                         input_loader=FakeInputLoader())
        delivery = MenthorqBriefDeliveryService(monitor, telegram, clock=lambda: BASE_TIME)

        def raise_replication_error(_now):
            raise ValueError("replication failed")
        monkeypatch.setattr(monitor, "run_live", raise_replication_error)
        job = MenthorqMonitorJob(ALL_ON, monitor, delivery, clock=lambda: BASE_TIME)
        job.run_hourly()
        job.run_hourly()                              # same hour again: cooldown, no second alert
        error_messages = [sent["message"] for sent in telegram.sent if "Monitor error" in sent["message"]]
        assert error_messages == ["<b>Monitor error:</b> the hourly computation failed: ValueError "
                                  "(details: see the collector log). Levels and alerts are paused "
                                  "until it succeeds."]

    def test_the_log_keeps_the_full_exception_with_secrets_scrubbed(self, monkeypatch, caplog):
        telegram = FakeTelegram()
        delivery = MagicMock()
        delivery.telegram = telegram
        monitor = MagicMock()
        monitor.run_live.side_effect = RuntimeError('could not connect to host "db.internal" token fake-token')
        with caplog.at_level("INFO"):
            MenthorqMonitorJob(ALL_ON, monitor, delivery, clock=lambda: BASE_TIME).run_hourly()
        errors = [record.getMessage() for record in caplog.records if record.levelname == "ERROR"]
        assert len(errors) == 1
        assert errors[0].startswith("MenthorQ monitor: live record failed\nTraceback")
        assert 'RuntimeError: could not connect to host "db.internal" token <bot token>' in errors[0]
        assert "fake-token" not in caplog.text


class TestMonitorNotStartedNotice:
    """start_menthorq_monitor_job: a bad setting keeps the collector running and tells the owner once."""

    def test_a_bad_weekly_brief_day_logs_an_error_and_sends_one_notice(self, monkeypatch, caplog):
        monkeypatch.setenv("MENTHORQ_WEEKLY_BRIEF_DAY", "frii")
        telegram = FakeTelegram()
        with caplog.at_level("INFO"):
            job = start_menthorq_monitor_job(repository=MagicMock(), telegram=telegram)
        assert job is None
        assert telegram.sent == [{
            "message": ("<b>MenthorQ monitor NOT started:</b> MENTHORQ_WEEKLY_BRIEF_DAY='frii' is not one "
                        "of ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'). "
                        "The collector itself keeps running."),
            "parse_mode": "HTML"}]
        errors = [record for record in caplog.records if record.levelname == "ERROR"]
        assert len(errors) == 1 and "MenthorQ monitor NOT started" in errors[0].getMessage()

    def test_no_notice_without_telegram_credentials(self, monkeypatch, caplog):
        monkeypatch.setenv("MENTHORQ_WEEKLY_BRIEF_DAY", "frii")
        telegram = FakeTelegram()
        telegram._token = ""
        with caplog.at_level("INFO"):
            assert start_menthorq_monitor_job(repository=MagicMock(), telegram=telegram) is None
        assert telegram.sent == []
        assert "not-started notice NOT sent" in caplog.text

    def test_a_construction_error_sends_only_its_class_and_a_failing_send_is_swallowed(
            self, monkeypatch, caplog):
        def raise_construction_error(**_keyword_arguments):
            raise RuntimeError('connection to server at "10.0.0.5" failed for user "collector" '
                               'database "option_trading", bot fake-token')
        monkeypatch.setattr(
            "coding.service.on_chain.menthorq_monitor.menthorq_monitor_job.build_menthorq_monitor_job",
            raise_construction_error)
        telegram = FakeTelegram(accept=False)
        with caplog.at_level("INFO"):
            assert start_menthorq_monitor_job(repository=MagicMock(), telegram=telegram) is None
        assert telegram.sent[0]["message"] == ("<b>MenthorQ monitor NOT started:</b> RuntimeError "
                                               "(details: see the collector log). "
                                               "The collector itself keeps running.")
        assert "not-started notice NOT delivered" in caplog.text
        # The log has the full exception, the bot token scrubbed.
        errors = [record.getMessage() for record in caplog.records if record.levelname == "ERROR"]
        assert len(errors) == 1 and "MenthorQ monitor NOT started" in errors[0]
        assert 'user "collector" database "option_trading", bot <bot token>' in errors[0]
        assert "fake-token" not in caplog.text

    def test_a_settings_error_message_reaches_telegram_in_full(self, monkeypatch):
        monkeypatch.setenv("MENTHORQ_STALE_SNAPSHOT_MINUTES", "9")
        telegram = FakeTelegram()
        assert start_menthorq_monitor_job(repository=MagicMock(), telegram=telegram) is None
        assert telegram.sent[0]["message"] == ("<b>MenthorQ monitor NOT started:</b> "
                                               "MENTHORQ_STALE_SNAPSHOT_MINUTES=9 is outside 10..None. "
                                               "The collector itself keeps running.")

    def test_valid_settings_send_nothing(self, monkeypatch):
        monkeypatch.setenv("MENTHORQ_MONITOR_ENABLED", "0")
        telegram = FakeTelegram()
        assert start_menthorq_monitor_job(repository=MagicMock(), telegram=telegram) is None
        assert telegram.sent == []


class TestMonitorTablesCheckedAtStartUp:
    """An enabled monitor whose tables are missing (031/032 not applied) is NOT started."""

    def test_missing_tables_log_an_error_send_one_notice_and_register_no_job(self, monkeypatch, caplog):
        monkeypatch.setenv("MENTHORQ_MONITOR_ENABLED", "1")
        repository, telegram = FakeMonitorRepository(), FakeTelegram()
        repository.absent_tables = ["menthorq_alert_deliveries", "menthorq_monitor_state"]
        with caplog.at_level("INFO"):
            assert start_menthorq_monitor_job(repository=repository, telegram=telegram) is None
        assert telegram.sent == [{
            "message": ("<b>MenthorQ monitor NOT started:</b> database table(s) missing: "
                        "menthorq_alert_deliveries (migration 031), menthorq_monitor_state (migration 032); "
                        "apply migrations 031 and 032, then restart the collector. "
                        "The collector itself keeps running."),
            "parse_mode": "HTML"}]
        errors = [record.getMessage() for record in caplog.records if record.levelname == "ERROR"]
        assert len(errors) == 1 and "MenthorQ monitor NOT started" in errors[0]
        assert "menthorq_monitor_state" in errors[0]

    def test_all_tables_present_starts_the_monitor_and_sends_nothing(self, monkeypatch):
        monkeypatch.setenv("MENTHORQ_MONITOR_ENABLED", "1")
        telegram = FakeTelegram()
        job = start_menthorq_monitor_job(repository=FakeMonitorRepository(), telegram=telegram)
        assert job is not None and telegram.sent == []

    def test_a_failing_check_is_logged_and_the_monitor_still_starts(self, monkeypatch, caplog):
        monkeypatch.setenv("MENTHORQ_MONITOR_ENABLED", "1")
        repository, telegram = FakeMonitorRepository(), FakeTelegram()
        repository.fail_table_check = True
        with caplog.at_level("INFO"):
            job = start_menthorq_monitor_job(repository=repository, telegram=telegram)
        assert job is not None and telegram.sent == []
        assert "start-up check of the monitor tables failed" in caplog.text

    def test_monitor_off_runs_no_table_check(self, monkeypatch):
        monkeypatch.setenv("MENTHORQ_MONITOR_ENABLED", "0")
        repository = MagicMock()
        assert start_menthorq_monitor_job(repository=repository, telegram=FakeTelegram()) is None
        repository.missing_tables.assert_not_called()


def test_collector_and_monitor_import_graph_has_no_matplotlib():
    code = ("import sys\n"
            "import coding.service.data_collection.unified_scheduler\n"
            "import coding.service.on_chain.menthorq_monitor.menthorq_monitor_job\n"
            "import scripts.menthorq_brief\n"
            "loaded = sorted(name for name in sys.modules if name.split('.')[0] == 'matplotlib')\n"
            "assert not loaded, loaded\n")
    result = subprocess.run([sys.executable, "-c", code], cwd=REPOSITORY_ROOT,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
