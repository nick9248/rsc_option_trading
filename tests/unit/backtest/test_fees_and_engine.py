"""Fees and the trade engine: hand-computed numbers."""

from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

from coding.core.analytics.backtest.engine import intrinsic_value_usd, simulate_trade, summarize
from coding.core.analytics.backtest.fees import delivery_fee_usd, trading_fee_usd
from coding.core.analytics.backtest.structures import Leg

T0 = datetime(2026, 6, 1, 9, tzinfo=timezone.utc)


def leg(action, option_type, strike, price):
    return Leg(action, option_type, strike, f"BTC-5JUN26-{strike}-{option_type[0].upper()}", price)


def test_trading_fee_is_the_rate_times_the_index_capped_at_an_eighth_of_the_option_price():
    assert trading_fee_usd(70000.0, 500.0) == pytest.approx(21.0)          # 0.03% of 70,000
    assert trading_fee_usd(70000.0, 100.0) == pytest.approx(12.5)          # capped: 12.5% of 100


def test_delivery_fee_only_for_an_option_in_the_money():
    assert delivery_fee_usd(70000.0, 0.0) == 0.0
    assert delivery_fee_usd(70000.0, 4000.0) == pytest.approx(10.5)        # 0.015% of 70,000
    assert delivery_fee_usd(70000.0, 20.0) == pytest.approx(2.5)           # capped: 12.5% of 20


def test_intrinsic_value():
    assert intrinsic_value_usd("call", 60000, 65000) == 5000 and intrinsic_value_usd("call", 60000, 55000) == 0
    assert intrinsic_value_usd("put", 60000, 55000) == 5000 and intrinsic_value_usd("put", 60000, 65000) == 0


def test_a_put_credit_spread_that_expires_worthless_keeps_the_credit_less_fees():
    legs = [leg("sell", "put", 70000, 360.0), leg("buy", "put", 66000, 120.0)]
    trade = simulate_trade("put_credit_spread", "5JUN26", T0, 75000.0, legs, 76000.0)
    assert trade.premium_usd == 240.0 and trade.payoff_usd == 0.0 and trade.delivery_fees_usd == 0.0
    # fees: min(22.5, 45) for the sold put, min(22.5, 15) for the bought one
    assert trade.trading_fees_usd == pytest.approx(22.5 + 15.0)
    assert trade.pnl_usd == pytest.approx(240.0 - 37.5)


def test_a_put_credit_spread_through_both_strikes_loses_the_width_less_the_credit_and_fees():
    legs = [leg("sell", "put", 70000, 360.0), leg("buy", "put", 66000, 120.0)]
    trade = simulate_trade("put_credit_spread", "5JUN26", T0, 75000.0, legs, 62000.0)
    assert trade.payoff_usd == -4000.0                                    # -(8000) + 4000
    # delivery fees: sold put intrinsic 8000 -> min(9.3, 1000) = 9.3; bought put intrinsic 4000 -> 9.3
    assert trade.delivery_fees_usd == pytest.approx(2 * 0.00015 * 62000)
    assert trade.pnl_usd == pytest.approx(240.0 - 4000.0 - 37.5 - 18.6)


def test_a_bought_call_pays_its_ask_and_receives_the_intrinsic_value():
    trade = simulate_trade("long_call", "5JUN26", T0, 75000.0, [leg("buy", "call", 80000, 700.0)], 90000.0)
    assert trade.premium_usd == -700.0 and trade.payoff_usd == 10000.0
    assert trade.pnl_usd == pytest.approx(-700.0 + 10000.0 - 22.5 - 0.00015 * 90000)


def test_summary_statistics_drawdown_and_months():
    def trade(day, pnl):
        base = simulate_trade("x", "5JUN26", datetime(2026, 6, day, 9, tzinfo=timezone.utc), 1.0, [], 1.0)
        return base.__class__(**{**base.__dict__, "pnl_usd": pnl})
    trades = [trade(1, 100.0), trade(2, 100.0), trade(3, -300.0), trade(4, 50.0)]
    summary = summarize(trades[::-1], Counter({"no snapshot": 2}))          # order of the input does not matter
    assert (summary.trades, summary.wins, summary.win_rate) == (4, 3, 0.75)
    assert summary.total_pnl_usd == -50.0 and summary.worst_pnl_usd == -300.0 and summary.best_pnl_usd == 100.0
    assert summary.max_drawdown_usd == 300.0                                # 200 -> -100
    assert summary.pnl_by_month == {"2026-06": -50.0} and summary.skipped == {"no snapshot": 2}


def test_no_trades_gives_none_not_zero_statistics():
    summary = summarize([])
    assert summary.trades == 0 and summary.win_rate is None and summary.mean_pnl_usd is None and summary.median_pnl_usd is None
