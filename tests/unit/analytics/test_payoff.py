"""PnL at expiry of option structures: hand-computed numbers, exact extremes and breakevens, the chart."""

import pytest

from coding.core.analytics.charts.payoff import PayoffLeg, payoff_figure, pnl_at_expiry, summarize_payoff


def leg(action, option_type, strike, quantity=1.0):
    return PayoffLeg(action, option_type, float(strike), quantity)


IRON_CONDOR = [leg("buy", "put", 53000), leg("sell", "put", 55000), leg("sell", "call", 61000), leg("buy", "call", 63000)]


def test_iron_condor_extremes_and_breakevens():
    # credit 581: sell 55,000 put at 813, buy 53,000 put at 523, sell 61,000 call at 610, buy 63,000 call at 320 (580.9 in the stored run)
    summary = summarize_payoff(IRON_CONDOR, 581.0)
    assert summary.max_profit_usd == 581.0
    assert summary.max_loss_usd == pytest.approx(2000.0 - 581.0)
    assert summary.breakevens == pytest.approx((55000 - 581.0, 61000 + 581.0))


def test_pnl_at_a_few_prices_by_hand():
    assert pnl_at_expiry(IRON_CONDOR, 581.0, 58000) == 581.0                      # between the shorts: keep the credit
    assert pnl_at_expiry(IRON_CONDOR, 581.0, 54000) == pytest.approx(581.0 - 1000)   # short put 1,000 in the money
    assert pnl_at_expiry(IRON_CONDOR, 581.0, 50000) == pytest.approx(581.0 - 2000)   # both puts in the money: loss capped at the width
    assert pnl_at_expiry(IRON_CONDOR, 581.0, 70000) == pytest.approx(581.0 - 2000)


def test_a_long_call_has_unlimited_profit_a_loss_of_the_premium_and_one_breakeven():
    summary = summarize_payoff([leg("buy", "call", 60000)], -700.0)
    assert summary.max_profit_usd is None and summary.max_loss_usd == 700.0 and summary.breakevens == (60700.0,)


def test_a_short_strangle_has_unlimited_loss_and_two_breakevens():
    summary = summarize_payoff([leg("sell", "put", 55000), leg("sell", "call", 61000)], 600.0)
    assert summary.max_profit_usd == 600.0 and summary.max_loss_usd is None
    assert summary.breakevens == pytest.approx((54400.0, 61600.0))


def test_a_long_put_loses_the_premium_and_its_profit_is_bounded_by_the_strike():
    summary = summarize_payoff([leg("buy", "put", 60000)], -500.0)
    assert summary.max_loss_usd == 500.0 and summary.max_profit_usd == pytest.approx(60000 - 500.0) and summary.breakevens == (59500.0,)


def test_quantity_scales_the_payoff():
    assert summarize_payoff([leg("sell", "put", 55000, 2.0)], 1000.0).max_profit_usd == 1000.0
    assert pnl_at_expiry([leg("sell", "put", 55000, 2.0)], 1000.0, 54000) == pytest.approx(1000.0 - 2000.0)


def test_a_put_debit_spread_has_a_bounded_profit_and_loss_and_one_breakeven():
    summary = summarize_payoff([leg("buy", "put", 55000), leg("sell", "put", 53000)], -500.0)       # pays 500, width 2,000
    assert summary.max_loss_usd == 500.0 and summary.max_profit_usd == 1500.0 and summary.breakevens == (54500.0,)


def test_no_legs_is_an_error():
    with pytest.raises(ValueError):
        summarize_payoff([], 0.0)


def test_the_figure_has_the_curve_the_spot_and_the_breakevens():
    figure = payoff_figure(IRON_CONDOR, 581.0, 58000.0)
    curve = next(trace for trace in figure.data if trace.name == "PnL at expiry")
    assert min(curve.x) < 53000 and max(curve.x) > 63000 and 58000.0 in curve.x
    marks = [trace for trace in figure.data if trace.mode == "markers"]
    assert sorted(trace.x[0] for trace in marks) == pytest.approx([55000 - 581.0, 61000 + 581.0])
    assert figure.layout.xaxis.title.text == "BTC settlement price"
