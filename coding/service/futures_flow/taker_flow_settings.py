"""
Settings of the taker flow collector, read from the environment (.env).

Variables (all optional):
  TAKER_FLOW_ENABLED       1/0  hourly collection in the collector (unified_scheduler),
                                default 0. Opt-in like the MenthorQ monitor: only the
                                production collector (the VPS .env sets it to 1) writes
                                taker_flow_hourly; a local debug run writes nothing, because
                                the local table is a mirror that scripts/sync_from_vps.py fills.
  BINANCE_FUTURES_BASE_URL URL  where the Binance futures klines are read, default
                                https://fapi.binance.com. Binance blocks US addresses, so the
                                VPS .env sets the relay on the Spanish server
                                (http://65.20.101.75:8082, documentation/futures_taker_flow.md).
"""

import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Mapping, Optional
from urllib.parse import urlparse

from dotenv import load_dotenv

DEFAULT_BINANCE_FUTURES_BASE_URL = "https://fapi.binance.com"
SYMBOLS = ("BTCUSDT", "ETHUSDT")      # Binance USD-M perpetuals; the brief shows BTC and ETH
BACKFILL_DAYS = 30
RUN_MINUTE = 5                        # minute past each hour of the collection (UTC)
STALE_AFTER = timedelta(hours=3)      # data older than this is reported as stale


def symbol_label(symbol: str) -> str:
    """``BTCUSDT`` -> ``BTC``."""
    return symbol[:-4] if symbol.endswith("USDT") else symbol


class TakerFlowSettingsError(ValueError):
    """An invalid TAKER_FLOW_* / BINANCE_FUTURES_* setting; the message names only the variable and value."""


@dataclass(frozen=True)
class TakerFlowSettings:
    """Taker flow configuration."""

    enabled: bool = False
    base_url: str = DEFAULT_BINANCE_FUTURES_BASE_URL

    @classmethod
    def from_environment(cls, environment: Optional[Mapping[str, str]] = None) -> "TakerFlowSettings":
        """
        Read the settings; a variable that is not set keeps its default.

        Raises:
            TakerFlowSettingsError: when a variable has an invalid value (a typo is never
                silently replaced by a default).
        """
        if environment is None:
            load_dotenv(dotenv_path=Path(__file__).parents[3] / ".env")
            environment = os.environ

        enabled_text = (environment.get("TAKER_FLOW_ENABLED") or "").strip().lower()
        if enabled_text in ("", "0", "false", "no", "off"):
            enabled = False
        elif enabled_text in ("1", "true", "yes", "on"):
            enabled = True
        else:
            raise TakerFlowSettingsError(f"TAKER_FLOW_ENABLED={enabled_text!r} is not a boolean (use 1 or 0)")

        base_url = (environment.get("BINANCE_FUTURES_BASE_URL") or "").strip() or DEFAULT_BINANCE_FUTURES_BASE_URL
        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or parsed.path not in ("", "/") \
                or parsed.query or parsed.params:
            raise TakerFlowSettingsError(
                f"BINANCE_FUTURES_BASE_URL={base_url!r} must look like http://host[:port] (no path)")
        return cls(enabled=enabled, base_url=base_url.rstrip("/"))
