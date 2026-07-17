"""
Telegram delivery for the long-straddle scanner (increment 2, Part 3).

Clean rewrite for the scanner — shaped after (but not sharing any code
with) the deleted displacement TelegramAlertService
(coding/service/displacement/telegram_alert_service.py, removed in the
2026-07-13 foundation cleanup). This version sends the plain-text message
StraddleScanService.format_alert() already builds; it does no formatting
of its own.
"""

import logging
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)


class TelegramAlertService:
    """
    Sends a plain-text message to a Telegram chat via the Bot API.

    Never raises: any failure (missing credentials, network error, non-200
    response) is logged as a warning and send() returns False. Callers
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

    def send(self, message: str) -> bool:
        """
        Send `message` to the configured chat.

        Args:
            message: Plain-text message body (no HTML/Markdown parsing).

        Returns:
            True on a successful (2xx) send, False on any failure.
        """
        if not self._token or not self._chat_id:
            logger.warning("TelegramAlertService: missing credentials, skipping send")
            return False

        try:
            response = requests.post(
                f"{self._base_url}/sendMessage",
                json={"chat_id": self._chat_id, "text": message},
                timeout=10,
            )
            if not response.ok:
                logger.warning(
                    "TelegramAlertService: API returned %s: %s",
                    response.status_code, response.text,
                )
                return False
            return True
        except Exception as exc:
            logger.warning("TelegramAlertService: send failed: %s", exc)
            return False
