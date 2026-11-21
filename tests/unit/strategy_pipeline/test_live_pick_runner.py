"""The live pick runner: one pick per distinct expiry, free repeats, failures propagate."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from coding.service.strategy.strategy_suggestion_service import BudgetExceeded
from coding.service.strategy_pipeline.live_pick_runner import LIVE_TARGET_DAYS, LivePickRunner
from coding.service.strategy_pipeline.strategy_pick_service import PickResult

NOW = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)


def result(expiration, status="ok", choice="C3", cost=0.045, stored_before=False):
    return PickResult(1, status, choice, None, [], 0.1, cost, stored_before, expiration)


def test_every_target_is_asked_and_an_expiry_met_twice_is_counted_once():
    service = MagicMock()
    service.pick.side_effect = [result("22NOV26"), result("27NOV26"), result("27NOV26", stored_before=True, cost=0.0), result("25DEC26")]
    lines = []

    summary = LivePickRunner(service).run("BTC", lines.append, now_utc=NOW)

    assert service.pick.call_count == len(LIVE_TARGET_DAYS)
    assert summary == "3 expiries, 3 new picks, 0.135 USD"                    # the repeated expiry is free and counted once
    assert [call.kwargs["target_days_to_expiry"] for call in service.pick.call_args_list] == list(LIVE_TARGET_DAYS)
    assert len(lines) == 3 and "27NOV26: ok, C3" in lines[1]


def test_a_target_without_a_usable_chain_is_said_not_hidden():
    service = MagicMock()
    service.pick.return_value = None
    lines = []
    summary = LivePickRunner(service).run("BTC", lines.append, now_utc=NOW)
    assert summary.startswith("0 expiries") and len(lines) == len(LIVE_TARGET_DAYS) and "no pick" in lines[0]


def test_the_budget_error_reaches_the_caller_and_the_money_already_spent_is_reported():
    service = MagicMock()
    service.pick.side_effect = [result("22NOV26"), result("27NOV26"), BudgetExceeded("used")]
    lines = []
    with pytest.raises(BudgetExceeded):
        LivePickRunner(service).run("BTC", lines.append, now_utc=NOW)
    assert "2 picks stored and paid (0.090 USD)" in lines[-1]


def test_a_stop_request_ends_the_run_before_the_next_paid_call_and_keeps_the_picks():
    service = MagicMock()
    service.pick.side_effect = [result("22NOV26"), result("27NOV26")]
    asked = iter([False, True])
    summary = LivePickRunner(service).run("BTC", lambda line: None, should_stop=lambda: next(asked), now_utc=NOW)
    assert service.pick.call_count == 1 and summary == "1 expiries, 1 new picks, 0.045 USD (stopped by the user)"
