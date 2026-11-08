"""
Receives Telegram bot commands (/brief, /levels, /changes, /status, /mute,
/help) by polling getUpdates.

How it runs: the collector's APScheduler calls ``poll_once`` every few
seconds (an interval job, ``max_instances=1``), so it starts and stops with
the existing service; no extra thread or process. Each call long-polls
for at most ``LONG_POLL_SECONDS``.

Rules:
  - Only the owner chat (the configured OSF_TELEGRAM_CHAT_ID) may run a
    command; any other chat is rejected and logged, and gets no reply.
  - A command older than ``MAXIMUM_COMMAND_AGE`` (e.g. sent while the
    service was down) is skipped, so a restart does not replay a backlog.
  - An owner command without a handler goes to ``unknown_command_handler``
    (a short help hint).
  - The first poll registers the command menu with Telegram
    (setMyCommands) once; a failure is logged and polling goes on.
  - At the first poll (each start of the collector) every pending update
    is skipped: getUpdates with offset -1 returns only the newest update
    and makes Telegram forget the older ones, and the offset is set after
    it. A restart therefore never re-runs a command that was already
    handled (the offset is kept in memory only). Commands sent while the
    service was down are lost too; the owner sends them again.
  - Without an owner chat id one warning is logged at construction and
    ``poll_once`` does nothing (the job does not even schedule polling).
  - Getting updates fails if a webhook is set on the bot or another process
    polls the same bot token (Telegram answers 409); that is logged.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Optional, Sequence, Tuple

from coding.service.scanner.telegram_alert_service import TelegramAlertService

logger = logging.getLogger(__name__)

LONG_POLL_SECONDS = 10
MAXIMUM_COMMAND_AGE = timedelta(minutes=10)

# A handler gets the text after the command (e.g. "24h" for "/mute 24h"), "" when none.
CommandHandler = Callable[[str], object]


class TelegramCommandListener:
    """Dispatches owner-only bot commands to handlers."""

    def __init__(
        self,
        telegram: TelegramAlertService,
        owner_chat_id: str,
        handlers: Dict[str, CommandHandler],
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        long_poll_seconds: int = LONG_POLL_SECONDS,
        unknown_command_handler: Optional[CommandHandler] = None,
        command_menu: Sequence[Tuple[str, str]] = (),
    ):
        """
        Args:
            telegram: Bot API access (its token is used for getUpdates).
            owner_chat_id: The only chat id whose commands are run.
            handlers: Command (lower case, with the slash, e.g. "/brief") ->
                callable run with the command's argument text.
            clock: Wall clock (UTC-aware) for the command-age rule.
            long_poll_seconds: getUpdates long-polling wait.
            unknown_command_handler: Run with the command (e.g. "/start")
                for an owner command without a handler; None ignores it.
            command_menu: (command, description) pairs registered with
                setMyCommands at the first poll; empty registers nothing.
        """
        self._telegram = telegram
        self._owner_chat_id = str(owner_chat_id).strip()
        self._handlers = {command.lower(): handler for command, handler in handlers.items()}
        self._clock = clock
        self._long_poll_seconds = long_poll_seconds
        self._unknown_command_handler = unknown_command_handler
        self._command_menu = tuple(command_menu)
        self._menu_registration_attempted = False
        self._offset: Optional[int] = None
        self._pending_updates_skipped = False
        if not self._owner_chat_id:
            logger.warning("TelegramCommandListener: no owner chat id configured; commands disabled")

    def skip_pending_updates(self) -> bool:
        """
        Forget every update that arrived before this start (getUpdates with
        offset -1 and no wait) and continue after the newest one.

        Returns:
            True when done (also when nothing was pending); False when
            Telegram could not be asked (retried at the next poll). Never raises.
        """
        newest = self._telegram.get_updates(-1, 0)
        if newest is None:
            logger.warning("TelegramCommandListener: pending updates could not be skipped; retried at the next poll")
            return False
        update_ids = [update.get("update_id") for update in newest]
        update_ids = [update_id for update_id in update_ids if isinstance(update_id, int)]
        if update_ids:
            self._offset = max(update_ids) + 1
            logger.info("TelegramCommandListener: skipped the pending updates up to update_id %d "
                        "(%d returned by Telegram; older ones are discarded unread): commands sent "
                        "before this start are not run", max(update_ids), len(update_ids))
        else:
            logger.info("TelegramCommandListener: no pending updates at start")
        return True

    def register_command_menu(self) -> bool:
        """
        Send the command menu to Telegram once (later calls do nothing).

        Returns:
            True when Telegram accepted it. Never raises.
        """
        if self._menu_registration_attempted or not self._command_menu:
            return False
        self._menu_registration_attempted = True
        try:
            accepted = self._telegram.set_my_commands(self._command_menu)
        except Exception:
            logger.exception("TelegramCommandListener: registering the command menu failed")
            return False
        if accepted:
            logger.info("TelegramCommandListener: command menu registered (%d commands)",
                        len(self._command_menu))
        else:
            logger.warning("TelegramCommandListener: command menu NOT registered; commands still work")
        return accepted

    def poll_once(self) -> int:
        """
        Fetch pending updates and run the owner's commands. The first
        successful call skips the updates from before the start
        (``skip_pending_updates``).

        Returns:
            Number of commands answered (known or unknown). Never raises (a
            handler error is logged).
        """
        if not self._owner_chat_id:
            return 0   # warned once at construction
        self.register_command_menu()
        if not self._pending_updates_skipped:
            self._pending_updates_skipped = self.skip_pending_updates()
            if not self._pending_updates_skipped:
                return 0
        updates = self._telegram.get_updates(self._offset, self._long_poll_seconds)
        if updates is None:
            return 0
        handled = 0
        for update in updates:
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                self._offset = max(self._offset or 0, update_id + 1)
            if self._handle(update.get("message") or {}):
                handled += 1
        return handled

    def _handle(self, message: Dict) -> bool:
        text = (message.get("text") or "").strip()
        if not text.startswith("/"):
            return False
        parts = text.split(maxsplit=1)
        command = parts[0].split("@")[0].lower()
        arguments = parts[1].strip() if len(parts) > 1 else ""
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", "")).strip()
        if chat_id != self._owner_chat_id:
            sender = (message.get("from") or {}).get("username")
            logger.warning("TelegramCommandListener: REJECTED %s from chat %s (user %s): not the owner chat",
                           command, chat_id or "unknown", sender or "unknown")
            return False
        sent_at = message.get("date")
        if isinstance(sent_at, (int, float)):
            age = self._clock() - datetime.fromtimestamp(sent_at, tz=timezone.utc)
            if age > MAXIMUM_COMMAND_AGE:
                logger.info("TelegramCommandListener: skipping %s sent %.0f min ago (stale)",
                            command, age.total_seconds() / 60)
                return False
        handler = self._handlers.get(command)
        if handler is None:
            if self._unknown_command_handler is None:
                logger.info("TelegramCommandListener: ignoring unknown command %r", command)
                return False
            logger.info("TelegramCommandListener: unknown command %r, sending the help hint", command)
            handler, arguments = self._unknown_command_handler, command
        else:
            logger.info("TelegramCommandListener: running %s for the owner chat", command)
        try:
            handler(arguments)
        except Exception:
            logger.exception("TelegramCommandListener: handler of %s failed", command)
        return True
