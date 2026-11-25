"""The comparison statistics: hand-checked scores, the pick rule and the criteria."""

import math
from datetime import date

import numpy as np
import pytest

from coding.core.analytics.validation.model_comparison import (
    ALL_MODELS, CANDIDATE_MODELS, CURRENT_MODELS, correctness, crps, pick_result, probability_integral_transform, report_lines, rule_result)


def test_crps_of_a_sure_forecast_is_the_distance_and_of_a_two_point_forecast_is_hand_calculated():
    assert crps(np.array([2.0]), np.array([1.0]), 5.0) == pytest.approx(3.0)
    # X is -1 or +1 with 1/2: E|X - 0| = 1, E|X - X'| = 1, score 1 - 0.5 = 0.5
    assert crps(np.array([-1.0, 1.0]), np.array([0.5, 0.5]), 0.0) == pytest.approx(0.5)


def test_crps_matches_the_closed_form_of_a_normal_forecast():
    x = np.linspace(-8, 8, 4001)
    w = np.exp(-0.5 * x * x)
    w /= w.sum()
    y = 0.7
    closed = y * (2 * 0.5 * (1 + math.erf(y / math.sqrt(2))) - 1) + 2 * math.exp(-0.5 * y * y) / math.sqrt(2 * math.pi) - 1 / math.sqrt(math.pi)
    assert crps(x, w, y) == pytest.approx(closed, abs=1e-4)
    assert probability_integral_transform(x, w, y) == pytest.approx(0.5 * (1 + math.erf(y / math.sqrt(2))), abs=1e-3)
    assert probability_integral_transform(x, w, -20.0) == 0.0 and probability_integral_transform(x, w, 20.0) == 1.0


def candidate(current, new, r):
    scores = {**{model: current for model in CURRENT_MODELS}, "smile": -0.01, **{model: new for model in CANDIDATE_MODELS}}
    return {"r": r, "won": r > 0, "costs_usd": 10.0, "expected_per_risk": scores, "expected_usd": {**{m: -10.0 for m in ALL_MODELS}, "implied_t": 40.0},
            "probability": {model: 0.6 for model in ALL_MODELS}}


def entry(index, candidates):
    return {"currency": "BTC", "day": date(2026, 4, 1 + index % 28), "horizon": 7, "cluster": f"BTC {index % 6}", "candidates": candidates,
            "crps": {model: 0.02 for model in ALL_MODELS}, "pit": {model: 0.4 for model in ALL_MODELS}, "option_errors": [1.0, 2.0], "half_spreads": [30.0, 30.0]}


def test_a_rule_takes_its_best_positive_candidate_and_otherwise_does_not_trade():
    candidates = [candidate(0.02, -0.01, -1.0), candidate(0.01, 0.03, 0.5)]
    assert pick_result(candidates, CURRENT_MODELS) == (-1.0, True) and pick_result(candidates, CANDIDATE_MODELS) == (0.5, True)
    assert pick_result([candidate(-0.01, -0.02, 0.9)], CURRENT_MODELS) == (0.0, False)
    result = rule_result([entry(i, candidates) for i in range(12)], CANDIDATE_MODELS, draws=100)
    assert result.traded_share == 1.0 and result.mean_r_of_trades == 0.5 and result.log_growth.estimate == pytest.approx(math.log1p(0.025))


def test_correctness_passes_for_a_model_that_returns_minus_costs_and_shows_the_current_gap():
    outcome = correctness([entry(0, [candidate(0.0, 0.0, 0.1)])])
    assert outcome.passed and outcome.candidate_gap_usd == 0.0 and outcome.current_gap_usd == 50.0 and outcome.option_error_usd == 1.5


def test_the_report_states_the_three_criteria_the_decision_and_that_an_edge_is_not_shown_for_noise():
    rng = np.random.default_rng(0)
    entries = [entry(i, [candidate(0.01, 0.02, float(rng.choice([-1.0, 1.0])))]) for i in range(60)]
    text = "\n".join(report_lines(entries, draws=100))
    for wanted in ("C1 PASSED", "C2 PASSED", "C3 PASSED", "DECISION: ADOPT", "EDGE: not shown"):
        assert wanted in text, wanted
    assert report_lines([]) == ["No entries: nothing to compare."]


def test_a_clearly_worse_candidate_rule_fails_the_third_criterion():
    good_then_bad = [candidate(0.02, -0.01, 1.0), candidate(-0.01, 0.02, -1.0)]
    text = "\n".join(report_lines([entry(i, good_then_bad) for i in range(60)], draws=100))
    assert "C3 FAILED" in text and "DECISION: DO NOT ADOPT" in text
