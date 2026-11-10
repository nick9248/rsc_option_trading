"""
The monitor's bot commands: /brief, /levels, /changes, /status, /mute,
/help and the hint for an unknown command. Each handler builds its reply
from the stored records (command_formatter; /brief from the delivery
service) and sends it to the owner chat with ``TelegramAlertService`` (one
attempt, never raises).

Replies are not written to ``menthorq_alert_deliveries`` (they are answers,
not alerts), except /brief, which is recorded as ``on_demand_brief``.
Replies are never muted: /mute only silences urgent alerts.
A reply that cannot be built (e.g. the database is down) is logged and
answered with a short "failed" line instead of silence.
"""

import logging
from datetime import datetime, timezone
from typing import Callable, Dict, Optional

from coding.core.analytics.menthorq_monitor.alert_mute import (
    MenthorqMonitorState,
    parse_mute_duration,
)
from coding.core.analytics.menthorq_monitor.alert_rules import URGENT_ALERT_KEYS
from coding.core.analytics.menthorq_monitor.level_record import MODE_DAILY_CLOSE, MODE_LIVE
from coding.service.on_chain.menthorq_monitor.brief_delivery_service import (
    TELEGRAM_PARSE_MODE,
    MenthorqBriefDeliveryService,
)
from coding.service.on_chain.menthorq_monitor.brief_formatter import fit_to_telegram_limit
from coding.service.on_chain.menthorq_monitor.command_formatter import (
    MonitorStatus,
    format_changes_reply,
    format_command_failed_reply,
    format_help_reply,
    format_levels_reply,
    format_mute_cleared_reply,
    format_mute_error_reply,
    format_mute_set_reply,
    format_mute_state_reply,
    format_status_reply,
    format_unknown_command_reply,
)
from coding.service.on_chain.menthorq_monitor.menthorq_monitor_service import MenthorqMonitorService
from coding.service.on_chain.menthorq_monitor.telegram_command_listener import CommandHandler
from coding.service.notifications.telegram_alert_service import TelegramAlertService

logger = logging.getLogger(__name__)


class MenthorqBotCommandService:
    """Builds and sends the replies of the monitor's bot commands."""

    def __init__(
        self,
        monitor_service: MenthorqMonitorService,
        delivery_service: MenthorqBriefDeliveryService,
        telegram: TelegramAlertService,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        """
        Args:
            monitor_service: Stored records and comparisons.
            delivery_service: The /brief reply and the alert thresholds.
            telegram: Sender (its chat is the owner chat).
            clock: Wall clock (UTC-aware).
        """
        self.monitor_service = monitor_service
        self.delivery_service = delivery_service
        self.repository = monitor_service.repository
        self.settings = monitor_service.settings
        self.telegram = telegram
        self.clock = clock

    def handlers(self) -> Dict[str, CommandHandler]:
        """Command -> handler, for ``TelegramCommandListener``."""
        return {
            "/brief": lambda arguments: self.reply(
                "/brief", self.delivery_service.build_on_demand_brief,
                send=self.delivery_service.deliver_on_demand_brief),
            "/levels": lambda arguments: self.reply("/levels", self.build_levels_reply),
            "/changes": lambda arguments: self.reply("/changes", self.build_changes_reply),
            "/status": lambda arguments: self.reply("/status", self.build_status_reply),
            "/mute": lambda arguments: self.reply("/mute", lambda: self.apply_mute(arguments)),
            "/help": lambda arguments: self.reply("/help", self.build_help_reply),
        }

    def reply_to_unknown_command(self, command: str) -> bool:
        """The short help hint for a command the bot does not know."""
        return self.reply(command, lambda: format_unknown_command_reply(command))

    def reply(
        self, command: str, build: Callable[[], str],
        send: Optional[Callable[[str], bool]] = None,
    ) -> bool:
        """
        Build a reply and send it; a build error is logged and answered
        with a "failed" line. Every reply passes ``fit_to_telegram_limit``.

        Args:
            command: The command, for the log and the "failed" line.
            build: Builds the reply text.
            send: Sends a successfully built reply (e.g. /brief, which also
                records the delivery); a plain Telegram send when omitted.
                The "failed" line is always sent plainly.

        Returns:
            True when Telegram accepted the reply.
        """
        try:
            message = build()
        except Exception:
            logger.exception("MenthorQ monitor: reply to %s could not be built", command)
            message = format_command_failed_reply(command)
            send = None
        message = fit_to_telegram_limit(message)
        if send is None:
            accepted = self.telegram.send(message, parse_mode=TELEGRAM_PARSE_MODE)
        else:
            accepted = send(message)
        if not accepted:
            logger.warning("MenthorQ monitor: reply to %s NOT delivered (Telegram send failed)", command)
        return accepted

    def build_levels_reply(self) -> str:
        """/levels from the newest stored computed live record."""
        now = self.clock()
        return format_levels_reply(self.settings.currency,
                                   self.monitor_service.latest_record(MODE_LIVE, now),
                                   stale_limit=self.settings.stale_snapshot_limit, now=now)

    def build_changes_reply(self) -> str:
        """/changes of the newest stored computed live record."""
        now = self.clock()
        latest = self.monitor_service.latest_record(MODE_LIVE, now)
        comparisons = self.monitor_service.build_comparisons(latest) if latest is not None else []
        return format_changes_reply(self.settings.currency, latest, comparisons,
                                    stale_limit=self.settings.stale_snapshot_limit, now=now)

    def build_status_reply(self) -> str:
        """/status: runs, newest snapshot in the database, data the last record used, health and alert state."""
        settings = self.settings
        repository = self.repository
        state: Optional[MenthorqMonitorState] = None
        state_error: Optional[str] = None
        try:
            state = repository.get_menthorq_monitor_state(settings.currency)
        except Exception as error:
            logger.exception("MenthorQ monitor: mute state not readable for /status")
            state_error = type(error).__name__
        now = self.clock()
        status = MonitorStatus(
            currency=settings.currency,
            now=now,
            latest_live_run=repository.get_latest_menthorq_level_run(
                settings.currency, MODE_LIVE, settings.expiry_universe, settings.gamma_underlying),
            latest_daily_close_run=repository.get_latest_menthorq_level_run(
                settings.currency, MODE_DAILY_CLOSE, settings.expiry_universe,
                settings.gamma_underlying),
            alerts_enabled=settings.alerts_enabled,
            monitor_state=state,
            monitor_state_error=state_error,
            last_alert_delivered=repository.get_last_menthorq_alert_delivery_of_keys(
                settings.currency, list(URGENT_ALERT_KEYS)),
        )
        # The newest snapshot in the database, not the one the last stored record used:
        # a newer snapshot may have arrived since the last hourly run.
        newest_snapshot = repository.get_latest_option_chain_capture_at_or_before(settings.currency, now)
        return format_status_reply(status, settings.stale_snapshot_limit, newest_snapshot)

    def apply_mute(self, arguments: str) -> str:
        """
        /mute: with a duration, mute urgent alerts until now + duration;
        with "off", unmute; without an argument, show the current state.
        Saved in ``menthorq_monitor_state``, so it survives a restart.

        Returns:
            The reply text.
        """
        currency = self.settings.currency
        now = self.clock()
        if not arguments.strip():
            return format_mute_state_reply(currency, self.repository.get_menthorq_monitor_state(currency), now)
        try:
            duration = parse_mute_duration(arguments)
        except ValueError as error:
            return format_mute_error_reply(str(error))
        muted_until = None if duration is None else now + duration
        try:
            self.repository.save_menthorq_alert_mute(currency, muted_until, now)
        except Exception:
            logger.exception("MenthorQ monitor: mute not saved")
            return format_mute_error_reply("could not be saved (database error, see the collector log)")
        if muted_until is None:
            logger.info("MenthorQ monitor: urgent alerts unmuted by the owner")
            return format_mute_cleared_reply(currency)
        logger.info("MenthorQ monitor: urgent alerts muted until %s", muted_until.isoformat())
        return format_mute_set_reply(currency, muted_until, arguments.strip().lower())

    def build_help_reply(self) -> str:
        """/help: commands and the urgent alert rules in force."""
        return format_help_reply(self.settings.currency, self.delivery_service.thresholds)
