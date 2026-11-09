"""
The MenthorQ-style monitor's jobs inside the collector (unified_scheduler).

Registered on the collector's APScheduler only with
MENTHORQ_MONITOR_ENABLED=1 (all times UTC; the weekly brief only with
MENTHORQ_ALERTS_ENABLED=1, the polling only with MENTHORQ_COMMANDS_ENABLED=1;
all three off by default, the VPS .env sets them):
  - ``menthorq_monitor_hourly``  every hour at MENTHORQ_LIVE_RUN_MINUTE: store
    the live record, store the missing or not-computed daily-close records
    of the last 7 ended days, send urgent alerts, catch up a missed weekly
    brief.
  - ``menthorq_weekly_brief``    MENTHORQ_WEEKLY_BRIEF_DAY at
    MENTHORQ_WEEKLY_BRIEF_TIME_UTC.
  - ``menthorq_command_poll``    every MENTHORQ_COMMAND_POLL_SECONDS: the bot commands
    (/brief /levels /changes /status /mute /help, see bot_command_service).

Every step is isolated: a failure is logged and never stops the collector or
the next step. When the live record or yesterday's daily close raised (and
alerts are enabled), the owner also gets one ``monitor_error`` alert (12 h
cooldown, muted by /mute), so a persistent failure is not silent. Numbers
only; nothing on this path imports matplotlib.
"""

import logging
from datetime import datetime, timezone
from typing import Callable, List, Optional, Sequence

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from coding.core.database.repository import DatabaseRepository
from coding.service.on_chain.menthorq_monitor.bot_command_service import MenthorqBotCommandService
from coding.service.on_chain.menthorq_monitor.brief_delivery_service import (
    TELEGRAM_PARSE_MODE,
    MenthorqBriefDeliveryService,
    error_text_for_telegram,
    scrubbed_traceback,
)
from coding.service.on_chain.menthorq_monitor.brief_formatter import format_monitor_not_started_notice
from coding.service.on_chain.menthorq_monitor.command_formatter import BOT_COMMANDS
from coding.service.on_chain.menthorq_monitor.menthorq_monitor_service import MenthorqMonitorService
from coding.service.on_chain.menthorq_monitor.monitor_settings import MenthorqMonitorSettings
from coding.service.on_chain.menthorq_monitor.telegram_command_listener import (
    TelegramCommandListener,
    resolve_owner_user_id,
)
from coding.service.notifications.telegram_alert_service import TelegramAlertService

logger = logging.getLogger(__name__)

HOURLY_JOB_ID = "menthorq_monitor_hourly"
WEEKLY_BRIEF_JOB_ID = "menthorq_weekly_brief"
COMMAND_POLL_JOB_ID = "menthorq_command_poll"
MISFIRE_GRACE_SECONDS = 600
# Table -> the migration that creates it (checked at start-up, missing_monitor_tables).
MONITOR_TABLE_MIGRATIONS = {
    "menthorq_level_snapshots": "031",
    "menthorq_alert_deliveries": "031",
    "menthorq_monitor_state": "032",
}


def log_scrubbed_exception(message: str, error: BaseException, telegram: TelegramAlertService) -> None:
    """
    Log ``message`` at ERROR with the full traceback of ``error``, the bot
    token and database password scrubbed. Never raises: when the scrubbing
    itself fails, the traceback is logged as it is.
    """
    try:
        text = scrubbed_traceback(error, telegram)
    except Exception:
        logger.error(message, exc_info=error)
        return
    logger.error("%s\n%s", message, text)


class MenthorqMonitorJob:
    """Runs the monitor steps; ``register`` adds them to a scheduler."""

    def __init__(
        self,
        settings: MenthorqMonitorSettings,
        monitor_service: MenthorqMonitorService,
        delivery_service: MenthorqBriefDeliveryService,
        command_listener: Optional[TelegramCommandListener] = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        """
        Args:
            settings: Monitor settings.
            monitor_service: Computes and stores the records.
            delivery_service: Sends alerts and briefs.
            command_listener: Bot command polling; None when commands are disabled.
            clock: Wall clock (UTC-aware).
        """
        self.settings = settings
        self.monitor_service = monitor_service
        self.delivery_service = delivery_service
        self.command_listener = command_listener
        self.clock = clock

    def run_hourly(self) -> None:
        """
        Live record, daily-close catch-up, urgent alerts, weekly-brief
        catch-up. When the live record or yesterday's daily close raised,
        one ``monitor_error`` alert names the first error (alerts enabled
        only; cooldown and mute as for the other urgent alerts).
        """
        now = self.clock()
        live = None
        computation_error: Optional[Exception] = None
        try:
            live = self.monitor_service.run_live(now)
        except Exception as error:
            log_scrubbed_exception("MenthorQ monitor: live record failed", error,
                                   self.delivery_service.telegram)
            computation_error = error
        try:
            self.monitor_service.run_daily_close_if_missing(now)
        except Exception as error:
            log_scrubbed_exception("MenthorQ monitor: daily-close record failed", error,
                                   self.delivery_service.telegram)
            computation_error = computation_error or error
        if not self.settings.alerts_enabled:
            return
        if computation_error is not None:
            try:
                self.delivery_service.send_monitor_error_alert(computation_error, now)
            except Exception:
                logger.exception("MenthorQ monitor: monitor-error alert failed")
        if live is not None:
            try:
                self.delivery_service.send_urgent_alerts(live, now)
            except Exception:
                logger.exception("MenthorQ monitor: urgent alerts failed")
        try:
            self.delivery_service.send_weekly_brief_if_due(now)
        except Exception:
            logger.exception("MenthorQ monitor: weekly brief catch-up failed")

    def run_weekly_brief(self) -> None:
        """The scheduled weekly brief (skipped when already delivered)."""
        if not self.settings.alerts_enabled:
            return
        try:
            self.delivery_service.send_weekly_brief_if_due(self.clock())
        except Exception:
            logger.exception("MenthorQ monitor: weekly brief failed")

    def poll_commands(self) -> None:
        """One getUpdates poll (never raises)."""
        if self.command_listener is None:
            return
        try:
            self.command_listener.poll_once()
        except Exception:
            logger.exception("MenthorQ monitor: command polling failed")

    def register(self, scheduler) -> None:
        """Add the jobs to an APScheduler scheduler (UTC triggers, one instance each)."""
        settings = self.settings
        scheduler.add_job(
            func=self.run_hourly,
            trigger=CronTrigger(minute=settings.live_run_minute, timezone=timezone.utc),
            id=HOURLY_JOB_ID, name="MenthorQ-style levels (hourly live + daily close)",
            replace_existing=True, max_instances=1, coalesce=True,
            misfire_grace_time=MISFIRE_GRACE_SECONDS,
        )
        if settings.alerts_enabled:
            scheduler.add_job(
                func=self.run_weekly_brief,
                trigger=CronTrigger(day_of_week=settings.weekly_brief_day_name,
                                    hour=settings.weekly_brief_time_utc.hour,
                                    minute=settings.weekly_brief_time_utc.minute,
                                    timezone=timezone.utc),
                id=WEEKLY_BRIEF_JOB_ID, name="MenthorQ-style weekly brief",
                replace_existing=True, max_instances=1, coalesce=True,
                misfire_grace_time=MISFIRE_GRACE_SECONDS,
            )
            logger.info("[OK] MenthorQ monitor scheduled: hourly at :%02d UTC, weekly brief %s %s UTC",
                        settings.live_run_minute, settings.weekly_brief_day_name,
                        settings.weekly_brief_time_utc.strftime("%H:%M"))
        else:
            logger.info("[OK] MenthorQ monitor scheduled: hourly at :%02d UTC (records only)",
                        settings.live_run_minute)
        switched_off = []
        if not settings.alerts_enabled:
            switched_off.append("urgent alerts and weekly brief (set MENTHORQ_ALERTS_ENABLED=1)")
        if not settings.commands_enabled:
            switched_off.append("bot commands (set MENTHORQ_COMMANDS_ENABLED=1)")
        if switched_off:
            logger.info("MenthorQ monitor: Telegram OFF for %s; records are still computed and "
                        "stored. Only the production collector should enable these.",
                        " and ".join(switched_off))
        if self.command_listener is not None:
            scheduler.add_job(
                func=self.poll_commands,
                trigger=IntervalTrigger(seconds=settings.command_poll_seconds),
                id=COMMAND_POLL_JOB_ID, name="MenthorQ bot command polling",
                replace_existing=True, max_instances=1, coalesce=True,
            )
            logger.info("[OK] MenthorQ bot command polling every %d s",
                        settings.command_poll_seconds)


def build_menthorq_monitor_job(
    repository: Optional[DatabaseRepository] = None,
    settings: Optional[MenthorqMonitorSettings] = None,
    telegram: Optional[TelegramAlertService] = None,
) -> Optional[MenthorqMonitorJob]:
    """
    The job with its services, or None unless MENTHORQ_MONITOR_ENABLED=1
    (off by default; logged in one INFO line).

    Raises:
        MonitorSettingsError: when a MENTHORQ_* setting is invalid.
    """
    settings = settings or MenthorqMonitorSettings.from_environment()
    if not settings.monitor_enabled:
        logger.info("MenthorQ monitor OFF: no record is computed or stored and nothing is sent "
                    "(set MENTHORQ_MONITOR_ENABLED=1, production collector only)")
        return None
    telegram = telegram or TelegramAlertService()
    monitor_service = MenthorqMonitorService(repository=repository, settings=settings)
    delivery_service = MenthorqBriefDeliveryService(monitor_service, telegram)
    listener = None
    if settings.commands_enabled and not telegram.chat_id.strip():
        logger.warning("MenthorQ monitor: MENTHORQ_COMMANDS_ENABLED=1 but no owner chat id "
                       "(OSF_TELEGRAM_CHAT_ID) is configured; bot command polling NOT scheduled")
    elif settings.commands_enabled and resolve_owner_user_id(telegram.chat_id, settings.owner_user_id) is None:
        logger.warning("MenthorQ monitor: MENTHORQ_COMMANDS_ENABLED=1 but the owner chat %s is a group "
                       "chat and no owner user id (MENTHORQ_OWNER_USER_ID) is configured; bot command "
                       "polling NOT scheduled", telegram.chat_id.strip())
    elif settings.commands_enabled:
        command_service = MenthorqBotCommandService(monitor_service, delivery_service, telegram)
        listener = TelegramCommandListener(
            telegram, telegram.chat_id, command_service.handlers(),
            unknown_command_handler=command_service.reply_to_unknown_command,
            command_menu=BOT_COMMANDS,
            configured_owner_user_id=settings.owner_user_id,
        )
    return MenthorqMonitorJob(settings, monitor_service, delivery_service, listener)


def missing_monitor_tables(repository: DatabaseRepository) -> List[str]:
    """
    The monitor tables (``MONITOR_TABLE_MIGRATIONS``) missing from the
    database, e.g. migration 031 or 032 not applied. When the check itself
    fails it is logged and treated as nothing missing: a failing check must
    not stop the monitor (the hourly run then reports the real error).
    """
    try:
        return list(repository.missing_tables(list(MONITOR_TABLE_MIGRATIONS)))
    except Exception:
        logger.exception("MenthorQ monitor: the start-up check of the monitor tables failed; "
                         "the monitor is started anyway")
        return []


def missing_tables_text(missing: Sequence[str]) -> str:
    """The not-started reason for missing tables, naming each table and its migration."""
    named = ", ".join(f"{table} (migration {MONITOR_TABLE_MIGRATIONS[table]})" for table in missing)
    return (f"database table(s) missing: {named}; apply migrations 031 and 032, "
            "then restart the collector")


def _send_not_started_notice(telegram: Optional[TelegramAlertService], reason: str) -> None:
    """ONE best-effort not-started notice (``reason`` must be free of secrets); never raises."""
    try:
        telegram = telegram or TelegramAlertService()
        if not telegram.has_credentials:
            logger.warning("MenthorQ monitor: no Telegram credentials; not-started notice NOT sent")
            return
        if not telegram.send(format_monitor_not_started_notice(reason), parse_mode=TELEGRAM_PARSE_MODE):
            logger.warning("MenthorQ monitor: not-started notice NOT delivered (Telegram send failed)")
    except Exception:
        logger.exception("MenthorQ monitor: the not-started notice itself failed")


def start_menthorq_monitor_job(
    repository: Optional[DatabaseRepository] = None,
    telegram: Optional[TelegramAlertService] = None,
) -> Optional[MenthorqMonitorJob]:
    """
    ``build_menthorq_monitor_job`` for the collector: a bad MENTHORQ_*
    setting (``MonitorSettingsError``) or a construction error never stops
    the collector, and neither do missing monitor tables (migration 031 or
    032 not applied; ``missing_monitor_tables``, checked once here so the
    hourly job does not raise every hour). In each case the monitor is NOT
    started: the cause is logged at ERROR (full traceback, secrets
    scrubbed) and the owner gets ONE best-effort Telegram notice
    (``format_monitor_not_started_notice``; a settings error's message in
    full, any other exception by class name only,
    ``error_text_for_telegram``) when the Telegram credentials exist; a failing notice
    is logged and swallowed, and nothing is sent without credentials.

    Args:
        repository: Database access shared with the collector.
        telegram: Sender; a default ``TelegramAlertService`` (credentials
            from the environment) when omitted.

    Returns:
        The job, or None when the monitor is off or could not be started.
    """
    try:
        job = build_menthorq_monitor_job(repository=repository, telegram=telegram)
    except Exception as error:
        message = ("MenthorQ monitor NOT started (configuration or construction error); "
                   "the collector keeps running")
        try:
            telegram = telegram or TelegramAlertService()
            reason = error_text_for_telegram(error, telegram)
        except Exception:
            logger.error(message, exc_info=error)
            logger.exception("MenthorQ monitor: the not-started notice itself failed")
            return None
        log_scrubbed_exception(message, error, telegram)
        _send_not_started_notice(telegram, reason)
        return None
    if job is None:
        return None
    missing = missing_monitor_tables(job.monitor_service.repository)
    if missing:
        reason = missing_tables_text(missing)
        logger.error("MenthorQ monitor NOT started: %s; the collector keeps running", reason)
        _send_not_started_notice(job.delivery_service.telegram, reason)
        return None
    return job
