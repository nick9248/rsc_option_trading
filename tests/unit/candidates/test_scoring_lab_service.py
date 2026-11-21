"""ScoringLabService with a fake repository: point-in-time volatility, skips, and the result tables."""

import math
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock

import numpy as np
import pytest

from coding.core.analytics.risk.budget import RiskBudget
from coding.service.candidates.scoring_lab_service import MINIMUM_QUANTITY, ScoringLabService
from tests.unit.backtest.test_backtest_service import ENTRY, NOW, make_repository, rows

BUDGET = RiskBudget(200.0)


def daily_closes(count=45, daily_move=0.01):
    """Closes alternating up and down by the same log move, one per day ending the day before the entry day."""
    closes, price = [], 70000.0
    for index in range(count):
        price *= math.exp(daily_move if index % 2 == 0 else -daily_move)
        closes.append({"date": datetime(2026, 6, 1) - timedelta(days=count - index), "close": price})
    return closes


def make_lab(closes=None, **repository_arguments):
    repository = make_repository(**repository_arguments)
    repository.get_ohlcv_by_date_range.return_value = daily_closes() if closes is None else closes
    return ScoringLabService(repository), repository


def run(lab):
    return lab.run("BTC", date(2026, 6, 1), date(2026, 6, 1), BUDGET, now_utc=NOW)


def test_realised_volatility_is_the_annualised_std_of_the_last_30_daily_log_returns():
    lab, _ = make_lab()
    returns = [0.01 if index % 2 == 0 else -0.01 for index in range(30)]
    assert lab.realised_volatility("BTC", date(2026, 6, 1)) == pytest.approx(np.std(returns, ddof=1) * math.sqrt(365.0), rel=0.05)


def test_realised_volatility_only_uses_candles_that_were_complete_at_the_entry_time():
    lab, repository = make_lab()
    lab.realised_volatility("BTC", date(2026, 6, 1))
    start, end = repository.get_ohlcv_by_date_range.call_args.args[1:3]
    assert end == datetime(2026, 5, 31, 9, 0) and start < end                       # the candle that finished at 08:00 UTC of 31 May is the newest one a 09:00 entry on 1 June may use


def test_a_gap_in_the_daily_candles_is_not_stretched_into_one_return():
    candles = daily_closes()
    gapped = [candle for index, candle in enumerate(candles) if index % 2 == 0]               # every second day missing: no consecutive pair
    lab, _ = make_lab(closes=gapped)
    assert lab.realised_volatility("BTC", date(2026, 6, 1)) is None


def test_too_few_daily_closes_give_no_volatility():
    lab, _ = make_lab(closes=daily_closes(10))
    assert lab.realised_volatility("BTC", date(2026, 6, 1)) is None
    assert run(lab).skipped == {"no volatility": 1}


def test_a_day_gives_candidates_rule_reports_and_family_statistics():
    lab, _ = make_lab()
    result = run(lab)
    assert result.dates == 1
    assert result.candidates > 100 and result.skipped == {}
    assert len(result.rule_reports) == 6 and all(report.dates == 1 for report in result.rule_reports if report.dates)
    assert {stat.family for stat in result.family_stats} >= {"put_credit_spread", "iron_condor", "long_call"}


def test_a_two_hundred_dollar_account_fits_nothing_at_the_minimum_order_size_and_the_needed_capital_is_reported():
    lab, _ = make_lab()
    result = run(lab)
    assert result.feasible_share == 0.0 and result.capital_needed_usd is not None and result.capital_needed_usd > 200.0
    rich = lab.run("BTC", date(2026, 6, 1), date(2026, 6, 1), RiskBudget(1_000_000.0), now_utc=NOW)
    assert rich.feasible_share == 1.0 and MINIMUM_QUANTITY["BTC"] == 0.1


def test_skips_of_the_entry_are_counted_by_reason():
    lab, _ = make_lab(settlement=None)
    assert run(lab).skipped == {"no settlement price": 1}
