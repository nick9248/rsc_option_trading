"""
Receives Telegram bot commands (/brief, /levels, /changes, /status, /mute,
/help) by polling getUpdates.

How it runs: the collector's APScheduler calls ``poll_once`` every few
seconds (an interval job, ``max_instances=1``), so it starts and stops with
the existing service; no extra thread or process. Each call long-polls
for at most ``LONG_POLL_SECONDS``.

Rules:
  - Only the OWNER may run a command: the message must come from the owner
    chat (the configured OSF_TELEGRAM_CHAT_ID) AND its sender
    (``message["from"]["id"]``) must be the owner user. In a private chat
    (positive chat id) the owner user id is the chat id itself; in a group
    or supergroup (negative chat id) every member writes into the same
    chat, so the owner user id must be configured (MENTHORQ_OWNER_USER_ID,
    ``resolve_owner_user_id``). Any other chat or sender is rejected and
    logged, and gets no reply; a message without a sender ("from") is
    ignored.
  - A command older than ``MAXIMUM_COMMAND_AGE`` (e.g. sent while the
    service was down) is skipped, so a restart does not replay a backlog.
  - An owner command without a handler goes to ``unknown_command_handler``
    (a short help hint).
  - A command addressed to a bot ("/mute@SomeBot") runs only when that is
    THIS bot (its username from getMe, asked once and cached; compared
    without case). Addressed to another bot it is ignored (logged at DEBUG).
    While the username is unknown (getMe failed; asked again at the next
    poll) every addressed command is ignored and plain ones still run.
  - Each poll registers the command menu with Telegram (setMyCommands)
    until it is accepted once; a failure is logged (the first as a
    WARNING, repeats at DEBUG), polling goes on and the next poll retries.
  - At the first poll (each start of the collector) every pending update
    is skipped: getUpdates with offset -1 returns only the newest update
    and makes Telegram forget the older ones, and the offset is set after
    it. A restart therefore never re-runs a command that was already
    handled (the offset is kept in memory only). Commands sent while the
    service was down are not run either. When the newest pending update
    (the only one Telegram returns) is an owner command that would have
    run (owner chat and user, not stale), ONE notice goes to the owner
    chat: "Monitor restarted. The command /xyz sent while it was
    restarting was skipped; please send it again." (only the command
    word, at most 20 characters, escaped; no arguments). Nothing is sent
    otherwise; a failed notice is logged and ignored. A command handled
    just before a shutdown but not yet confirmed to Telegram (confirmed at
    the next poll, seconds later) can also get this notice.
  - Without an owner chat id, or without an owner user id (a group chat
    without MENTHORQ_OWNER_USER_ID), one warning is logged at construction
    and ``poll_once`` does nothing (the job does not even schedule polling).
  - Getting updates fails if a webhook is set on the bot or another process
    polls the same bot token (Telegram answers 409); that is logged.
"""

import html
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Optional, Sequence, Tuple

from coding.service.notifications.telegram_alert_service import TelegramAlertService

logger = logging.getLogger(__name__)

# Telegram's long-poll wait per getUpdates. With the (connect 4 s, read wait + 3 s)
# timeouts the intended bound of one poll is 12 s (get_updates_worst_case_seconds),
# below the 15 s default polling interval, so APScheduler should not have to skip runs.
LONG_POLL_SECONDS = 5
MAXIMUM_COMMAND_AGE = timedelta(minutes=10)
# The restart notice names at most this many characters of the skipped command.
SKIPPED_COMMAND_MAXIMUM_LENGTH = 20

# A handler gets the text after the command (e.g. "24h" for "/mute 24h"), "" when none.
CommandHandler = Callable[[str], object]


def resolve_owner_user_id(owner_chat_id: str, configured_owner_user_id: Optional[int]) -> Optional[str]:
    """
    The Telegram user id whose commands are run: the chat id itself for a
    private chat (a positive chat id: there the user id equals the chat
    id), else (a group or supergroup, negative id) the configured
    MENTHORQ_OWNER_USER_ID. None when neither gives one.

    Args:
        owner_chat_id: The owner chat id (OSF_TELEGRAM_CHAT_ID), "" when unset.
        configured_owner_user_id: MENTHORQ_OWNER_USER_ID, None when unset.
    """
    text = str(owner_chat_id).strip()
    if text.isdigit() and int(text) > 0:
        return text
    if configured_owner_user_id is not None:
        return str(configured_owner_user_id)
    return None


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
        configured_owner_user_id: Optional[int] = None,
    ):
        """
        Args:
            telegram: Bot API access (its token is used for getUpdates).
            owner_chat_id: The only chat id whose commands are run.
            configured_owner_user_id: MENTHORQ_OWNER_USER_ID (needed for a
                group chat; see ``resolve_owner_user_id``).
            handlers: Command (lower case, with the slash, e.g. "/brief") ->
                callable run with the command's argument text.
            clock: Wall clock (UTC-aware) for the command-age rule.
            long_poll_seconds: getUpdates long-polling wait.
            unknown_command_handler: Run with the command (e.g. "/start")
                for an owner command without a handler; None ignores it.
            command_menu: (command, description) pairs registered with
                setMyCommands at the first poll (retried at each poll until
                accepted); empty registers nothing.
        """
        self._telegram = telegram
        self._owner_chat_id = str(owner_chat_id).strip()
        self._handlers = {command.lower(): handler for command, handler in handlers.items()}
        self._clock = clock
        self._long_poll_seconds = long_poll_seconds
        self._unknown_command_handler = unknown_command_handler
        self._command_menu = tuple(command_menu)
        self._menu_registered = False
        self._menu_failure_logged = False   # the first failure is a WARNING, repeats DEBUG
        self._offset: Optional[int] = None
        self._pending_updates_skipped = False
        self._bot_username: Optional[str] = None   # this bot's getMe username, once known
        self._owner_user_id = resolve_owner_user_id(self._owner_chat_id, configured_owner_user_id)
        if not self._owner_chat_id:
            logger.warning("TelegramCommandListener: no owner chat id configured; commands disabled")
        elif self._owner_user_id is None:
            logger.warning("TelegramCommandListener: owner chat %s is a group chat and no "
                           "MENTHORQ_OWNER_USER_ID is configured; commands disabled", self._owner_chat_id)

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
        newest_updates = [update for update in newest if isinstance(update.get("update_id"), int)]
        if newest_updates:
            newest_update = max(newest_updates, key=lambda update: update["update_id"])
            owner_command = self._owner_command(newest_update.get("message") or {})
            if owner_command is not None:
                self._notify_skipped_command(owner_command[0])
        return True

    def register_command_menu(self) -> bool:
        """
        Send the command menu to Telegram until it is accepted once (later
        calls then do nothing). A failed attempt is retried at the next call
        (setMyCommands is cheap); the first failure is logged as a WARNING,
        repeated ones at DEBUG.

        Returns:
            True when Telegram accepted it at this call. Never raises.
        """
        if self._menu_registered or not self._command_menu:
            return False
        try:
            accepted = self._telegram.set_my_commands(self._command_menu)
        except Exception:
            if self._menu_failure_logged:
                logger.debug("TelegramCommandListener: registering the command menu failed again",
                             exc_info=True)
            else:
                logger.exception("TelegramCommandListener: registering the command menu failed; "
                                 "retried at the next poll")
                self._menu_failure_logged = True
            return False
        if accepted:
            self._menu_registered = True
            logger.info("TelegramCommandListener: command menu registered (%d commands)",
                        len(self._command_menu))
        elif self._menu_failure_logged:
            logger.debug("TelegramCommandListener: command menu still NOT registered; retried at the next poll")
        else:
            logger.warning("TelegramCommandListener: command menu NOT registered; commands still work; "
                           "retried at the next poll")
            self._menu_failure_logged = True
        return accepted

    def resolve_bot_username(self) -> Optional[str]:
        """
        This bot's username, asked from Telegram (getMe) until it is known,
        then cached. None while unknown. Never raises.
        """
        if self._bot_username is None:
            try:
                username = self._telegram.get_bot_username()
            except Exception:
                logger.exception("TelegramCommandListener: getMe failed")
                username = None
            if username:
                self._bot_username = username
                logger.info("TelegramCommandListener: this bot is @%s", username)
            else:
                logger.warning("TelegramCommandListener: bot username unknown (getMe failed): "
                               "commands addressed with @<bot> are ignored until it is known")
        return self._bot_username

    def poll_once(self) -> int:
        """
        Fetch pending updates and run the owner's commands. The first
        successful call skips the updates from before the start
        (``skip_pending_updates``).

        Returns:
            Number of commands answered (known or unknown). Never raises (a
            handler error is logged).
        """
        if not self._owner_chat_id or self._owner_user_id is None:
            return 0   # warned once at construction
        self.register_command_menu()
        self.resolve_bot_username()
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
        owner_command = self._owner_command(message)
        if owner_command is None:
            return False
        command, arguments = owner_command
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

    def _owner_command(self, message: Dict) -> Optional[Tuple[str, str]]:
        """
        (command in lower case, argument text) when ``message`` is a command
        this listener runs: from the owner chat and the owner user, addressed
        to no other bot, not older than ``MAXIMUM_COMMAND_AGE``. None
        otherwise (a rejection is logged).
        """
        text = (message.get("text") or "").strip()
        if not text.startswith("/"):
            return None
        parts = text.split(maxsplit=1)
        command, _, addressed_to = parts[0].partition("@")
        command = command.lower()
        arguments = parts[1].strip() if len(parts) > 1 else ""
        if addressed_to and (self._bot_username is None
                             or addressed_to.lower() != self._bot_username.lower()):
            logger.debug("TelegramCommandListener: ignoring %s addressed to @%s (this bot: %s)",
                         command, addressed_to, f"@{self._bot_username}" if self._bot_username else "unknown")
            return None
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", "")).strip()
        sender = message.get("from")
        if not isinstance(sender, dict) or "id" not in sender:
            logger.info("TelegramCommandListener: ignoring %s from chat %s: no sender", command,
                        chat_id or "unknown")
            return None
        sender_name = sender.get("username")
        if chat_id != self._owner_chat_id:
            logger.warning("TelegramCommandListener: REJECTED %s from chat %s (user %s): not the owner chat",
                           command, chat_id or "unknown", sender_name or "unknown")
            return None
        sender_id = str(sender["id"]).strip()
        if sender_id != self._owner_user_id:
            logger.warning("TelegramCommandListener: REJECTED %s in the owner chat from user %s (%s): "
                           "not the owner user", command, sender_id, sender_name or "unknown")
            return None
        sent_at = message.get("date")
        if isinstance(sent_at, (int, float)):
            age = self._clock() - datetime.fromtimestamp(sent_at, tz=timezone.utc)
            if age > MAXIMUM_COMMAND_AGE:
                logger.info("TelegramCommandListener: skipping %s sent %.0f min ago (stale)",
                            command, age.total_seconds() / 60)
                return None
        return command, arguments

    def _notify_skipped_command(self, command: str) -> None:
        """
        Tell the owner that ``command`` (sent while the collector restarted)
        was skipped: one short message naming only the command word (cut to
        ``SKIPPED_COMMAND_MAXIMUM_LENGTH`` characters, escaped), never its
        arguments. A failure is logged and ignored.
        """
        command_text = html.escape(command[:SKIPPED_COMMAND_MAXIMUM_LENGTH], quote=False)
        message = (f"<b>Monitor restarted.</b> The command <code>{command_text}</code> sent while it "
                   "was restarting was skipped; please send it again.")
        try:
            if self._telegram.send(message, parse_mode="HTML"):
                logger.info("TelegramCommandListener: told the owner that %s was skipped at start", command_text)
            else:
                logger.warning("TelegramCommandListener: the skipped-command notice for %s was NOT delivered",
                               command_text)
        except Exception:
            logger.exception("TelegramCommandListener: the skipped-command notice failed")
