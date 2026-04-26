import logging
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

from coding.core.displacement.models.displacement_signal import DisplacementSignal

logger = logging.getLogger(__name__)


class TelegramAlertService:
    """Sends displacement alert messages to a Telegram chat via Bot API."""

    def __init__(self, token: str = "", chat_id: str = ""):
        if not token or not chat_id:
            env_path = Path(__file__).parents[3] / ".env"
            load_dotenv(dotenv_path=env_path)
            token = token or os.getenv("TELEGRAM_BOT_TOKEN", "")
            chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self._token = token
        self._chat_id = chat_id
        self._base_url = f"https://api.telegram.org/bot{self._token}"

    def send(self, signal: DisplacementSignal) -> bool:
        """Send alert. Returns True on success, False on any failure."""
        if not self._token or not self._chat_id:
            logger.warning("Telegram not configured — skipping alert")
            return False
        message = self._format_message(signal)
        try:
            response = requests.post(
                f"{self._base_url}/sendMessage",
                json={"chat_id": self._chat_id, "text": message, "parse_mode": "HTML"},
                timeout=10,
            )
            if not response.ok:
                logger.error("Telegram API returned %s: %s", response.status_code, response.text)
            return response.ok
        except Exception as e:
            logger.error("Telegram send failed: %s", e)
            return False

    def _format_message(self, signal: DisplacementSignal) -> str:
        label_emoji = "\U0001f534" if signal.conviction_label == "HIGH" else "\U0001f7e1"

        def bar(score: float) -> str:
            filled = round(score / 10)
            return "█" * filled + "░" * (10 - filled)

        signals_text = (
            f"  Drop magnitude   {bar(signal.score_drop_magnitude)}  {signal.score_drop_magnitude:.0f}\n"
            f"  Funding rate     {bar(signal.score_funding_rate)}  {signal.score_funding_rate:.0f}"
            f"  ({signal.funding_rate_value * 100:.2f}% funding)\n"
            f"  DVOL spike       {bar(signal.score_dvol_spike)}  {signal.score_dvol_spike:.0f}"
            f"  ({signal.dvol_sigma:.1f}σ above mean)\n"
            f"  Max pain dist    {bar(signal.score_max_pain)}  {signal.score_max_pain:.0f}"
            f"  ({signal.max_pain_distance_pct * 100:.1f}% below pain)\n"
            f"  Term structure   {bar(signal.score_term_structure)}  {signal.score_term_structure:.0f}\n"
            f"  Drop speed       {bar(signal.score_drop_speed)}  {signal.score_drop_speed:.0f}"
        )

        contract_section = ""
        if signal.instrument_name:
            contract_section = (
                f"\n\n<b>Recommended contract:</b>\n"
                f"  {signal.instrument_name}\n"
                f"  Delta: {signal.delta:.2f} | IV: {(signal.mark_iv or 0) * 100:.0f}%"
                f" | Premium: ${signal.premium_usd:,.0f}\n"
                f"  DTE: {signal.dte} days\n\n"
                f"<b>Profit targets:</b>\n"
                f"  50%  → {signal.asset} at ${signal.target_50pct_price:,.0f}\n"
                f"  100% → {signal.asset} at ${signal.target_100pct_price:,.0f}\n"
                f"  200% → {signal.asset} at ${signal.target_200pct_price:,.0f}"
            )

        return (
            f"{label_emoji} <b>DISPLACEMENT ALERT — {signal.asset}</b>\n"
            f"━" * 24 + "\n\n"
            f"Drop: -{abs(signal.drop_24h_pct) * 100:.1f}% in 24h"
            f" | -{abs(signal.drop_1h_pct) * 100:.1f}% in 1h\n"
            f"Conviction: {signal.conviction_pct:.0f}% ({signal.conviction_label})\n\n"
            f"<b>Signals:</b>\n{signals_text}"
            f"{contract_section}\n\n"
            f"⚠️ Paper trade — verify before acting"
        )
