"""
Real daily liquidations of Binance BTC/USDT from CryptoQuant (``market-data/liquidations``, daily window, which the
current plan allows). Research use. The API key (``crypto_quant_token`` in .env) is only sent in the
Authorization header and never logged.

Plan limit: the Basic plan serves the daily window only and only the last 30 days (older dates are refused with
"Out of allowed request range"), so by default no date range is sent and whatever the plan allows is returned.

Caveat: this is what CryptoQuant records from Binance's liquidation feed, which is a sample (Binance publishes
at most one liquidation order per symbol per second), so the amounts are lower than the true totals; ranks and
direction are what a backtest can use.
"""

import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

import requests
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

URL = "https://api.cryptoquant.com/v1/btc/market-data/liquidations"
TOKEN_VARIABLE = "crypto_quant_token"
REQUEST_TIMEOUT_SECONDS = 60
MAXIMUM_DAYS = 100000


class CryptoQuantError(RuntimeError):
    """CryptoQuant answered with an error or an unusable body (the message never contains the key)."""


def read_token() -> str:
    """The API key from the environment (.env of the repository); raises when it is missing."""
    load_dotenv(dotenv_path=Path(__file__).resolve().parents[3] / ".env")
    token = (os.environ.get(TOKEN_VARIABLE) or "").strip().strip("\"'")
    if not token:
        raise CryptoQuantError(f"{TOKEN_VARIABLE} is not set in .env")
    return token


def load_daily_liquidations(
    token: str, first_day: Optional[date] = None, last_day: Optional[date] = None,
    session: Optional[requests.Session] = None,
) -> Dict[date, Tuple[float, float]]:
    """
    Real Binance BTC/USDT liquidations per UTC day, USD: ``{day: (long_usd, short_usd)}`` for the days CryptoQuant has.

    Args:
        token: API key.
        first_day: First day to ask for; without it (and ``last_day``) the whole range the plan allows is returned.
        last_day: Last day to ask for.
        session: HTTP session (a new one by default; tests pass a fake).

    Raises:
        CryptoQuantError: on an HTTP error (for example a first day further back than the plan allows) or an
            unexpected body.
    """
    params = {"exchange": "binance", "symbol": "btc_usdt", "window": "day", "limit": MAXIMUM_DAYS}
    if first_day is not None:
        params["from"] = first_day.strftime("%Y%m%d")
    if last_day is not None:
        params["to"] = last_day.strftime("%Y%m%d")
    response = (session or requests.Session()).get(
        URL, params=params, headers={"Authorization": f"Bearer {token}"}, timeout=REQUEST_TIMEOUT_SECONDS)
    if response.status_code != 200:
        raise CryptoQuantError(f"liquidations: HTTP {response.status_code}")
    try:
        rows = response.json()["result"]["data"]
    except (ValueError, KeyError, TypeError):
        raise CryptoQuantError("liquidations: unexpected answer") from None
    days: Dict[date, Tuple[float, float]] = {}
    for row in rows:
        try:
            day = datetime.strptime(row["date"], "%Y-%m-%d").date()
            days[day] = (float(row["long_liquidations_usd"]), float(row["short_liquidations_usd"]))
        except (KeyError, TypeError, ValueError):
            logger.warning("liquidations: skipped a row that is not complete")
    return days
