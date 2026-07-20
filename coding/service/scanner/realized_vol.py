"""
Shared realized-vol computation for scanner services. Extracted 2026-07-20
from StraddleScanService._compute_realized_vol so the defined-risk scanners
(and RegimeGateService) don't duplicate it. Log-return stdev of daily OHLCV
closes, annualized -- matches VRPCalculator's existing methodology.
"""
import math
from datetime import datetime, timedelta
from typing import Optional

RV_MIN_WINDOW_DAYS = 21
RV_FETCH_BUFFER_DAYS = 15


def dte_matched_window(dte: float) -> int:
    """window_days = max(RV_MIN_WINDOW_DAYS, round(dte)) -- the straddle scanner's own RV lookback rule."""
    return max(RV_MIN_WINDOW_DAYS, round(dte))


def compute_realized_vol(repo, currency: str, window_days: int, as_of: datetime) -> Optional[float]:
    """
    Annualized realized vol (percent units) over `window_days` trailing
    daily closes ending at/before `as_of`.

    Args:
        repo: DatabaseRepository (or a fake exposing get_ohlcv_by_date_range).
        currency: Currency symbol.
        window_days: Trailing daily-close window.
        as_of: End of the lookback window.

    Returns:
        Annualized vol in percent, or None if fewer than window_days+1
        closes are available in [as_of - (window_days+buffer), as_of].
    """
    end = as_of.replace(tzinfo=None) if as_of.tzinfo else as_of
    start = end - timedelta(days=window_days + RV_FETCH_BUFFER_DAYS)
    rows = repo.get_ohlcv_by_date_range(currency, start, end)
    if len(rows) < window_days + 1:
        return None
    closes = [row["close"] for row in rows[-(window_days + 1):]]
    log_returns = [
        math.log(closes[i] / closes[i - 1])
        for i in range(1, len(closes))
        if closes[i - 1] > 0 and closes[i] > 0
    ]
    n = len(log_returns)
    if n < 2:
        return None
    mean = sum(log_returns) / n
    variance = sum((r - mean) ** 2 for r in log_returns) / (n - 1)
    return math.sqrt(variance) * math.sqrt(365.0) * 100.0
