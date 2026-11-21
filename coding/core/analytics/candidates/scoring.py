"""
Scoring rules for candidates and their evaluation against what really happened (pure, numpy).

A rule turns the metrics of a candidate into one number; the candidate with the highest number is the rule's pick. A rule is judged on many
dates: on each date, did the pick earn more than the average candidate of that date, and did the rule's ranking of all candidates agree with the
realised ranking? Results are in units of the maximum loss ("R"), so structures of different width are comparable.
"""

import math
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence

import numpy as np

from coding.core.analytics.candidates.metrics import CandidateMetrics

REALISED = "realised"
IMPLIED = "implied"
"""Names of the two distributions the lab computes: a lognormal with the recent realised volatility, and one with the market's implied."""


def _distribution(metrics: CandidateMetrics, name: str):
    return metrics.by_distribution.get(name)


def _expected_per_risk(name: str) -> Callable[[CandidateMetrics], Optional[float]]:
    def score(metrics: CandidateMetrics) -> Optional[float]:
        result = _distribution(metrics, name)
        return None if result is None else result.expected_pnl_usd / metrics.max_loss_usd
    return score


def _probability(metrics: CandidateMetrics) -> Optional[float]:
    result = _distribution(metrics, REALISED)
    return None if result is None else result.probability_of_profit


def _expected_per_tail(metrics: CandidateMetrics) -> Optional[float]:
    result = _distribution(metrics, REALISED)
    if result is None or result.tail_loss_usd >= 0:
        return None
    return result.expected_pnl_usd / abs(result.tail_loss_usd)


def _reward_to_risk(metrics: CandidateMetrics) -> Optional[float]:
    return None if metrics.max_profit_usd is None else metrics.max_profit_usd / metrics.max_loss_usd      # unlimited profit: no ratio


def _cheapness(metrics: CandidateMetrics) -> float:
    return -(metrics.spread_cost_usd + metrics.trading_fees_usd) / metrics.max_loss_usd


RULES: Dict[str, Callable[[CandidateMetrics], Optional[float]]] = {
    "expected_pnl_per_risk_realised_vol": _expected_per_risk(REALISED),
    "expected_pnl_per_risk_implied_vol": _expected_per_risk(IMPLIED),
    "probability_of_profit_realised_vol": _probability,
    "expected_pnl_per_tail_loss_realised_vol": _expected_per_tail,
    "reward_to_risk": _reward_to_risk,
    "lowest_cost": _cheapness,
}


@dataclass(frozen=True)
class CandidateOutcome:
    """A candidate with its metrics at entry and what it really earned."""

    family: str
    metrics: CandidateMetrics
    realised_pnl_usd: float

    @property
    def realised_r(self) -> float:
        """The realised PnL in units of the maximum loss."""
        return self.realised_pnl_usd / self.metrics.max_loss_usd


@dataclass(frozen=True)
class RuleReport:
    """How a rule did over many dates."""

    rule: str
    dates: int
    mean_pick_r: Optional[float]
    mean_all_r: Optional[float]
    mean_excess_r: Optional[float]
    """Pick minus the average candidate, per date, averaged."""
    t_statistic: Optional[float]
    """Of the excess over the dates (dates a week apart overlap in time, so read it as optimistic)."""
    positive_dates: Optional[float]
    """Share of dates where the pick beat the average candidate."""
    mean_rank_correlation: Optional[float]
    """Spearman correlation between the rule's score and the realised R of all candidates of a date, averaged."""


def _ranks(values: np.ndarray) -> np.ndarray:
    """Ranks from 0, equal values sharing the average of their ranks (otherwise ties would look like order)."""
    order = values.argsort(kind="mergesort")
    sorted_values = values[order]
    ranks = np.empty(len(values))
    start = 0
    while start < len(values):
        end = start
        while end + 1 < len(values) and sorted_values[end + 1] == sorted_values[start]:
            end += 1
        ranks[order[start:end + 1]] = (start + end) / 2.0
        start = end + 1
    return ranks


def spearman(first: Sequence[float], second: Sequence[float]) -> Optional[float]:
    """Rank correlation; None when it is undefined (fewer than 3 values or no variation)."""
    a, b = _ranks(np.asarray(first, dtype=float)), _ranks(np.asarray(second, dtype=float))
    if len(a) < 3 or a.std() == 0 or b.std() == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def evaluate_rule(rule: str, dates: Sequence[Sequence[CandidateOutcome]]) -> RuleReport:
    """
    Judge a rule over dates; each date is the list of all candidates of that date with their outcomes.

    Raises:
        KeyError: for an unknown rule.
    """
    score_of = RULES[rule]
    pick_r: List[float] = []
    all_r: List[float] = []
    correlations: List[float] = []
    for outcomes in dates:
        scored = [(score_of(outcome.metrics), outcome) for outcome in outcomes]
        scored = [(score, outcome) for score, outcome in scored if score is not None and math.isfinite(score)]
        if len(scored) < 3:
            continue
        best = max(scored, key=lambda pair: pair[0])[1]
        pick_r.append(best.realised_r)
        all_r.append(float(np.mean([outcome.realised_r for _, outcome in scored])))
        correlation = spearman([score for score, _ in scored], [outcome.realised_r for _, outcome in scored])
        if correlation is not None:
            correlations.append(correlation)
    count = len(pick_r)
    if count == 0:
        return RuleReport(rule, 0, None, None, None, None, None, None)
    excess = np.asarray(pick_r) - np.asarray(all_r)
    t_statistic = float(excess.mean() / (excess.std(ddof=1) / math.sqrt(count))) if count > 1 and excess.std(ddof=1) > 0 else None
    return RuleReport(rule, count, float(np.mean(pick_r)), float(np.mean(all_r)), float(excess.mean()), t_statistic,
                      float((excess > 0).mean()), float(np.mean(correlations)) if correlations else None)
