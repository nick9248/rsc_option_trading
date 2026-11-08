"""Telegram bot configuration health check (the bot of the MenthorQ monitor)."""

import os
from pathlib import Path
from typing import List

import requests
from dotenv import load_dotenv

from coding.core.health.models import CheckEnvironment, CheckResult, CheckStatus
from coding.service.health.base import HealthCheck

_ENV_PATH = Path(__file__).parents[4] / ".env"


class TelegramConfigCheck(HealthCheck):
    """Validates the configured Telegram bot token via getMe (no message sent)."""

    category = "Telegram"
    environment = CheckEnvironment.BOTH

    def __init__(self, requests_module=requests, env_path=None):
        self._requests = requests_module
        self._env_path = env_path if env_path is not None else _ENV_PATH

    def run(self, repo) -> List[CheckResult]:
        load_dotenv(dotenv_path=self._env_path)
        token = os.getenv("OSF_TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("OSF_TELEGRAM_CHAT_ID", "")

        if not token or not chat_id:
            return [CheckResult(
                name="Telegram config", status=CheckStatus.FAIL,
                message="OSF_TELEGRAM_BOT_TOKEN / OSF_TELEGRAM_CHAT_ID not set",
            )]

        try:
            response = self._requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
        except Exception as exc:
            return [CheckResult(
                name="Telegram config", status=CheckStatus.FAIL,
                # requests puts the URL, which contains the token, in its error text.
                message=f"Telegram API unreachable: {str(exc).replace(token, '<bot token>')}",
            )]

        if not response.ok:
            return [CheckResult(
                name="Telegram config", status=CheckStatus.FAIL,
                message=f"Telegram getMe returned {response.status_code}: {response.text}",
            )]

        return [CheckResult(name="Telegram config", status=CheckStatus.PASS, message="Bot token valid")]
