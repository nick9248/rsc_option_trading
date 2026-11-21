"""
A price distribution at expiry: a lognormal with a given forward, volatility and time, on a grid (pure, numpy).

The forward is the mean of the distribution (no drift beyond it), so an option chain priced consistently with the same volatility has an
expected PnL of zero before costs. Different volatilities give different views: the implied one is the market's, a realised one is a
view that options are rich or cheap.
"""

import math
from dataclasses import dataclass
from typing import Tuple

import numpy as np

GRID_POINTS = 1601
GRID_LIMIT = 6.0
"""The grid spans plus and minus this many standard deviations of the log price."""


@dataclass(frozen=True)
class LognormalDistribution:
    """Settlement price S = forward x exp(-0.5 v^2 + v z), z standard normal, v = annual_vol x sqrt(days / 365)."""

    forward: float
    annual_vol: float
    """As a fraction (0.55 for 55%)."""
    days: float

    def __post_init__(self) -> None:
        if self.forward <= 0 or self.annual_vol <= 0 or self.days <= 0:
            raise ValueError("forward, annual_vol and days must be positive")

    def grid(self) -> Tuple[np.ndarray, np.ndarray]:
        """(prices, weights): the weights are the probabilities of the grid points and sum to 1."""
        total_vol = self.annual_vol * math.sqrt(self.days / 365.0)
        z = np.linspace(-GRID_LIMIT, GRID_LIMIT, GRID_POINTS)
        weights = np.exp(-0.5 * z * z)
        weights /= weights.sum()
        prices = self.forward * np.exp(-0.5 * total_vol * total_vol + total_vol * z)
        return prices, weights
