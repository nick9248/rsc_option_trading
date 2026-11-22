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
from scipy import stats

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


STUDENT_T_DEGREES_OF_FREEDOM = 5
"""Fat tails: 5 degrees of freedom was the best calibrated choice on 483 BTC and ETH entries (2026-03 to 2026-11), see the research note of 2026-11-22."""
STUDENT_T_GRID_LIMIT = 12.0


@dataclass(frozen=True)
class StudentTDistribution:
    """
    Settlement price S = forward x exp(shift + scale x t), t Student-t distributed; the total volatility (annual_vol x sqrt(days / 365)) is the
    standard deviation of the log return, and ``shift`` makes the mean of S the forward, like the lognormal one. Same ``grid()`` interface.
    """

    forward: float
    annual_vol: float
    days: float
    degrees_of_freedom: float = STUDENT_T_DEGREES_OF_FREEDOM

    def __post_init__(self) -> None:
        if self.forward <= 0 or self.annual_vol <= 0 or self.days <= 0:
            raise ValueError("forward, annual_vol and days must be positive")
        if self.degrees_of_freedom <= 2:
            raise ValueError("the variance of a Student-t exists only above 2 degrees of freedom")

    def grid(self) -> Tuple[np.ndarray, np.ndarray]:
        """(prices, weights): the weights are the probabilities of the grid points and sum to 1."""
        total_vol = self.annual_vol * math.sqrt(self.days / 365.0)
        scale = total_vol * math.sqrt((self.degrees_of_freedom - 2.0) / self.degrees_of_freedom)
        x = np.linspace(-STUDENT_T_GRID_LIMIT, STUDENT_T_GRID_LIMIT, GRID_POINTS)
        weights = stats.t.pdf(x, self.degrees_of_freedom)
        weights /= weights.sum()
        growth = np.exp(scale * x)
        shift = -math.log(float((weights * growth).sum()))
        return self.forward * np.exp(shift) * growth, weights
