"""Realised volatility from daily candles (pure)."""

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Sequence

import numpy as np

CANDLE_LENGTH = timedelta(days=1)
"""Deribit daily candles start at 08:00 UTC and last one day."""
MINIMUM_RETURNS = 20
"""Fewer daily returns than this give no volatility."""


def realised_volatility(candles: Sequence[Dict[str, Any]], days: int, minimum_returns: int = MINIMUM_RETURNS) -> Optional[float]:
    """
    Annualised volatility (a fraction) of the last ``days`` daily log returns.

    Args:
        candles: Daily closes oldest first, each ``{"date": datetime, "close": float}``.
        days: Number of daily returns wanted; the last ``days + 1`` candles are used.
        minimum_returns: Fewer usable returns than this give None.

    Returns:
        The sample standard deviation of the returns times the square root of 365, or None. Only a close followed by the close of the
        NEXT day is a daily return: a gap in the candles is never stretched into one.
    """
    window = list(candles)[-(days + 1):]
    returns = [math.log(second["close"] / first["close"]) for first, second in zip(window, window[1:])
               if first["close"] > 0 and second["close"] > 0 and (second["date"] - first["date"]) == timedelta(days=1)]
    if len(returns) < min(minimum_returns, days):
        return None
    return float(np.std(returns, ddof=1) * math.sqrt(365.0))


def last_completed_candle_start(as_of: datetime) -> datetime:
    """
    The start (naive UTC, as the repository expects) of the newest daily candle that is complete at ``as_of``.

    A candle starting at 08:00 UTC of day D ends at 08:00 UTC of D + 1, and only then is its close final: at any earlier instant its close is
    the price of that moment. A reader at ``as_of`` may use the candles that start at or before ``as_of`` minus one day, never the running one.

    Raises:
        ValueError: for a naive ``as_of``.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware (UTC)")
    return as_of.astimezone(timezone.utc).replace(tzinfo=None) - CANDLE_LENGTH
