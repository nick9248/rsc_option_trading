"""
Settings of the MenthorQ-style monitor, read from the environment (.env).

Variables (all optional; the defaults are in ``MenthorqMonitorSettings``):
  MENTHORQ_MONITOR_ENABLED        1/0  hourly computation and storage in the collector,
                                       default 0
  MENTHORQ_ALERTS_ENABLED         1/0  urgent alerts and the weekly brief (Telegram),
                                       default 0
  MENTHORQ_COMMANDS_ENABLED       1/0  the bot commands (Telegram getUpdates polling),
                                       default 0

All three switches are opt-in: only the production collector (the VPS .env
sets all three to 1) runs the monitor. A local debug run of the collector
with the defaults writes no menthorq_level_snapshots row (the local database
is a read-only mirror that scripts/sync_from_vps.py fills, with
MAX(computed_at) as its watermark), never polls getUpdates and sends no
message, so it cannot steal the production bot's updates or send duplicate
alerts. The dry-run CLI and the GUI preview only compute and do not read
these switches.
  MENTHORQ_EXPIRY_UNIVERSE        all | monthly | quarterly_plus_front_month
  MENTHORQ_LIVE_RUN_MINUTE        0-59, minute past each hour of the live run
  MENTHORQ_WEEKLY_BRIEF_DAY       mon | tue | wed | thu | fri | sat | sun
  MENTHORQ_WEEKLY_BRIEF_TIME_UTC  HH:MM (UTC; a value with an offset is refused)
  MENTHORQ_COMMAND_POLL_SECONDS   seconds between getUpdates calls (>= the listener's
                                  long-poll wait + 2 = 12)
  MENTHORQ_STALE_SNAPSHOT_MINUTES minutes (>= 10, default 120): a snapshot or spot
                                  trade older than this before the data time, or a
                                  stored record's snapshot older than this before
                                  now, puts a "DATA STALE" line on the brief,
                                  /levels, /changes

Urgent alert rules (see alert_rules; "percent" = percent of spot, a number
in (0, 100] or "off"):
  MENTHORQ_ALERT_HVL_MOVE_PERCENT              default 5    (HVL move vs 24h)
  MENTHORQ_ALERT_CALL_RESISTANCE_MOVE_PERCENT  default 10   (Call Resistance move vs 24h)
  MENTHORQ_ALERT_PUT_SUPPORT_MOVE_PERCENT      default 5    (Put Support move vs 24h)
  MENTHORQ_ALERT_NET_GEX_SIGN_CHANGE           1/0, default 1
  MENTHORQ_ALERT_SPOT_CROSSES_HVL              1/0, default 0
  MENTHORQ_ALERT_SPOT_NEAR_WALL_PERCENT        default off (e.g. 1: spot within 1% of
                                               Call Resistance or Put Support)

The Telegram bot token and the owner chat id come from the environment
(OSF_TELEGRAM_BOT_TOKEN / OSF_TELEGRAM_CHAT_ID, see TelegramAlertService);
only the owner user in that chat may use the commands:
  MENTHORQ_OWNER_USER_ID          the owner's Telegram user id (positive integer).
                                  Not needed for a private chat (positive chat id:
                                  the user id is the chat id). REQUIRED for a group
                                  or supergroup chat (negative chat id): without it
                                  the commands are disabled (one warning at start-up).
"""

import os
from dataclasses import dataclass
from datetime import time, timedelta
from pathlib import Path
from typing import Mapping, Optional

from dotenv import load_dotenv

from coding.core.analytics.menthorq_monitor.alert_rules import AlertThresholds
from coding.core.analytics.menthorq_replication_calculator import (
    EXPIRY_UNIVERSE_MONTHLY,
    EXPIRY_UNIVERSES,
    GAMMA_UNDERLYING_INDEX,
)
from coding.service.on_chain.menthorq_monitor.telegram_command_listener import LONG_POLL_SECONDS

WEEKDAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
# Each poll blocks for the long-poll wait; a shorter interval only makes
# APScheduler skip runs (max_instances=1). 2 s margin for the request itself.
MINIMUM_COMMAND_POLL_SECONDS = LONG_POLL_SECONDS + 2
MINIMUM_STALE_SNAPSHOT_MINUTES = 10
RULE_OFF = "off"


@dataclass(frozen=True)
class MenthorqMonitorSettings:
    """
    Monitor configuration. ``currency`` is fixed to BTC: MenthorQ's Deribit
    charts are BTC. ``expiry_universe`` defaults to "monthly" (the replay
    script's default); no single universe matched MenthorQ on every
    checked date, so treat it as a choice, not a verified fact.
    """

    monitor_enabled: bool = False          # opt-in, see the module docstring
    alerts_enabled: bool = False           # opt-in, see the module docstring
    commands_enabled: bool = False         # opt-in, see the module docstring
    currency: str = "BTC"
    expiry_universe: str = EXPIRY_UNIVERSE_MONTHLY
    gamma_underlying: str = GAMMA_UNDERLYING_INDEX
    live_run_minute: int = 10
    weekly_brief_weekday: int = 4          # Monday = 0 ... Friday = 4
    weekly_brief_time_utc: time = time(9, 0)
    command_poll_seconds: int = 20
    stale_snapshot_minutes: int = 120
    alert_thresholds: AlertThresholds = AlertThresholds()
    owner_user_id: Optional[int] = None    # MENTHORQ_OWNER_USER_ID; needed for a group chat only

    @property
    def weekly_brief_day_name(self) -> str:
        """APScheduler day_of_week name, e.g. "fri"."""
        return WEEKDAY_NAMES[self.weekly_brief_weekday]

    @property
    def stale_snapshot_limit(self) -> timedelta:
        """``stale_snapshot_minutes`` as a timedelta (the "DATA STALE" limit)."""
        return timedelta(minutes=self.stale_snapshot_minutes)

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

        def percent(name: str, default: Optional[float]) -> Optional[float]:
            text = environment.get(name)
            if text is None or text.strip() == "":
                return default
            if text.strip().lower() == RULE_OFF:
                return None
            try:
                value = float(text)
            except ValueError:
                raise ValueError(f"{name}={text!r} is not a number or 'off'") from None
            if not 0 < value <= 100:
                raise ValueError(f"{name}={value:g} is outside (0, 100] percent of spot")
            return value

        default_thresholds = defaults.alert_thresholds
        thresholds = AlertThresholds(
            hvl_move_percent_of_spot=percent(
                "MENTHORQ_ALERT_HVL_MOVE_PERCENT", default_thresholds.hvl_move_percent_of_spot),
            call_resistance_move_percent_of_spot=percent(
                "MENTHORQ_ALERT_CALL_RESISTANCE_MOVE_PERCENT",
                default_thresholds.call_resistance_move_percent_of_spot),
            put_support_move_percent_of_spot=percent(
                "MENTHORQ_ALERT_PUT_SUPPORT_MOVE_PERCENT",
                default_thresholds.put_support_move_percent_of_spot),
            net_gex_sign_change=flag("MENTHORQ_ALERT_NET_GEX_SIGN_CHANGE",
                                     default_thresholds.net_gex_sign_change),
            spot_crosses_hvl=flag("MENTHORQ_ALERT_SPOT_CROSSES_HVL",
                                  default_thresholds.spot_crosses_hvl),
            spot_near_wall_percent=percent("MENTHORQ_ALERT_SPOT_NEAR_WALL_PERCENT",
                                           default_thresholds.spot_near_wall_percent),
            cooldown=default_thresholds.cooldown,
        )

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
            if brief_time.tzinfo is not None:
                raise ValueError(f"MENTHORQ_WEEKLY_BRIEF_TIME_UTC={time_text!r} has an offset; "
                                 "UTC only, format HH:MM")

        owner_user_text = environment.get("MENTHORQ_OWNER_USER_ID", "").strip()
        owner_user_id = defaults.owner_user_id
        if owner_user_text:
            if not owner_user_text.isdigit() or int(owner_user_text) <= 0:
                raise ValueError(f"MENTHORQ_OWNER_USER_ID={owner_user_text!r} is not a positive integer "
                                 "(a Telegram user id)")
            owner_user_id = int(owner_user_text)

        return cls(
            owner_user_id=owner_user_id,
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
            stale_snapshot_minutes=integer("MENTHORQ_STALE_SNAPSHOT_MINUTES",
                                           defaults.stale_snapshot_minutes,
                                           MINIMUM_STALE_SNAPSHOT_MINUTES, None),
            alert_thresholds=thresholds,
        )
