"""
Receives Telegram bot commands (e.g. /brief) by polling getUpdates.

How it runs: the collector's APScheduler calls ``poll_once`` every few
seconds (an interval job, ``max_instances=1``), so it starts and stops with
the existing service; no extra thread or process. Each call long-polls
for at most ``LONG_POLL_SECONDS``.

Rules:
  - Only the owner chat (the configured OSF_TELEGRAM_CHAT_ID) may run a
    command; any other chat is rejected and logged, and gets no reply.
  - A command older than ``MAXIMUM_COMMAND_AGE`` (e.g. sent while the
    service was down) is skipped, so a restart does not replay a backlog.
  - The offset is kept in memory. Telegram keeps unconfirmed updates for
    24 h, so after a restart the last command before the restart can be
    seen again; the age rule above limits that to recent commands.
  - Getting updates fails if a webhook is set on the bot or another process
    polls the same bot token (Telegram answers 409); that is logged.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Optional

from coding.service.scanner.telegram_alert_service import TelegramAlertService

logger = logging.getLogger(__name__)

LONG_POLL_SECONDS = 10
MAXIMUM_COMMAND_AGE = timedelta(minutes=10)


class TelegramCommandListener:
    """Dispatches owner-only bot commands to handlers."""

    def __init__(
        self,
        telegram: TelegramAlertService,
        owner_chat_id: str,
        handlers: Dict[str, Callable[[], object]],
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        long_poll_seconds: int = LONG_POLL_SECONDS,
    ):
        """
        Args:
            telegram: Bot API access (its token is used for getUpdates).
            owner_chat_id: The only chat id whose commands are run.
            handlers: Command (lower case, with the slash, e.g. "/brief") ->
                callable run for it.
            clock: Wall clock (UTC-aware) for the command-age rule.
            long_poll_seconds: getUpdates long-polling wait.
        """
        self._telegram = telegram
        self._owner_chat_id = str(owner_chat_id).strip()
        self._handlers = {command.lower(): handler for command, handler in handlers.items()}
        self._clock = clock
        self._long_poll_seconds = long_poll_seconds
        self._offset: Optional[int] = None

    def poll_once(self) -> int:
        """
        Fetch pending updates and run the owner's known commands.

        Returns:
            Number of commands run. Never raises (a handler error is logged).
        """
        if not self._owner_chat_id:
            logger.warning("TelegramCommandListener: no owner chat id configured; commands disabled")
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
        command = text.split()[0].split("@")[0].lower()
        handler = self._handlers.get(command)
        if handler is None:
            logger.info("TelegramCommandListener: ignoring unknown command %r", command)
            return False
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
        logger.info("TelegramCommandListener: running %s for the owner chat", command)
        try:
            handler()
        except Exception:
            logger.exception("TelegramCommandListener: handler of %s failed", command)
        return True
