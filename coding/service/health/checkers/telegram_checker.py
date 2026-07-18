"""Telegram alert delivery health checks."""

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import List

import requests
from dotenv import load_dotenv

from coding.core.health.models import CheckEnvironment, CheckResult, CheckStatus
from coding.service.health.base import HealthCheck

_TRIGGER_THRESHOLD = 15.0
_LOOKBACK_HOURS = 48
_CURRENCIES = ["BTC", "ETH"]
_ENV_PATH = Path(__file__).parents[4] / ".env"


class TelegramConfigCheck(HealthCheck):
    """Validates the configured Telegram bot token via getMe (no message sent)."""

    category = "Telegram"
    environment = CheckEnvironment.BOTH

    def __init__(self, requests_module=requests):
        self._requests = requests_module

    def run(self, repo) -> List[CheckResult]:
        load_dotenv(dotenv_path=_ENV_PATH)
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
                message=f"Telegram API unreachable: {exc}",
            )]

        if not response.ok:
            return [CheckResult(
                name="Telegram config", status=CheckStatus.FAIL,
                message=f"Telegram getMe returned {response.status_code}: {response.text}",
            )]

        return [CheckResult(name="Telegram config", status=CheckStatus.PASS, message="Bot token valid")]


class TelegramDeliveryCheck(HealthCheck):
    """
    Confirms triggered alerts (iv_percentile <= threshold) are actually
    getting marked alert_sent -- catches silent Telegram send failures.
    telegram_alert_service.send() only logs failures, it doesn't persist
    them, so this is a data-based proxy rather than a delivery-log read.
    """

    category = "Telegram"
    environment = CheckEnvironment.BOTH

    def run(self, repo) -> List[CheckResult]:
        results: List[CheckResult] = []
        window_start = datetime.now() - timedelta(hours=_LOOKBACK_HOURS)

        conn = repo._get_connection()
        try:
            cursor = conn.cursor()
            try:
                for currency in _CURRENCIES:
                    cursor.execute("""
                        SELECT COUNT(*), COUNT(CASE WHEN alert_sent THEN 1 END)
                        FROM straddle_scan_history
                        WHERE currency = %s AND scan_time >= %s
                          AND iv_percentile IS NOT NULL AND iv_percentile <= %s
                    """, (currency, window_start, _TRIGGER_THRESHOLD))
                    triggered, sent = cursor.fetchone()

                    if triggered == 0:
                        results.append(CheckResult(
                            name=f"Telegram delivery ({currency})", status=CheckStatus.PASS,
                            message=f"{currency}: no alert-worthy scans in the last {_LOOKBACK_HOURS}h",
                        ))
                    elif sent == 0:
                        results.append(CheckResult(
                            name=f"Telegram delivery ({currency})", status=CheckStatus.WARN,
                            message=(
                                f"{currency}: {triggered} scans triggered the alert threshold "
                                f"but none were marked sent — alerts may be silently failing"
                            ),
                            details={"triggered": triggered, "sent": sent},
                        ))
                    else:
                        results.append(CheckResult(
                            name=f"Telegram delivery ({currency})", status=CheckStatus.PASS,
                            message=f"{currency}: {sent}/{triggered} triggered scans sent",
                            details={"triggered": triggered, "sent": sent},
                        ))
            finally:
                cursor.close()
        finally:
            repo._return_connection(conn)
        return results
