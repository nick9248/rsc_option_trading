"""
Monitor settings from the environment, job registration on APScheduler (not
started), step isolation of the hourly run, and the import graph (no
matplotlib on the collector path).
"""

import subprocess
import sys
from datetime import time, timezone
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
])
def test_invalid_values_raise_instead_of_falling_back(name, value):
    with pytest.raises(ValueError, match=name):
        MenthorqMonitorSettings.from_environment({name: value})


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
