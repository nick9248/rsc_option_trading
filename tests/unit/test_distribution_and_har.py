"""Student-t distribution and the HAR-RV forecast: hand-checked properties."""

import math

import numpy as np
import pytest

from coding.core.analytics.candidates.distribution import LognormalDistribution, StudentTDistribution
from coding.core.analytics.har_volatility import MINIMUM_RETURNS, har_annual_volatility

FORWARD, VOL, DAYS = 60_000.0, 0.5, 7.0


def test_the_student_t_distribution_has_the_forward_as_mean_and_the_requested_log_volatility():
    prices, weights = StudentTDistribution(FORWARD, VOL, DAYS).grid()
    assert weights.sum() == pytest.approx(1.0) and float((prices * weights).sum()) == pytest.approx(FORWARD, rel=1e-9)
    log_returns = np.log(prices / FORWARD)
    mean = (log_returns * weights).sum()
    std = math.sqrt(((log_returns - mean) ** 2 * weights).sum())
    assert std == pytest.approx(VOL * math.sqrt(DAYS / 365.0), rel=0.03)         # a little lost beyond the 12-scale grid limit


def test_the_student_t_has_fatter_tails_than_the_lognormal_of_the_same_volatility():
    def tail(distribution):
        prices, weights = distribution.grid()
        return float(weights[np.abs(np.log(prices / FORWARD)) > 4 * VOL * math.sqrt(DAYS / 365.0)].sum())
    assert tail(StudentTDistribution(FORWARD, VOL, DAYS)) > 10 * tail(LognormalDistribution(FORWARD, VOL, DAYS))


def test_a_student_t_with_too_few_degrees_of_freedom_or_bad_input_is_refused():
    with pytest.raises(ValueError):
        StudentTDistribution(FORWARD, VOL, DAYS, degrees_of_freedom=2.0)
    with pytest.raises(ValueError):
        StudentTDistribution(FORWARD, 0.0, DAYS)


def test_har_recovers_a_constant_volatility_and_gives_none_without_enough_history():
    rng = np.random.default_rng(1)
    returns = rng.normal(0.0, 0.03, 600)                                          # a daily 3% volatility, i.e. 0.03 x sqrt(365) = 57% a year
    forecast = har_annual_volatility(returns, 7)
    assert forecast == pytest.approx(0.03 * math.sqrt(365.0), rel=0.2)
    assert har_annual_volatility(returns[:MINIMUM_RETURNS - 1], 7) is None and har_annual_volatility(returns, 0) is None


def test_har_follows_volatility_clustering():
    rng = np.random.default_rng(2)
    regimes = np.concatenate([np.full(40, 0.01 if block % 2 == 0 else 0.05) for block in range(16)])        # calm and stormy blocks of 40 days
    returns = rng.normal(0.0, 1.0, len(regimes)) * regimes
    after_calm = har_annual_volatility(returns[:600], 7)       # block 15 (index 600) starts a stormy block: 560..599 is calm (block 14)
    after_storm = har_annual_volatility(returns[:560], 7)      # 520..559 is stormy (block 13)
    assert after_storm > after_calm
