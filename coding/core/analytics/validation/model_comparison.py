"""
Do two ways of modelling the settlement price differ in how well they forecast it and in the structures they pick? (pure, numpy)

The criteria are those of documentation/research/skew_model_test_2026-11-25.md, written before any result: C1 the market-smile model at the market's
own volatility prices options as the market does; C2 its width-scaled versions forecast the price no worse than the one-volatility models at the same
volatility; C3 the ranking built on them earns no less than the previous one. Entries overlap, so every interval is a block bootstrap over
(currency, expiry) clusters.
"""

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from coding.core.analytics.validation.edge_evaluation import ClusterInterval, calibration_table, cluster_bootstrap

CURRENT_MODELS = ("realised", "implied_t", "har_t")
"""The three one-volatility models of the pipeline's ranking up to pipeline-6."""
CANDIDATE_MODELS = ("smile_rv", "smile_har")
MARKET_MODEL = "smile"
ALL_MODELS = CURRENT_MODELS + (MARKET_MODEL,) + CANDIDATE_MODELS
FORECAST_PAIRS = (("smile_rv", "realised"), ("smile_har", "har_t"))
"""C2 compares each width-scaled smile with the one-volatility model of the same volatility."""
RISK_FRACTION = 0.05
BOOTSTRAP_DRAWS = 2000
COVERAGE_LEVELS = (0.5, 0.8, 0.9)


def crps(points: np.ndarray, weights: np.ndarray, outcome: float) -> float:
    """
    Continuous ranked probability score of a discrete forecast (lower is better): E|X - y| - 1/2 E|X - X'|.

    Args:
        points: Ascending support of the forecast.
        weights: Probabilities of the points (sum to 1).
        outcome: What happened.
    """
    distance = float(np.sum(weights * np.abs(points - outcome)))
    cumulative = np.cumsum(weights)[:-1]
    half_self_distance = float(np.sum(cumulative * (1.0 - cumulative) * np.diff(points)))
    return distance - half_self_distance


def probability_integral_transform(points: np.ndarray, weights: np.ndarray, outcome: float) -> float:
    """The forecast probability of finishing at or below the outcome (each point's weight centred on it); uniform for a calibrated forecast."""
    centred = np.cumsum(weights) - 0.5 * weights
    return float(np.interp(outcome, points, centred, left=0.0, right=1.0))


def score(candidate: Dict[str, Any], models: Sequence[str]) -> float:
    """The lowest expected PnL per unit of maximum loss of a candidate over the models."""
    return min(candidate["expected_per_risk"][model] for model in models)


def pick_result(candidates: Sequence[Dict[str, Any]], models: Sequence[str]) -> Tuple[float, bool]:
    """(realised R of the rule's pick, traded): the best-scoring candidate when its score is positive; otherwise no trade, which earns 0."""
    best = max(candidates, key=lambda candidate: score(candidate, models))
    if score(best, models) <= 0:
        return 0.0, False
    return float(best["r"]), True


def _growth(values: np.ndarray) -> float:
    return float(np.mean(np.log1p(RISK_FRACTION * np.maximum(values, -1.0))))


@dataclass(frozen=True)
class RuleResult:
    """One ranking rule over a set of entries."""

    entries: int
    traded_share: float
    mean_r_of_trades: Optional[float]
    log_growth: ClusterInterval


def rule_result(entries: Sequence[Dict[str, Any]], models: Sequence[str], draws: int = BOOTSTRAP_DRAWS) -> RuleResult:
    """The rule's expected log growth per entry at ``RISK_FRACTION``, with its cluster-bootstrap interval."""
    results = [pick_result(entry["candidates"], models) for entry in entries]
    values = np.array([result for result, _ in results])
    traded = [result for result, did in results if did]
    interval = cluster_bootstrap(values, [entry["cluster"] for entry in entries], _growth, draws=draws)
    return RuleResult(len(entries), len(traded) / len(entries), float(np.mean(traded)) if traded else None, interval)


def paired_growth_difference(entries: Sequence[Dict[str, Any]], draws: int = BOOTSTRAP_DRAWS) -> ClusterInterval:
    """Mean per-entry difference of log growth, candidate rule minus current rule, on the same entries."""
    difference = np.array([np.log1p(RISK_FRACTION * max(pick_result(entry["candidates"], CANDIDATE_MODELS)[0], -1.0))
                           - np.log1p(RISK_FRACTION * max(pick_result(entry["candidates"], CURRENT_MODELS)[0], -1.0)) for entry in entries])
    return cluster_bootstrap(difference, [entry["cluster"] for entry in entries], lambda values: float(np.mean(values)), draws=draws)


@dataclass(frozen=True)
class CorrectnessResult:
    """C1: does the market-smile model at the market's own volatility price what the market prices?"""

    option_error_usd: float
    """Median absolute difference between an option's value under the smile and its mid."""
    half_spread_usd: float
    """Median half-spread of the same options."""
    candidate_gap_usd: float
    """Median of (expected PnL under the smile) minus (minus costs) over the candidates."""
    candidate_cost_usd: float
    current_gap_usd: float
    """The same gap for the current implied one-volatility model, for comparison."""

    @property
    def passed(self) -> bool:
        """Both parts of C1."""
        return self.option_error_usd <= self.half_spread_usd and abs(self.candidate_gap_usd) <= 0.10 * self.candidate_cost_usd


def correctness(entries: Sequence[Dict[str, Any]]) -> CorrectnessResult:
    """C1 over all entries."""
    errors = np.concatenate([np.asarray(entry["option_errors"]) for entry in entries])
    halves = np.concatenate([np.asarray(entry["half_spreads"]) for entry in entries])
    candidates = [candidate for entry in entries for candidate in entry["candidates"]]
    gaps = [candidate["expected_usd"][MARKET_MODEL] + candidate["costs_usd"] for candidate in candidates]
    current = [candidate["expected_usd"]["implied_t"] + candidate["costs_usd"] for candidate in candidates]
    return CorrectnessResult(float(np.median(errors)), float(np.median(halves)), float(np.median(gaps)),
                             float(np.median([candidate["costs_usd"] for candidate in candidates])), float(np.median(current)))


def forecast_difference(entries: Sequence[Dict[str, Any]], model: str, reference: str, draws: int = BOOTSTRAP_DRAWS) -> ClusterInterval:
    """Mean CRPS of ``model`` minus that of ``reference`` (negative: the model forecasts better)."""
    difference = np.array([entry["crps"][model] - entry["crps"][reference] for entry in entries])
    return cluster_bootstrap(difference, [entry["cluster"] for entry in entries], lambda values: float(np.mean(values)), draws=draws)


def coverage(entries: Sequence[Dict[str, Any]], model: str) -> List[float]:
    """Share of outcomes inside the central 50, 80 and 90 percent of the forecast."""
    transforms = np.array([entry["pit"][model] for entry in entries])
    return [float(np.mean(np.abs(transforms - 0.5) <= level / 2.0)) for level in COVERAGE_LEVELS]


def slope(entries: Sequence[Dict[str, Any]], models: Sequence[str], draws: int = 600) -> ClusterInterval:
    """Slope of the realised R on the rule's score over all candidates (1 for a score that means what it says, 0 for no information)."""
    pairs = [(score(candidate, models), candidate["r"], entry["cluster"]) for entry in entries for candidate in entry["candidates"]]
    scores = np.array([pair[0] for pair in pairs])
    results = np.array([pair[1] for pair in pairs])
    labels = [pair[2] for pair in pairs]
    return cluster_bootstrap(np.arange(len(pairs)), labels,
                             lambda index: float(np.polyfit(scores[index.astype(int)], results[index.astype(int)], 1)[0]), draws=draws)


def win_calibration(entries: Sequence[Dict[str, Any]], model: str) -> float:
    """Mean absolute gap between predicted and observed win frequency over the probability bins, weighted by their size."""
    predicted = [candidate["probability"][model] for entry in entries for candidate in entry["candidates"]]
    happened = [candidate["won"] for entry in entries for candidate in entry["candidates"]]
    bins = calibration_table(predicted, happened)
    total = sum(row.count for row in bins)
    return math.nan if total == 0 else sum(abs(row.mean_predicted - row.observed_frequency) * row.count for row in bins) / total


def report_lines(entries: Sequence[Dict[str, Any]], draws: int = BOOTSTRAP_DRAWS) -> List[str]:
    """The whole comparison as plain text lines, ending with the three criteria and the decision."""
    if len({entry["cluster"] for entry in entries}) < 2:
        return ["No entries from at least two currency x expiry clusters: nothing to compare."]
    lines = [f"Entries {len(entries)}, candidates {sum(len(entry['candidates']) for entry in entries):,}, "
             f"clusters (currency x expiry) {len({entry['cluster'] for entry in entries})}"]
    first = correctness(entries)
    lines += ["", "C1 correctness (market smile at its own width)",
              f"  option value minus mid: median {first.option_error_usd:.4g} USD against a median half-spread of {first.half_spread_usd:.4g} USD",
              f"  candidate expected PnL minus (minus costs): median {first.candidate_gap_usd:+.4g} USD (costs {first.candidate_cost_usd:.4g}); "
              f"current implied one-volatility model: {first.current_gap_usd:+.4g} USD",
              f"  C1 {'PASSED' if first.passed else 'FAILED'}"]
    lines += ["", "C2 forecast of the settlement (CRPS of the log return, lower is better; coverage of the central 50/80/90%)"]
    for model in ALL_MODELS:
        mean = float(np.mean([entry["crps"][model] for entry in entries]))
        lines.append(f"  {model:10} CRPS {mean:.5f}  coverage {' / '.join(f'{value:.2f}' for value in coverage(entries, model))}")
    second = True
    for model, reference in FORECAST_PAIRS:
        difference = forecast_difference(entries, model, reference, draws)
        worse = difference.low > 0
        second = second and not worse
        lines.append(f"  {model} minus {reference}: {difference.estimate:+.5f}  95% [{difference.low:+.5f}, {difference.high:+.5f}]"
                     + ("  WORSE" if worse else ""))
    lines.append(f"  C2 {'PASSED' if second else 'FAILED'}")
    lines += ["", "C3 ranking (take the top candidate only if its score is positive; expected log growth per entry at 5% risk)"]
    subsets = [("all", entries)]
    subsets += [(f"{days}-day expiries", [entry for entry in entries if entry["horizon"] == days]) for days in sorted({entry["horizon"] for entry in entries})]
    subsets += [(currency, [entry for entry in entries if entry["currency"] == currency]) for currency in sorted({entry["currency"] for entry in entries})]
    middle = sorted(entry["day"] for entry in entries)[len(entries) // 2]
    subsets += [("first half", [entry for entry in entries if entry["day"] < middle]), ("second half", [entry for entry in entries if entry["day"] >= middle])]
    for name, subset in subsets:
        if len({entry["cluster"] for entry in subset}) < 2:
            continue
        for label, models in (("current", CURRENT_MODELS), ("candidate", CANDIDATE_MODELS)):
            result = rule_result(subset, models, draws)
            growth = result.log_growth
            lines.append(f"  {name:16} {label:9} traded {result.traded_share:5.0%}  mean R of trades "
                         f"{'n/a' if result.mean_r_of_trades is None else f'{result.mean_r_of_trades:+.3f}'}  log growth {100 * growth.estimate:+.4f}% "
                         f"[{100 * growth.low:+.4f}, {100 * growth.high:+.4f}]  (n={result.entries})")
    average = np.array([np.mean([candidate["r"] for candidate in entry["candidates"]]) for entry in entries])
    lines.append(f"  benchmark: the average candidate of each entry, always traded: log growth {100 * _growth(average):+.4f}%")
    difference = paired_growth_difference(entries, draws)
    third = difference.high >= 0
    lines += [f"  candidate minus current: {100 * difference.estimate:+.4f}%  95% [{100 * difference.low:+.4f}, {100 * difference.high:+.4f}]",
              f"  C3 {'PASSED' if third else 'FAILED'}"]
    lines += ["", "Reported, not criteria"]
    candidates = [candidate for entry in entries for candidate in entry["candidates"]]
    for model in ("implied_t", MARKET_MODEL):          # the signed median of C1 lets gains and losses cancel; the absolute one does not
        gap = float(np.median([abs(candidate["expected_usd"][model] + candidate["costs_usd"]) for candidate in candidates]))
        lines.append(f"  median ABSOLUTE gap between the expected PnL under {model} and minus costs: {gap:.4g} USD")
    for label, models in (("current", CURRENT_MODELS), ("candidate", CANDIDATE_MODELS)):
        value = slope(entries, models)
        lines.append(f"  slope of realised R on the {label} score: {value.estimate:+.2f} [{value.low:+.2f}, {value.high:+.2f}] (1 = means what it says)")
    lines.append("  win probability, mean absolute calibration gap: " + ", ".join(f"{model} {win_calibration(entries, model):.3f}" for model in ALL_MODELS))
    own = rule_result(entries, CANDIDATE_MODELS, draws).log_growth
    if own.low > 0:
        edge = "the candidate rule is above zero with 95% confidence"
    elif own.high < 0:
        edge = "none: the candidate rule LOSES against not trading with 95% confidence (C3 only compares it with the current rule)"
    else:
        edge = "not shown (the interval of the candidate rule includes zero)"
    lines += ["", f"DECISION by C1 to C3: {'ADOPT' if first.passed and second and third else 'DO NOT ADOPT'} the market-smile models "
                  f"(C1 {'passed' if first.passed else 'failed'}, C2 {'passed' if second else 'failed'}, C3 {'passed' if third else 'failed'}). "
                  "It binds only for the run the criteria were written for.",
              f"EDGE: {edge}"]
    return lines
