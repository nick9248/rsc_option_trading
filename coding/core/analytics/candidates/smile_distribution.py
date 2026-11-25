"""
The market's own price distribution of one expiry, with its width scaled to a volatility view (pure, numpy and scipy).

An option chain prices strikes at different volatilities (skew) and gives large moves more weight than a lognormal (fat tails). This module fits
that smile with SVI (Gatheral: total variance w(k) = a + b (rho (k - m) + sqrt((k - m)^2 + sigma^2)), k = ln(strike / forward)) to the mid prices
of the out-of-the-money options, reads the distribution of the settlement price from it (the probability of finishing at or below K is
1 + dC/dK of the call price, Breeden and Litzenberger), and can widen or narrow it: each log return's distance from the mean log return is
multiplied by (view volatility x sqrt(years) / the market distribution's own standard deviation), and the mean price stays the forward.

Without a view this is the market's distribution: every option is worth about its mid, so a structure's expected result is minus its costs. A view
volatility sets the standard deviation of the log return (annualised) to the view, keeping the shape: it expresses one view only, that the
market's SHAPE is right and its WIDTH is not. (The market's own width is larger than its at-the-money volatility, by about 13% on stored data,
because of the tails: the first version divided by the at-the-money volatility and was that much wider than named.) Nothing here can find a gain from
skew alone, which three one-volatility models did (see documentation/strategy_pipeline.md).
"""

import math
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import brentq, least_squares
from scipy.stats import norm

from coding.core.analytics.backtest.structures import Quote

MINIMUM_SMILE_QUOTES = 6
"""Fewer two-sided out-of-the-money options than this give no smile."""
MINIMUM_QUOTES_PER_SIDE = 2
MAXIMUM_MEDIAN_ERROR = 0.03
"""A fit whose median absolute error is above this (3 volatility points) does not describe the chain: no smile."""
VOLATILITY_BOUNDS = (0.01, 5.0)
SPREAD_FLOOR = 0.005
"""Smallest volatility spread used to weight a quote (a locked or very tight quote must not dominate the fit)."""
MAXIMUM_PROBABILITY_REPAIR = 0.01
"""A fitted curve whose cumulative probability must be corrected by more than this (it implies negative probabilities) is no distribution."""
GRID_POINTS = 2001
GRID_MINIMUM_LOG_RANGE = 1.5
GRID_TOTAL_VOLATILITIES = 10.0


def black_price(forward: float, strike: float, volatility: float, years: float, option_type: str) -> float:
    """Undiscounted Black (1976) value in the forward's currency; option_type "call" or "put"."""
    total = volatility * math.sqrt(years)
    first = (math.log(forward / strike) + 0.5 * total * total) / total
    second = first - total
    if option_type == "call":
        return forward * norm.cdf(first) - strike * norm.cdf(second)
    return strike * norm.cdf(-second) - forward * norm.cdf(-first)


def implied_volatility(price: float, forward: float, strike: float, years: float, option_type: str) -> Optional[float]:
    """The volatility at which ``black_price`` equals the price, or None when no volatility within ``VOLATILITY_BOUNDS`` does."""
    low, high = VOLATILITY_BOUNDS
    difference = lambda volatility: black_price(forward, strike, volatility, years, option_type) - price
    if price <= 0 or difference(low) > 0 or difference(high) < 0:
        return None
    return float(brentq(difference, low, high, xtol=1e-8))


def _svi_total_variance(parameters: Sequence[float], k: np.ndarray) -> np.ndarray:
    a, b, rho, m, sigma = parameters
    return a + b * (rho * (k - m) + np.sqrt((k - m) ** 2 + sigma ** 2))


@dataclass(frozen=True)
class PriceGrid:
    """A distribution of the settlement price as points and probabilities (the interface the candidate metrics use)."""

    prices: np.ndarray
    weights: np.ndarray

    def grid(self) -> Tuple[np.ndarray, np.ndarray]:
        """(prices ascending, probabilities summing to 1)."""
        return self.prices, self.weights


@dataclass(frozen=True)
class MarketSmile:
    """The fitted smile of one expiry."""

    forward: float
    years: float
    parameters: Tuple[float, float, float, float, float]
    """SVI (a, b, rho, m, sigma) in total variance."""
    median_error: float
    """Median absolute difference between fitted and quoted volatility, as a fraction."""
    quotes_used: int

    def volatility(self, strike: float) -> float:
        """The fitted volatility at a strike (a fraction)."""
        total = float(_svi_total_variance(self.parameters, np.array([math.log(strike / self.forward)]))[0])
        return math.sqrt(max(total, 1e-12) / self.years)

    @property
    def at_the_money_volatility(self) -> float:
        """The fitted volatility at the forward."""
        return self.volatility(self.forward)

    def _market_probabilities(self) -> Tuple[np.ndarray, np.ndarray, float]:
        """(log returns, probabilities, largest correction): the market's own distribution read from the fitted curve."""
        total_at_the_money = math.sqrt(max(float(_svi_total_variance(self.parameters, np.zeros(1))[0]), 1e-12))
        reach = max(GRID_TOTAL_VOLATILITIES * total_at_the_money, GRID_MINIMUM_LOG_RANGE)
        k = np.linspace(-reach, reach, GRID_POINTS)
        strikes = self.forward * np.exp(k)
        total = np.sqrt(np.maximum(_svi_total_variance(self.parameters, k), 1e-12))
        first = (-k + 0.5 * total * total) / total
        calls = self.forward * norm.cdf(first) - strikes * norm.cdf(first - total)
        raw = 1.0 + np.diff(calls) / np.diff(strikes)                                 # 1 + dC/dK between neighbouring strikes
        cumulative = np.clip(np.maximum.accumulate(raw), 0.0, 1.0)                    # never decreasing: no negative probability
        probabilities = np.diff(np.concatenate(([0.0], cumulative, [1.0])))          # includes the mass beyond both ends
        middles = np.sqrt(strikes[:-1] * strikes[1:])
        prices = np.concatenate(([strikes[0]], 0.5 * (middles[:-1] + middles[1:]), [strikes[-1]]))
        return np.log(prices / self.forward), probabilities / probabilities.sum(), float(np.max(np.abs(cumulative - raw)))

    @property
    def probability_repair(self) -> float:
        """The largest correction the cumulative probability needed (0 for a curve without arbitrage)."""
        return self._market_probabilities()[2]

    def distribution(self, view_volatility: Optional[float] = None) -> PriceGrid:
        """
        The distribution of the settlement price.

        Args:
            view_volatility: The annualised standard deviation wanted for the log return (a fraction); None: the market's own distribution.

        Raises:
            ValueError: for a view volatility that is not positive.
        """
        if view_volatility is not None and view_volatility <= 0:
            raise ValueError("the view volatility must be positive")
        log_returns, probabilities, _ = self._market_probabilities()
        mean = float((probabilities * log_returns).sum())
        factor = 1.0
        if view_volatility is not None:
            own = math.sqrt(float((probabilities * (log_returns - mean) ** 2).sum()))
            factor = view_volatility * math.sqrt(self.years) / own
        scaled = np.exp(mean + factor * (log_returns - mean))
        return PriceGrid(self.forward * scaled / float((probabilities * scaled).sum()), probabilities)     # the mean price is the forward


def fit_market_smile(quotes: Sequence[Quote], forward: float, years: float) -> Optional[MarketSmile]:
    """
    Fit the smile of one expiry to the mids of its two-sided out-of-the-money options (puts at or below the forward, calls above it).

    Each quote is weighted by one over its volatility spread (a tight quote says more); a quote whose spread cannot be expressed in volatility is
    left out rather than given an assumed weight. Returns None, never a made-up curve, when there are too few quotes, too few on one side, the fit
    misses the quotes by more than ``MAXIMUM_MEDIAN_ERROR``, or the curve implies probabilities that need a correction above
    ``MAXIMUM_PROBABILITY_REPAIR``.

    Raises:
        ValueError: for a forward or time that is not positive.
    """
    if forward <= 0 or years <= 0:
        raise ValueError("forward and years must be positive")
    points = []
    for quote in quotes:
        if not quote.bid_usd or not quote.ask_usd or quote.ask_usd < quote.bid_usd:
            continue
        if (quote.option_type == "put") != (quote.strike <= forward):
            continue
        mid = implied_volatility(0.5 * (quote.bid_usd + quote.ask_usd), forward, quote.strike, years, quote.option_type)
        if mid is None:
            continue
        bid = implied_volatility(quote.bid_usd, forward, quote.strike, years, quote.option_type)
        ask = implied_volatility(quote.ask_usd, forward, quote.strike, years, quote.option_type)
        if bid is None or ask is None:
            continue
        points.append((math.log(quote.strike / forward), mid, ask - bid))
    below = sum(1 for k, _, _ in points if k <= 0)
    if len(points) < MINIMUM_SMILE_QUOTES or below < MINIMUM_QUOTES_PER_SIDE or len(points) - below < MINIMUM_QUOTES_PER_SIDE:
        return None
    k = np.array([point[0] for point in points])
    volatilities = np.array([point[1] for point in points])
    scales = np.array([max(point[2], SPREAD_FLOOR) for point in points])
    level = float(np.median(volatilities) ** 2 * years)

    def residuals(parameters: np.ndarray) -> np.ndarray:
        total = np.maximum(_svi_total_variance(parameters, k), 1e-12)
        return (np.sqrt(total / years) - volatilities) / np.sqrt(scales)

    lower = [-level * 5.0, 0.0, -0.999, float(k.min()) - 0.5, 1e-4]
    upper = [level * 5.0, 2.0, 0.999, float(k.max()) + 0.5, 5.0]
    best = None
    for rho, sigma in ((-0.3, 0.1), (0.0, 0.3), (-0.7, 0.05)):                         # a few starts: the problem is not convex
        start = np.clip([level * 0.5, math.sqrt(level), rho, 0.0, sigma], lower, upper)
        result = least_squares(residuals, start, bounds=(lower, upper), loss="soft_l1", max_nfev=300)
        if best is None or result.cost < best.cost:
            best = result
    fitted = np.sqrt(np.maximum(_svi_total_variance(best.x, k), 1e-12) / years)
    error = float(np.median(np.abs(fitted - volatilities)))
    if error > MAXIMUM_MEDIAN_ERROR:
        return None
    smile = MarketSmile(forward, years, tuple(float(value) for value in best.x), error, len(points))
    return None if smile.probability_repair > MAXIMUM_PROBABILITY_REPAIR else smile
