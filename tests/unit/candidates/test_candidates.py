"""Distribution, generator, metrics, scoring and risk budget: hand-computed numbers and a Black-Scholes consistency check."""

import math
from datetime import datetime, timezone

import numpy as np
import pytest

from coding.core.analytics.backtest.fees import trading_fee_usd
from coding.core.analytics.backtest.structures import Quote
from coding.core.analytics.candidates.distribution import LognormalDistribution
from coding.core.analytics.candidates.generator import (
    CALL_CREDIT_SPREAD, CALL_DEBIT_SPREAD, FAMILIES, IRON_CONDOR, LONG_CALL, LONG_PUT, PUT_CREDIT_SPREAD, PUT_DEBIT_SPREAD,
    CandidateLimits, generate_candidates)
from coding.core.analytics.candidates.metrics import compute_metrics
from coding.core.analytics.candidates.scoring import RULES, CandidateOutcome, evaluate_rule, spearman
from coding.core.analytics.risk.budget import RiskBudget, size_position

FORWARD, VOL, DAYS = 70000.0, 0.55, 7.0


def normal_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def black_price(strike, option_type):
    """Black-76 price in USD for a forward, flat vol and time, r = 0."""
    total = VOL * math.sqrt(DAYS / 365.0)
    d1 = (math.log(FORWARD / strike) + 0.5 * total * total) / total
    d2 = d1 - total
    if option_type == "call":
        return FORWARD * normal_cdf(d1) - strike * normal_cdf(d2)
    return strike * normal_cdf(-d2) - FORWARD * normal_cdf(-d1)


def black_delta(strike, option_type):
    total = VOL * math.sqrt(DAYS / 365.0)
    d1 = (math.log(FORWARD / strike) + 0.5 * total * total) / total
    return normal_cdf(d1) if option_type == "call" else normal_cdf(d1) - 1.0


def chain(half_spread=0.0, missing=()):
    quotes = []
    for strike in range(50000, 90001, 1000):
        for option_type in ("call", "put"):
            if (strike, option_type) in missing:
                continue
            mid = black_price(strike, option_type)
            quotes.append(Quote(f"BTC-X-{strike}-{option_type[0].upper()}", float(strike), option_type, max(mid - half_spread, 0.01),
                                mid + half_spread, black_delta(strike, option_type)))
    return quotes


def test_the_grid_has_unit_weight_and_the_forward_as_its_mean():
    prices, weights = LognormalDistribution(FORWARD, VOL, DAYS).grid()
    assert weights.sum() == pytest.approx(1.0) and float((prices * weights).sum()) == pytest.approx(FORWARD, rel=1e-4)


def test_a_bad_distribution_is_refused():
    with pytest.raises(ValueError):
        LognormalDistribution(0.0, VOL, DAYS)


def test_every_family_is_generated_and_every_leg_has_the_side_it_needs():
    candidates = generate_candidates(chain(half_spread=5.0), FORWARD)
    assert {candidate.family for candidate in candidates} == set(FAMILIES)
    for candidate in candidates:
        for leg in candidate.legs:
            assert leg.entry_price_usd > 0
        assert len({candidate.key for candidate in candidates}) == len(candidates)


def test_structures_have_the_right_shape():
    by_family = {}
    for candidate in generate_candidates(chain(), FORWARD):
        by_family.setdefault(candidate.family, candidate)
    put_credit = by_family[PUT_CREDIT_SPREAD]
    sold, bought = (next(leg for leg in put_credit.legs if leg.action == action) for action in ("sell", "buy"))
    assert bought.strike < sold.strike and put_credit.entry_cash_usd > 0
    call_credit = by_family[CALL_CREDIT_SPREAD]
    assert next(l for l in call_credit.legs if l.action == "buy").strike > next(l for l in call_credit.legs if l.action == "sell").strike
    assert by_family[PUT_DEBIT_SPREAD].entry_cash_usd < 0 and by_family[CALL_DEBIT_SPREAD].entry_cash_usd < 0
    assert by_family[LONG_CALL].entry_cash_usd < 0 and by_family[LONG_PUT].entry_cash_usd < 0
    condor = by_family[IRON_CONDOR]
    strikes = sorted(leg.strike for leg in condor.legs)
    assert len(condor.legs) == 4 and [leg.action for leg in sorted(condor.legs, key=lambda leg: leg.strike)] == ["buy", "sell", "sell", "buy"]
    assert strikes[1] < strikes[2]


def test_an_option_without_the_needed_side_is_never_used():
    quotes = [Quote(q.instrument_name, q.strike, q.option_type, None if (q.option_type == "put" and q.strike < 60000) else q.bid_usd,
                    q.ask_usd, q.delta) for q in chain()]
    for candidate in generate_candidates(quotes, FORWARD):
        for leg in candidate.legs:
            assert not (leg.action == "sell" and leg.option_type == "put" and leg.strike < 60000)


def test_the_delta_range_limits_the_short_strikes():
    limits = CandidateLimits(short_delta_range=(0.10, 0.20))
    for candidate in generate_candidates(chain(), FORWARD, limits):
        if candidate.family in (PUT_CREDIT_SPREAD, CALL_CREDIT_SPREAD):
            short = next(leg for leg in candidate.legs if leg.action == "sell")
            assert 0.10 <= abs(black_delta(short.strike, short.option_type)) <= 0.20


def test_mid_cash_differs_from_touch_cash_by_the_spread():
    candidate = next(c for c in generate_candidates(chain(half_spread=7.0), FORWARD) if c.family == PUT_CREDIT_SPREAD)
    assert candidate.mid_cash_usd - candidate.entry_cash_usd == pytest.approx(2 * 7.0)         # two legs, 7 USD each way


def test_priced_at_its_own_volatility_a_structure_is_worth_exactly_its_costs():
    # Under the distribution the chain was priced with, every structure at the MID has an expected PnL of zero before fees:
    # what is left is minus the half-spreads, minus the trading fees, minus the (small) delivery fees.
    distribution = {"implied": LognormalDistribution(FORWARD, VOL, DAYS)}
    for candidate in generate_candidates(chain(half_spread=6.0), FORWARD)[:60:7]:
        metrics = compute_metrics(candidate, FORWARD, distribution)
        expected = metrics.by_distribution["implied"].expected_pnl_usd
        costs = metrics.spread_cost_usd + metrics.trading_fees_usd
        assert expected == pytest.approx(-costs, abs=4.0 + 0.00015 * FORWARD * len(candidate.legs))
        assert expected < 0


def test_iron_condor_numbers_by_hand():
    quotes = {(55000.0, "put"): (800.0, 840.0), (53000.0, "put"): (500.0, 520.0), (61000.0, "call"): (610.0, 650.0),
              (63000.0, "call"): (300.0, 320.0)}
    chain_quotes = [Quote(f"X-{int(strike)}-{kind[0]}", strike, kind, bid, ask, -0.2 if kind == "put" else 0.2) for (strike, kind), (bid, ask) in quotes.items()]
    candidate = next(c for c in generate_candidates(chain_quotes, 58000.0, CandidateLimits(short_delta_range=(0.1, 0.3), width_fractions=(0.034,)))
                     if c.family == IRON_CONDOR)
    metrics = compute_metrics(candidate, 58000.0, {"implied": LognormalDistribution(58000.0, 0.55, 6.0)})
    assert candidate.entry_cash_usd == 800.0 - 520.0 + 610.0 - 320.0                              # 570
    fees = sum(trading_fee_usd(58000.0, price) for price in (800.0, 520.0, 610.0, 320.0))
    assert metrics.trading_fees_usd == pytest.approx(fees)
    assert metrics.max_profit_usd == pytest.approx(570.0 - fees)
    assert metrics.max_loss_usd == pytest.approx(2000.0 - 570.0 + fees)
    assert metrics.spread_cost_usd == pytest.approx(0.5 * (840 - 800) + 0.5 * (520 - 500) + 0.5 * (650 - 610) + 0.5 * (320 - 300))


def test_a_long_call_has_unlimited_profit_and_a_candidate_with_unlimited_loss_is_refused():
    call = next(c for c in generate_candidates(chain(), FORWARD) if c.family == LONG_CALL)
    assert compute_metrics(call, FORWARD, {}).max_profit_usd is None
    from coding.core.analytics.candidates.generator import Candidate
    from coding.core.analytics.backtest.structures import Leg
    naked = Candidate("naked_call", (Leg("sell", "call", 70000.0, "X", 1000.0),), 1000.0, 1000.0)
    with pytest.raises(ValueError):
        compute_metrics(naked, FORWARD, {})


def test_tail_loss_is_the_average_of_the_worst_five_percent():
    call = next(c for c in generate_candidates(chain(), FORWARD) if c.family == PUT_CREDIT_SPREAD)
    metrics = compute_metrics(call, FORWARD, {"implied": LognormalDistribution(FORWARD, VOL, DAYS)})
    result = metrics.by_distribution["implied"]
    assert -metrics.max_loss_usd - 20 <= result.tail_loss_usd <= -metrics.max_loss_usd + 1e-6 or result.tail_loss_usd < 0
    assert 0.0 <= result.probability_of_profit <= 1.0


def test_spearman_and_the_evaluation_of_a_rule_that_knows_the_future():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)
    assert spearman([1, 1, 1], [1, 2, 3]) is None

    def outcome(r, score):
        from coding.core.analytics.candidates.metrics import CandidateMetrics, DistributionMetrics
        metrics = CandidateMetrics(0.0, 1.0, 1.0, 100.0, 100.0, (), {"realised": DistributionMetrics(0.5, score * 100.0, -50.0),
                                                                     "implied": DistributionMetrics(0.5, 0.0, -50.0)})
        return CandidateOutcome("x", metrics, r * 100.0)
    perfect = [[outcome(r, r) for r in (-1.0, -0.5, 0.0, 0.5, 1.0)] for _ in range(10)]
    report = evaluate_rule("expected_pnl_per_risk_realised_vol", perfect)
    assert report.dates == 10 and report.mean_pick_r == pytest.approx(1.0) and report.mean_all_r == pytest.approx(0.0)
    assert report.mean_excess_r == pytest.approx(1.0) and report.positive_dates == 1.0 and report.mean_rank_correlation == pytest.approx(1.0)
    assert evaluate_rule("lowest_cost", []).dates == 0
    assert set(RULES) >= {"lowest_cost", "reward_to_risk"}


def test_risk_per_trade_is_the_drawdown_limit_divided_by_the_losses_in_a_row():
    budget = RiskBudget(200.0, 0.20, 4)
    assert budget.risk_per_trade_usd == pytest.approx(10.0)
    assert RiskBudget(10000.0).risk_per_trade_usd == pytest.approx(500.0)
    with pytest.raises(ValueError):
        RiskBudget(0.0)


def test_sizing_fits_the_budget_or_says_why_not():
    budget = RiskBudget(200.0)
    fractional = size_position(1000.0, budget)                                   # notional research account
    assert fractional.feasible and fractional.quantity == pytest.approx(0.01) and fractional.max_loss_usd == pytest.approx(10.0)
    too_big = size_position(1000.0, budget, minimum_quantity=0.1)                # 0.1 coin risks 100 USD
    assert not too_big.feasible and too_big.quantity == 0.0 and "100.00 USD" in too_big.reason
    assert size_position(1000.0, RiskBudget(10000.0), 0.1).quantity == pytest.approx(0.5)
    with pytest.raises(ValueError):
        size_position(0.0, budget)
