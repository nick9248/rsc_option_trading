"""
Settings of the MenthorQ-style monitor, read from the environment (.env).

Variables (all optional; the defaults are in ``MenthorqMonitorSettings``):
  MENTHORQ_MONITOR_ENABLED        1/0  hourly computation and storage
  MENTHORQ_ALERTS_ENABLED         1/0  urgent alerts and the weekly brief (Telegram)
  MENTHORQ_COMMANDS_ENABLED       1/0  the /brief command (Telegram getUpdates polling)
  MENTHORQ_EXPIRY_UNIVERSE        all | monthly | quarterly_plus_front_month
  MENTHORQ_LIVE_RUN_MINUTE        0-59, minute past each hour of the live run
  MENTHORQ_WEEKLY_BRIEF_DAY       mon | tue | wed | thu | fri | sat | sun
  MENTHORQ_WEEKLY_BRIEF_TIME_UTC  HH:MM (UTC)
  MENTHORQ_COMMAND_POLL_SECONDS   seconds between getUpdates calls (>= 5)

The Telegram bot token and the owner chat id come from the environment
(OSF_TELEGRAM_BOT_TOKEN / OSF_TELEGRAM_CHAT_ID, see TelegramAlertService);
only that chat may use /brief.
"""

import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Mapping, Optional

from dotenv import load_dotenv

from coding.core.analytics.menthorq_replication_calculator import (
    EXPIRY_UNIVERSE_MONTHLY,
    EXPIRY_UNIVERSES,
    GAMMA_UNDERLYING_INDEX,
)

WEEKDAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MINIMUM_COMMAND_POLL_SECONDS = 5


@dataclass(frozen=True)
class MenthorqMonitorSettings:
    """
    Monitor configuration. ``currency`` is fixed to BTC: MenthorQ's Deribit
    charts are BTC. ``expiry_universe`` defaults to "monthly" (the replay
    script's default); no single universe matched MenthorQ on every
    checked date, so treat it as a choice, not a verified fact.
    """

    monitor_enabled: bool = True
    alerts_enabled: bool = True
    commands_enabled: bool = True
    currency: str = "BTC"
    expiry_universe: str = EXPIRY_UNIVERSE_MONTHLY
    gamma_underlying: str = GAMMA_UNDERLYING_INDEX
    live_run_minute: int = 10
    weekly_brief_weekday: int = 4          # Monday = 0 ... Friday = 4
    weekly_brief_time_utc: time = time(9, 0)
    command_poll_seconds: int = 20

    @property
    def weekly_brief_day_name(self) -> str:
        """APScheduler day_of_week name, e.g. "fri"."""
        return WEEKDAY_NAMES[self.weekly_brief_weekday]

    @classmethod
    def from_environment(
        cls, environment: Optional[Mapping[str, str]] = None
    ) -> "MenthorqMonitorSettings":
        """
        Read the settings; a variable that is not set keeps its default.

        Args:
            environment: Variables to read; ``os.environ`` (after loading the
                repository's .env) when omitted.

        Raises:
            ValueError: when a variable is set to an invalid value (the
                monitor is then not started; a typo is never silently replaced
                by a default).
        """
        if environment is None:
            load_dotenv(dotenv_path=Path(__file__).parents[4] / ".env")
            environment = os.environ
        defaults = cls()

        def flag(name: str, default: bool) -> bool:
            text = environment.get(name)
            if text is None or text.strip() == "":
                return default
            if text.strip().lower() in ("1", "true", "yes", "on"):
                return True
            if text.strip().lower() in ("0", "false", "no", "off"):
                return False
            raise ValueError(f"{name}={text!r} is not a boolean (use 1 or 0)")

        def integer(name: str, default: int, minimum: int, maximum: Optional[int]) -> int:
            text = environment.get(name)
            if text is None or text.strip() == "":
                return default
            try:
                value = int(text)
            except ValueError:
                raise ValueError(f"{name}={text!r} is not an integer") from None
            if value < minimum or (maximum is not None and value > maximum):
                raise ValueError(f"{name}={value} is outside {minimum}..{maximum}")
            return value

        universe = environment.get("MENTHORQ_EXPIRY_UNIVERSE", "").strip() or defaults.expiry_universe
        if universe not in EXPIRY_UNIVERSES:
            raise ValueError(f"MENTHORQ_EXPIRY_UNIVERSE={universe!r} is not one of {EXPIRY_UNIVERSES}")

        day_text = environment.get("MENTHORQ_WEEKLY_BRIEF_DAY", "").strip().lower()
        if day_text and day_text not in WEEKDAY_NAMES:
            raise ValueError(f"MENTHORQ_WEEKLY_BRIEF_DAY={day_text!r} is not one of {WEEKDAY_NAMES}")
        weekday = WEEKDAY_NAMES.index(day_text) if day_text else defaults.weekly_brief_weekday

        time_text = environment.get("MENTHORQ_WEEKLY_BRIEF_TIME_UTC", "").strip()
        brief_time = defaults.weekly_brief_time_utc
        if time_text:
            try:
                brief_time = time.fromisoformat(time_text)
            except ValueError:
                raise ValueError(f"MENTHORQ_WEEKLY_BRIEF_TIME_UTC={time_text!r} is not HH:MM") from None

        return cls(
            monitor_enabled=flag("MENTHORQ_MONITOR_ENABLED", defaults.monitor_enabled),
            alerts_enabled=flag("MENTHORQ_ALERTS_ENABLED", defaults.alerts_enabled),
            commands_enabled=flag("MENTHORQ_COMMANDS_ENABLED", defaults.commands_enabled),
            expiry_universe=universe,
            live_run_minute=integer("MENTHORQ_LIVE_RUN_MINUTE", defaults.live_run_minute, 0, 59),
            weekly_brief_weekday=weekday,
            weekly_brief_time_utc=brief_time,
            command_poll_seconds=integer("MENTHORQ_COMMAND_POLL_SECONDS",
                                         defaults.command_poll_seconds,
                                         MINIMUM_COMMAND_POLL_SECONDS, None),
        )
