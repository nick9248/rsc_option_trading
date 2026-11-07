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
)
from coding.core.analytics.menthorq_monitor.alert_rules import AlertThresholds
from coding.service.on_chain.menthorq_monitor.brief_formatter import DEFAULT_STALE_SNAPSHOT_LIMIT
from coding.service.on_chain.menthorq_monitor.command_formatter import BOT_COMMANDS
from coding.service.on_chain.menthorq_monitor.monitor_settings import MenthorqMonitorSettings
from tests.fakes.fake_menthorq_monitor import BASE_TIME, FakeTelegram

REPOSITORY_ROOT = Path(__file__).parents[3]


def test_defaults_when_nothing_is_set():
    settings = MenthorqMonitorSettings.from_environment({})
    assert settings == MenthorqMonitorSettings()
    assert settings.weekly_brief_day_name == "fri"
    assert settings.weekly_brief_time_utc == time(9, 0)
    assert settings.expiry_universe == "monthly"


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
    settings = MenthorqMonitorSettings.from_environment({"MENTHORQ_ALERT_SPOT_CROSSES_HVL": "1"})
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


def test_disabled_monitor_builds_no_job():
    assert build_menthorq_monitor_job(repository=MagicMock(),
                                      settings=MenthorqMonitorSettings(monitor_enabled=False)) is None


def test_jobs_registered_with_utc_cron_triggers():
    job = build_menthorq_monitor_job(repository=MagicMock(), settings=MenthorqMonitorSettings(),
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
                                     settings=MenthorqMonitorSettings(commands_enabled=False))
    assert job is not None
    scheduler = BackgroundScheduler(timezone=timezone.utc)
    job.register(scheduler)
    assert COMMAND_POLL_JOB_ID not in {scheduled.id for scheduled in scheduler.get_jobs()}


def test_hourly_steps_are_isolated():
    monitor = MagicMock()
    monitor.run_live.side_effect = RuntimeError("database down")
    delivery = MagicMock()
    job = MenthorqMonitorJob(MenthorqMonitorSettings(), monitor, delivery, clock=lambda: BASE_TIME)
    job.run_hourly()                                   # does not raise
    monitor.run_daily_close_if_missing.assert_called_once_with(BASE_TIME)
    delivery.send_urgent_alerts.assert_not_called()    # no live record -> no alert evaluation
    delivery.send_weekly_brief_if_due.assert_called_once_with(BASE_TIME)


def test_alerts_disabled_sends_nothing():
    monitor, delivery = MagicMock(), MagicMock()
    job = MenthorqMonitorJob(MenthorqMonitorSettings(alerts_enabled=False), monitor, delivery,
                             clock=lambda: BASE_TIME)
    job.run_hourly()
    job.run_weekly_brief()
    monitor.run_live.assert_called_once()
    delivery.send_urgent_alerts.assert_not_called()
    delivery.send_weekly_brief_if_due.assert_not_called()


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
