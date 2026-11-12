"""
Generic Telegram Bot API sender, used by the MenthorQ monitor
(coding/service/on_chain/menthorq_monitor/) for its alerts, briefs and
command replies.

It was first written for the long-straddle scanner's alerts (it lived in
coding/service/scanner/ until that alert workflow was removed; it now sits
in this feature-neutral package). Shaped after (but not sharing any code with) the
deleted displacement TelegramAlertService (removed in the 2026-07-13
foundation cleanup). It sends the plain text its caller builds and does no
formatting of its own.
"""

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# getUpdates timeouts (requests applies a (connect, read) tuple separately):
# connect at most 4 s; the read waits the long poll plus 3 s for the reply.
GET_UPDATES_CONNECT_TIMEOUT_SECONDS = 4
GET_UPDATES_READ_MARGIN_SECONDS = 3
# Consecutive getUpdates failures: a WARNING at the 10th, then every 50 more.
GET_UPDATES_FAILURE_WARNING_THRESHOLD = 10
GET_UPDATES_FAILURE_WARNING_REPEAT = 50


def get_updates_timeouts(long_poll_seconds: int) -> Tuple[int, int]:
    """The (connect, read) timeout of one getUpdates call with this long-poll wait."""
    return GET_UPDATES_CONNECT_TIMEOUT_SECONDS, long_poll_seconds + GET_UPDATES_READ_MARGIN_SECONDS


def get_updates_worst_case_seconds(long_poll_seconds: int) -> int:
    """
    The longest one getUpdates call can block: the connect timeout plus the
    read timeout (e.g. 4 + 5 + 3 = 12 s for a 5 s long poll). A polling
    interval must be strictly longer, or the scheduler skips runs.
    """
    connect_timeout, read_timeout = get_updates_timeouts(long_poll_seconds)
    return connect_timeout + read_timeout


class TelegramAlertService:
    """
    Sends a plain-text message to a Telegram chat via the Bot API.

    Never raises: any failure (missing credentials, network error, non-200
    response) is logged as a warning (getUpdates: see ``get_updates``) and
    send() returns False. Callers
    should treat a False return as "alert not delivered" and retry (or
    accept the miss) rather than crash the calling pipeline.
    """

    def __init__(self, token: str = "", chat_id: str = ""):
        """
        Args:
            token: Telegram bot token. If empty, read from
                OSF_TELEGRAM_BOT_TOKEN in the environment (.env loaded here
                if not already).
            chat_id: Telegram chat id. If empty, read from
                OSF_TELEGRAM_CHAT_ID in the environment.
        """
        if not token or not chat_id:
            load_dotenv(dotenv_path=Path(__file__).parents[3] / ".env")
            token = token or os.getenv("OSF_TELEGRAM_BOT_TOKEN", "")
            chat_id = chat_id or os.getenv("OSF_TELEGRAM_CHAT_ID", "")
        self._token = token
        self._chat_id = chat_id
        self._base_url = f"https://api.telegram.org/bot{self._token}"
        self._bot_username: Optional[str] = None   # getMe result, cached once known
        # getUpdates keeps one connection open between polls (keep-alive); a new
        # session is opened after a network error.
        self._updates_session: Optional[requests.Session] = None
        self._consecutive_get_updates_failures = 0

    @property
    def chat_id(self) -> str:
        """The configured chat id ("" when not configured)."""
        return self._chat_id

    @property
    def has_credentials(self) -> bool:
        """True when both the bot token and the chat id are configured (``send`` can try)."""
        return bool(self._token) and bool(self._chat_id)

    def _without_token(self, error: object) -> str:
        """
        Return the text of ``error`` with the bot token replaced by
        "<bot token>". requests puts the request URL, which contains the
        token, into its exception text, so every logged request error must
        pass through here.
        """
        text = str(error)
        if not self._token:
            return text
        return text.replace(self._token, "<bot token>")

    def scrub_token(self, text: str) -> str:
        """``text`` with the bot token replaced by "<bot token>" (for text built outside this class)."""
        return self._without_token(text)

    def send(self, message: str, parse_mode: Optional[str] = None) -> bool:
        """
        Send `message` to the configured chat.

        Args:
            message: Message body. Plain text unless ``parse_mode`` is given.
            parse_mode: Optional Telegram parse mode, e.g. "HTML"; the caller
                must have escaped the text for it.

        Returns:
            True on a successful (2xx) send, False on any failure.
        """
        if not self._token or not self._chat_id:
            logger.warning("TelegramAlertService: missing credentials, skipping send")
            return False

        payload = {"chat_id": self._chat_id, "text": message}
        if parse_mode is not None:
            payload["parse_mode"] = parse_mode
        try:
            response = requests.post(
                f"{self._base_url}/sendMessage",
                json=payload,
                timeout=10,
            )
            if not response.ok:
                logger.warning(
                    "TelegramAlertService: API returned %s: %s",
                    response.status_code, response.text,
                )
                return False
            return True
        except Exception as error:
            logger.warning("TelegramAlertService: send failed: %s", self._without_token(error))
            return False

    def set_my_commands(self, commands: Sequence[Tuple[str, str]]) -> bool:
        """
        Register the bot's command menu (Bot API setMyCommands), shown by
        Telegram when the user types "/".

        Args:
            commands: (command without the slash, description) pairs.

        Returns:
            True when Telegram accepted the list, False on any failure.
            Never raises.
        """
        if not self._token:
            logger.warning("TelegramAlertService: missing bot token, skipping setMyCommands")
            return False
        payload = {"commands": [{"command": command, "description": description}
                                for command, description in commands]}
        try:
            response = requests.post(f"{self._base_url}/setMyCommands", json=payload, timeout=10)
            if not response.ok:
                logger.warning("TelegramAlertService: setMyCommands returned %s: %s",
                               response.status_code, response.text)
                return False
            return True
        except Exception as error:
            logger.warning("TelegramAlertService: setMyCommands failed: %s", self._without_token(error))
            return False

    def get_bot_username(self) -> Optional[str]:
        """
        This bot's username (Bot API getMe), e.g. "MenthorBot". A success is
        cached for the life of this object, so Telegram is asked once.

        Returns:
            The username, or None on any failure (missing token, network
            error, non-2xx, malformed body); a failure is not cached.
            Never raises.
        """
        if self._bot_username is not None:
            return self._bot_username
        if not self._token:
            logger.warning("TelegramAlertService: missing bot token, skipping getMe")
            return None
        try:
            response = requests.get(f"{self._base_url}/getMe", timeout=10)
            if not response.ok:
                logger.warning("TelegramAlertService: getMe returned %s: %s",
                               response.status_code, response.text)
                return None
            body = response.json()
            username = (body.get("result") or {}).get("username") if body.get("ok") else None
            if not isinstance(username, str) or not username:
                logger.warning("TelegramAlertService: unexpected getMe body: %s", body)
                return None
            self._bot_username = username
            return username
        except Exception as error:
            logger.warning("TelegramAlertService: getMe failed: %s", self._without_token(error))
            return None

    def get_updates(self, offset: Optional[int], timeout_seconds: int) -> Optional[List[Dict[str, Any]]]:
        """
        Fetch incoming updates (Bot API getUpdates long polling). Calling it
        with ``offset`` = last update_id + 1 confirms every earlier update,
        so Telegram does not deliver it again.

        Uses one persistent ``requests.Session`` (keep-alive, closed and
        reopened after a network error) and the (connect, read) timeouts of
        ``get_updates_timeouts``. A timeout or network error is expected now
        and then on a long poll and is logged at INFO; an HTTP error status
        or a malformed body at WARNING; the 10th consecutive failure (and
        every 50th after it) at WARNING too. A success resets the count.
        The bot token is scrubbed from every logged text.

        Args:
            offset: First update id to return, or None for every unconfirmed one.
            timeout_seconds: Long-polling wait on Telegram's side.

        Returns:
            The list of updates (possibly empty), or None on any failure
            (missing credentials, network error, non-2xx, malformed body).
            Never raises.
        """
        if not self._token:
            logger.warning("TelegramAlertService: missing bot token, skipping getUpdates")
            return None
        parameters: Dict[str, Any] = {"timeout": timeout_seconds, "allowed_updates": '["message"]'}
        if offset is not None:
            parameters["offset"] = offset
        if self._updates_session is None:
            self._updates_session = requests.Session()
        try:
            response = self._updates_session.get(
                f"{self._base_url}/getUpdates",
                params=parameters,
                timeout=get_updates_timeouts(timeout_seconds),
            )
        except requests.Timeout:
            # Expected now and then on a long poll; the next poll retries.
            self._close_updates_session()
            self._log_get_updates_failure("getUpdates timed out, will retry", always_warning=False)
            return None
        except Exception as error:
            self._close_updates_session()
            self._log_get_updates_failure(
                f"getUpdates failed, will retry: {self._without_token(error)}", always_warning=False)
            return None
        if not response.ok:
            self._log_get_updates_failure(
                f"getUpdates returned {response.status_code}: {self._without_token(response.text)}",
                always_warning=True)
            return None
        try:
            body = response.json()
        except ValueError:
            body = None
        if not isinstance(body, dict) or not body.get("ok") or not isinstance(body.get("result"), list):
            self._log_get_updates_failure(
                f"unexpected getUpdates body: {self._without_token(body if body is not None else response.text)}",
                always_warning=True)
            return None
        self._consecutive_get_updates_failures = 0
        return body["result"]

    def _close_updates_session(self) -> None:
        """Close the getUpdates session after a network error; the next poll opens a new one."""
        if self._updates_session is not None:
            try:
                self._updates_session.close()
            except Exception as error:
                logger.debug("TelegramAlertService: closing the getUpdates session failed: %s",
                             self._without_token(error))
            self._updates_session = None

    def _log_get_updates_failure(self, message: str, always_warning: bool) -> None:
        """
        Count one failed getUpdates and log ``message`` (already free of
        the token): at WARNING when ``always_warning`` (an HTTP error status
        or a malformed body) or when the consecutive failures reach
        ``GET_UPDATES_FAILURE_WARNING_THRESHOLD`` (and every
        ``GET_UPDATES_FAILURE_WARNING_REPEAT`` after it), else at INFO.
        """
        self._consecutive_get_updates_failures += 1
        count = self._consecutive_get_updates_failures
        repeated = (count >= GET_UPDATES_FAILURE_WARNING_THRESHOLD
                    and (count - GET_UPDATES_FAILURE_WARNING_THRESHOLD) % GET_UPDATES_FAILURE_WARNING_REPEAT == 0)
        if repeated:
            logger.warning("TelegramAlertService: %s (%d consecutive getUpdates failures)", message, count)
        elif always_warning:
            logger.warning("TelegramAlertService: %s", message)
        else:
            logger.info("TelegramAlertService: %s", message)
