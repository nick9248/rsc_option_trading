"""
Does a ranking rule have an edge? Statistics that respect how little data there is (pure, numpy).

Daily entries overlap (one expiry is entered on several days), so trades are not independent: every interval here is a block bootstrap over
CLUSTERS (for example coin x expiry). The yardstick is the expected log growth of an account that risks a fixed fraction of its capital on
each trade (the Kelly criterion's objective): it punishes lotteries (rare huge wins) that a plain mean flatters.
"""

from dataclasses import dataclass
from typing import Callable, Optional, Sequence, Tuple

import numpy as np

BOOTSTRAP_DRAWS = 1000
LOSS_FLOOR = -1.0
"""A defined-risk trade cannot lose more than its maximum loss: R is at least -1."""


def expected_log_growth(results_in_r: Sequence[float], risk_fraction: float) -> float:
    """
    Mean of log(1 + risk_fraction x R) over trades; R is the result in units of the maximum loss.

    Raises:
        ValueError: for no trades, a risk fraction outside (0, 1], or an R below -1.
    """
    r = np.asarray(results_in_r, dtype=float)
    if r.size == 0:
        raise ValueError("no trades")
    if not 0.0 < risk_fraction <= 1.0:
        raise ValueError("the risk fraction must be in (0, 1]")
    if (r < LOSS_FLOOR - 1e-9).any():
        raise ValueError("a defined-risk trade cannot lose more than its maximum loss (R >= -1)")
    return float(np.mean(np.log1p(risk_fraction * np.maximum(r, LOSS_FLOOR))))


@dataclass(frozen=True)
class ClusterInterval:
    """A statistic with its cluster-bootstrap 95% interval."""

    estimate: float
    low: float
    high: float
    clusters: int

    @property
    def excludes_zero(self) -> bool:
        """True when the whole interval is on one side of zero."""
        return self.low > 0.0 or self.high < 0.0


def cluster_bootstrap(values: Sequence[float], clusters: Sequence[object], statistic: Callable[[np.ndarray], float],
                      draws: int = BOOTSTRAP_DRAWS, seed: int = 0) -> ClusterInterval:
    """
    The statistic of ``values`` with a 95% percentile interval from resampling whole clusters with replacement.

    Raises:
        ValueError: for mismatched lengths or fewer than 2 clusters (no interval can be formed).
    """
    values_array = np.asarray(values, dtype=float)
    labels = np.asarray([str(label) for label in clusters])
    if len(values_array) != len(labels):
        raise ValueError("one cluster label per value")
    names = sorted(set(labels))
    if len(names) < 2:
        raise ValueError("a cluster bootstrap needs at least 2 clusters")
    members = [np.where(labels == name)[0] for name in names]
    generator = np.random.default_rng(seed)
    results = []
    for _ in range(draws):
        picked = generator.integers(0, len(names), len(names))
        results.append(statistic(values_array[np.concatenate([members[index] for index in picked])]))
    low, high = np.percentile(results, [2.5, 97.5])
    return ClusterInterval(float(statistic(values_array)), float(low), float(high), len(names))


def calibration_table(predicted: Sequence[float], happened: Sequence[bool], edges: Sequence[float] = (0.0, 0.2, 0.4, 0.6, 0.8, 0.95, 1.0001),
                      minimum_count: int = 30) -> Sequence[Tuple[float, float, float, int]]:
    """
    (mean predicted, observed frequency, bin lower edge, count) per probability bin with at least ``minimum_count`` cases.
    A calibrated forecaster has the first two equal.
    """
    p = np.asarray(predicted, dtype=float)
    y = np.asarray(happened, dtype=float)
    rows = []
    for low, high in zip(edges[:-1], edges[1:]):
        selected = (p >= low) & (p < high)
        if selected.sum() >= minimum_count:
            rows.append((float(p[selected].mean()), float(y[selected].mean()), float(low), int(selected.sum())))
    return rows


def kelly_fraction(outcomes_in_r: np.ndarray, weights: np.ndarray, maximum: float = 1.0, steps: int = 400) -> float:
    """
    The fraction of capital to risk per trade that maximises the expected log growth under a distribution of results (R per outcome, with
    probabilities). 0 when no positive fraction improves on staying out.
    """
    fractions = np.linspace(0.0, maximum, steps + 1)
    growth = [float((weights * np.log1p(f * np.maximum(outcomes_in_r, LOSS_FLOOR))).sum()) for f in fractions]
    best = int(np.argmax(growth))
    return float(fractions[best]) if growth[best] > growth[0] + 1e-12 else 0.0


def fractional_kelly_risk(kelly: float, fraction: float = 0.1, shrink: float = 0.5, cap: Optional[float] = None) -> float:
    """
    The risk fraction to use: a fraction of the Kelly fraction, shrunk because the edge is estimated with large error (the full Kelly
    fraction of an overestimated edge is a path to ruin); never above ``cap`` (the drawdown budget per trade).
    """
    if kelly < 0 or not 0 < fraction <= 1 or not 0 < shrink <= 1:
        raise ValueError("kelly must be >= 0, fraction and shrink in (0, 1]")
    risk = kelly * fraction * shrink
    return min(risk, cap) if cap is not None else risk
