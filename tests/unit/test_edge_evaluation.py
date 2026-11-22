"""Edge statistics: hand-calculated values and the properties that make them trustworthy."""

import math

import numpy as np
import pytest

from coding.core.analytics.validation.edge_evaluation import (
    calibration_table, cluster_bootstrap, expected_log_growth, fractional_kelly_risk, kelly_fraction)


def test_expected_log_growth_is_hand_calculated_and_punishes_a_lottery_more_than_a_mean_shows():
    # two trades, R = +1 and -1, risking 10%: (log 1.1 + log 0.9) / 2
    assert expected_log_growth([1.0, -1.0], 0.10) == pytest.approx((math.log(1.1) + math.log(0.9)) / 2)
    lottery = [-1.0] * 19 + [30.0]                       # mean R +0.55, but 95% of the trades lose everything they risk
    assert np.mean(lottery) > 0 and expected_log_growth(lottery, 0.5) < 0       # at a 50% risk the account shrinks despite the positive mean


def test_expected_log_growth_refuses_impossible_input():
    for bad in ((), ):
        with pytest.raises(ValueError):
            expected_log_growth(bad, 0.1)
    with pytest.raises(ValueError):
        expected_log_growth([0.1], 0.0)
    with pytest.raises(ValueError):
        expected_log_growth([-1.5], 0.1)


def test_the_cluster_bootstrap_is_wider_than_an_independent_one_when_trades_in_a_cluster_move_together():
    rng = np.random.default_rng(0)
    cluster_effect = rng.normal(0, 1.0, 30)
    values = np.repeat(cluster_effect, 10) + rng.normal(0, 0.1, 300)         # 10 near-identical trades per cluster
    labels = np.repeat(np.arange(30), 10)
    clustered = cluster_bootstrap(values, labels, np.mean, draws=500)
    naive = cluster_bootstrap(values, np.arange(300), np.mean, draws=500)
    assert (clustered.high - clustered.low) > 2 * (naive.high - naive.low)
    assert clustered.clusters == 30 and clustered.low <= clustered.estimate <= clustered.high


def test_the_bootstrap_interval_excludes_zero_for_a_clear_effect_and_not_for_noise():
    labels = np.repeat(np.arange(40), 5)
    assert cluster_bootstrap(np.full(200, 0.5), labels, np.mean, draws=200).excludes_zero
    noise = np.random.default_rng(3).normal(0, 1, 200)
    assert not cluster_bootstrap(noise, labels, np.mean, draws=200).excludes_zero


def test_the_bootstrap_needs_matching_labels_and_two_clusters():
    with pytest.raises(ValueError):
        cluster_bootstrap([1.0, 2.0], ["a"], np.mean)
    with pytest.raises(ValueError):
        cluster_bootstrap([1.0, 2.0], ["a", "a"], np.mean)


def test_calibration_of_a_perfect_forecaster_matches_and_a_bin_with_few_cases_is_left_out():
    rng = np.random.default_rng(4)
    p = rng.uniform(0, 1, 20000)
    happened = rng.uniform(0, 1, 20000) < p
    for mean_predicted, observed, _, count in calibration_table(p, happened):
        assert abs(mean_predicted - observed) < 0.03 and count >= 30
    assert calibration_table([0.5] * 10, [True] * 10) == []


def test_kelly_matches_the_textbook_even_money_bet_and_is_zero_without_an_edge():
    # win R = +1 with probability 0.6, lose R = -1 with 0.4: the Kelly fraction is 2p - 1 = 0.2
    assert kelly_fraction(np.array([1.0, -1.0]), np.array([0.6, 0.4])) == pytest.approx(0.2, abs=0.005)
    assert kelly_fraction(np.array([1.0, -1.0]), np.array([0.5, 0.5])) == 0.0
    assert kelly_fraction(np.array([1.0, -1.0]), np.array([0.4, 0.6])) == 0.0


def test_the_used_risk_is_a_small_shrunk_fraction_of_kelly_and_never_above_the_cap():
    assert fractional_kelly_risk(0.2, fraction=0.1, shrink=0.5) == pytest.approx(0.01)
    assert fractional_kelly_risk(0.2, fraction=1.0, shrink=1.0, cap=0.05) == 0.05
    with pytest.raises(ValueError):
        fractional_kelly_risk(-0.1)
