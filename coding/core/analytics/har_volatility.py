"""
HAR-RV volatility forecast (Corsi 2009), pure numpy: next-period variance regressed on the last day, week and month of squared daily returns.

Fitted by ordinary least squares on the history that is passed in (the caller passes only what was known at the forecast instant). The returns must be
consecutive days: a caller with gaps passes the longest unbroken stretch.
"""

import math
from typing import Optional, Sequence

import numpy as np

MINIMUM_RETURNS = 200
"""Fewer daily returns than this give no forecast."""
WEEK = 5
MONTH = 22


def har_annual_volatility(daily_log_returns: Sequence[float], horizon_days: int) -> Optional[float]:
    """
    Annualised volatility forecast for the mean daily variance of the next ``horizon_days`` days.

    Returns:
        A fraction (0.55 for 55%), or None when the fitted variance is not positive (never a floor that invents a quiet market) or with fewer than ``MINIMUM_RETURNS`` returns or a non-positive horizon.
    """
    returns = np.asarray(daily_log_returns, dtype=float)
    if horizon_days < 1 or len(returns) < MINIMUM_RETURNS:
        return None
    squared = returns ** 2
    rows, targets = [], []
    for t in range(MONTH - 1, len(squared) - horizon_days):
        rows.append([1.0, squared[t], squared[t - WEEK + 1:t + 1].mean(), squared[t - MONTH + 1:t + 1].mean()])
        targets.append(squared[t + 1:t + 1 + horizon_days].mean())
    coefficients, *_ = np.linalg.lstsq(np.array(rows), np.array(targets), rcond=None)
    latest = np.array([1.0, squared[-1], squared[-WEEK:].mean(), squared[-MONTH:].mean()])
    variance = float(latest @ coefficients)
    return math.sqrt(variance * 365.0) if variance > 0 else None
