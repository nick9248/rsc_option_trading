"""Hand-calculated tests of the small statistics used by the level backtest."""

import random

import pytest

from coding.core.analytics.level_signal_statistics import (
    average_ranks,
    group_mean_difference,
    moving_block_bootstrap_indices,
    moving_block_bootstrap_interval,
    paired_present,
    spearman_rank_correlation,
    two_sided_bootstrap_p_value,
)


def test_average_ranks_share_ties():
    assert average_ranks([10.0, 20.0, 20.0, 30.0]) == [1.0, 2.5, 2.5, 4.0]


def test_spearman_hand_calculated():
    # ranks equal the values; d = -1, 1, -1, 1, 0; rho = 1 - 6 * 4 / (5 * 24) = 0.8
    assert spearman_rank_correlation([1, 2, 3, 4, 5], [2, 1, 4, 3, 5]) == pytest.approx(0.8)
    assert spearman_rank_correlation([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)


def test_spearman_drops_missing_pairs_and_needs_three_points():
    assert spearman_rank_correlation([1, None, 2, 3], [1, 5, 2, 3]) == pytest.approx(1.0)
    assert spearman_rank_correlation([1, 2], [1, 2]) is None
    assert spearman_rank_correlation([1, 1, 1], [1, 2, 3]) is None   # constant series


def test_group_mean_difference():
    # in group: 1 and 10 -> 5.5; out: 2 and 3 -> 2.5; difference 3.0
    assert group_mean_difference([1.0, 2.0, 3.0, 10.0, None], [True, False, False, True, True]) == 3.0
    assert group_mean_difference([1.0, 2.0], [True, True]) is None


def test_missing_values_none_and_nan_are_dropped():
    nan = float("nan")
    # NaN pair dropped: [1, 2, 3] vs [1, 2, 3] -> perfect rank correlation
    assert spearman_rank_correlation([1.0, nan, 2.0, 3.0], [1.0, 5.0, 2.0, 3.0]) == pytest.approx(1.0)
    # in group: 1 (NaN dropped) -> 1.0; out: 2 (None group ignored, NaN value dropped) -> 2.0
    assert group_mean_difference([1.0, nan, 2.0, 7.0, nan], [True, True, False, None, False]) == -1.0
    # only rows 0 and 3 have both values present
    assert paired_present([1.0, None, nan, 4.0], [10.0, 20.0, 30.0, 40.0]) == ([1.0, 4.0], [10.0, 40.0])


def test_moving_block_bootstrap_indices_are_wrapped_blocks():
    indices = moving_block_bootstrap_indices(10, 3, random.Random(1))
    assert len(indices) == 10
    for block_start in range(0, 9, 3):
        block = indices[block_start:block_start + 3]
        assert all((later - earlier) % 10 == 1 for earlier, later in zip(block, block[1:]))


def test_bootstrap_interval_of_a_constant_statistic():
    interval = moving_block_bootstrap_interval(20, lambda positions: 1.0, 5, 200, seed=3)
    assert interval["low"] == 1.0 and interval["high"] == 1.0
    assert interval["share_at_or_below_zero"] == 0.0
    assert two_sided_bootstrap_p_value(interval["share_at_or_below_zero"]) == 0.0


def test_bootstrap_interval_is_none_with_too_few_valid_resamples():
    interval = moving_block_bootstrap_interval(20, lambda positions: None, 5, 200, seed=3)
    assert interval["low"] is None and interval["valid_resamples"] == 0


def test_two_sided_bootstrap_p_value():
    assert two_sided_bootstrap_p_value(0.01) == pytest.approx(0.02)
    assert two_sided_bootstrap_p_value(0.99) == pytest.approx(0.02)
    assert two_sided_bootstrap_p_value(0.5) == 1.0
    assert two_sided_bootstrap_p_value(None) is None
