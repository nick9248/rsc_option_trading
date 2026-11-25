"""StrategyPickService with a fake client and repository: no network, no paid call."""

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from coding.core.analytics.risk.budget import RiskBudget
from coding.service.strategy.strategy_suggestion_service import BudgetExceeded
from coding.service.strategy_pipeline.strategy_pick_service import METHOD_VERSION, MODE_LIVE, MODE_REPLAY, StrategyPickService
from tests.unit.backtest.test_backtest_service import ENTRY, NOW, make_repository
from tests.unit.strategy_pipeline.test_facts import analysis_row, daily_closes, volatility_row

AS_OF = ENTRY


def make_service(reply_choice="C1", spent=0.0, stored=None, mode_reply=None):
    repository = make_repository()
    repository.get_ohlcv_by_date_range.return_value = daily_closes(300)
    repository.get_onchain_analysis_snapshot_at_or_before.return_value = analysis_row(hour=AS_OF - timedelta(hours=1))
    repository.get_onchain_volatility_snapshot_at_or_before.return_value = volatility_row(hour=AS_OF - timedelta(hours=1))
    repository.get_flow_delta_window.return_value = {"hours_present": 24, "newest_hour": AS_OF - timedelta(hours=1), "hiro_usd": 1.0,
                                                     "premium_usd": 1.0, "gross_delta_usd": 1.0, "trade_count": 3}
    repository.get_dvol_history_before.return_value = [40.0, 45.0, 50.0]
    repository.get_latest_dvol_candle_start_before.return_value = AS_OF - timedelta(hours=1)
    repository.get_total_model_spend.return_value = spent
    repository.get_strategy_picks.return_value = stored or []
    repository.save_strategy_pick.return_value = 11
    repository.save_strategy_pick_evaluation.return_value = True
    client = MagicMock()
    client.messages.create.return_value = mode_reply
    service = StrategyPickService(repository, client, RiskBudget(10_000.0))
    return service, client, repository


def reply_for(service, choice_index=0, **overrides):
    """A model reply choosing the n-th shortlisted candidate (the shortlist is built from the same fake data)."""
    entry, _ = service._entry_for("BTC", AS_OF, MODE_REPLAY, 7.0, 2.0, None, NOW)
    _, shortlist = service._shortlist_for("BTC", AS_OF, entry, NOW)
    ids = [item.candidate_id for item in shortlist]
    ranking = [ids[choice_index]] + [value for value in ids if value != ids[choice_index]]
    body = {"choice": ids[choice_index], "ranking": ranking, "view": "range_bound", "summary": "s", "reasons": ["r"], "risks": ["k"],
            "invalidation": "i", **overrides}
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(body))], stop_reason="end_turn", stop_details=None,
                           usage=SimpleNamespace(input_tokens=3500, output_tokens=3000, cache_read_input_tokens=0, cache_creation_input_tokens=0),
                           _request_id="req_9"), shortlist


def run(service, **kwargs):
    return service.pick("BTC", AS_OF, kwargs.pop("mode", MODE_REPLAY), now_utc=NOW, **kwargs)


def test_a_replay_pick_is_stored_with_the_facts_the_shortlist_the_choice_and_a_size():
    service, client, repository = make_service()
    reply, shortlist = reply_for(service)
    client.messages.create.return_value = reply

    result = run(service)

    assert result.status == "ok" and result.choice == shortlist[0].candidate_id and result.row_id == 11 and result.cost_usd > 0
    row = repository.save_strategy_pick.call_args.args[0]
    assert row["mode"] == "replay" and row["as_of"] == AS_OF and row["status"] == "ok" and row["request_id"] == "req_9"
    assert row["method_version"] == METHOD_VERSION
    assert "## Market" in row["facts"] and len(row["candidates"]) == len(shortlist) and row["baseline_id"] is not None
    chosen = shortlist[0]
    assert row["max_loss_usd"] == pytest.approx(500.0) and row["quantity"] == pytest.approx(500.0 / chosen.metrics.max_loss_usd)   # 10,000 x 20% / 4
    call = client.messages.create.call_args.kwargs
    assert "Shortlist" in call["messages"][0]["content"] and call["output_config"]["format"]["type"] == "json_schema"


def test_no_trade_is_stored_without_a_size():
    service, client, repository = make_service()
    reply, shortlist = reply_for(service, choice=None)
    body = json.loads(reply.content[0].text)
    body.update(choice="no_trade", view="no_trade")
    reply.content[0].text = json.dumps(body)
    client.messages.create.return_value = reply
    result = run(service)
    assert result.status == "no_trade" and result.quantity is None


def test_an_answer_naming_an_id_not_on_the_shortlist_is_stored_as_invalid_with_its_cost():
    service, client, repository = make_service()
    reply, shortlist = reply_for(service)
    body = json.loads(reply.content[0].text)
    body.update(choice="C99")
    reply.content[0].text = json.dumps(body)
    client.messages.create.return_value = reply
    result = run(service)
    assert result.status == "invalid_response" and result.cost_usd > 0 and result.quantity is None


def test_a_refusal_and_a_cut_answer_are_stored_with_their_cost():
    for stop_reason, status in (("refusal", "refused"), ("max_tokens", "invalid_response")):
        service, client, repository = make_service()
        reply, _ = reply_for(service)
        reply.stop_reason = stop_reason
        client.messages.create.return_value = reply
        assert run(service).status == status
        repository.save_strategy_pick.assert_called_once()


def test_an_instant_already_stored_is_returned_without_a_call():
    service, client, repository = make_service(stored=[{"id": 4, "status": "ok", "choice": "C3", "quantity": 0.1}])
    result = run(service)
    assert result.stored_before and result.row_id == 4 and result.cost_usd == 0.0
    client.messages.create.assert_not_called()
    repository.save_strategy_pick.assert_not_called()
    assert repository.get_strategy_picks.call_args.kwargs["as_of"] == AS_OF                  # a replay instant is looked up exactly
    assert repository.get_strategy_picks.call_args.kwargs["method_version"] == METHOD_VERSION   # an older method version does not block a new answer


def test_a_live_pick_is_not_repeated_within_an_hour_for_the_same_expiry():
    service, client, repository = make_service(stored=[{"id": 4, "status": "ok", "choice": "C3", "quantity": 0.1}])
    result = service.pick("BTC", AS_OF, MODE_LIVE, now_utc=NOW)
    assert result.stored_before and repository.get_strategy_picks.call_args.kwargs["as_of_since"] == AS_OF - timedelta(hours=1)
    client.messages.create.assert_not_called()


def test_a_paid_answer_that_cannot_be_stored_is_logged_with_its_cost_and_the_error_is_raised(caplog):
    service, client, repository = make_service()
    reply, _ = reply_for(service)
    client.messages.create.return_value = reply
    repository.save_strategy_pick.side_effect = RuntimeError("database down")
    with pytest.raises(RuntimeError):
        run(service)
    assert "could not be stored" in caplog.text and "req_9" in caplog.text


def test_the_budget_stops_the_call_before_it_is_made():
    service, client, repository = make_service(spent=14.9)
    with pytest.raises(BudgetExceeded):
        run(service)
    client.messages.create.assert_not_called()


def test_a_live_pick_does_not_look_at_the_settlement():
    service, client, repository = make_service()
    reply, _ = reply_for(service)
    client.messages.create.return_value = reply
    repository.get_settlement_index_price.reset_mock()
    result = service.pick("BTC", AS_OF, MODE_LIVE, now_utc=NOW)
    assert result.status == "ok"
    repository.get_settlement_index_price.assert_not_called()
    assert repository.save_strategy_pick.call_args.args[0]["mode"] == "live"


def test_an_unusable_instant_gives_none_and_no_call():
    service, client, repository = make_service()
    repository.get_option_chain_snapshot_at_or_before.return_value = None
    assert run(service) is None
    client.messages.create.assert_not_called()


def test_a_naive_instant_and_an_unknown_mode_are_refused():
    service, _, _ = make_service()
    with pytest.raises(ValueError):
        service.pick("BTC", datetime(2026, 6, 1, 9), MODE_REPLAY)
    with pytest.raises(ValueError):
        service.pick("BTC", AS_OF, "dream")
    with pytest.raises(ValueError):
        StrategyPickService(MagicMock(), MagicMock(), model="claude-unknown")


def stored_pick(service, repository):
    """A pick row as the repository returns it, built from a real shortlist of the fake chain."""
    entry, _ = service._entry_for("BTC", AS_OF, MODE_REPLAY, 7.0, 2.0, None, NOW)
    _, shortlist = service._shortlist_for("BTC", AS_OF, entry, NOW)
    from coding.core.analytics.strategy_pipeline.shortlist import baseline_id, candidate_record
    return {"id": 5, "currency": "BTC", "expiration": entry.expiration, "mode": "replay", "as_of": AS_OF, "spot": entry.spot, "status": "ok",
            "choice": shortlist[0].candidate_id, "evaluated_at": None, "baseline_id": baseline_id(shortlist),
            "candidates": [candidate_record(item) for item in shortlist]}, shortlist


def test_evaluate_settles_every_shortlisted_candidate_and_the_whole_chain_and_stores_it_once():
    service, _, repository = make_service()
    row, shortlist = stored_pick(service, repository)
    repository.get_settlement_index_price.return_value = (70500.0, 300)

    assert service.evaluate(row, now_utc=NOW) is True

    pick_id, evaluated_at, settlement_price, baseline, outcomes = repository.save_strategy_pick_evaluation.call_args.args
    assert pick_id == 5 and settlement_price == 70500.0 and baseline == row["baseline_id"]
    assert set(outcomes) == {item.candidate_id for item in shortlist} | {"_shortlist_mean_r", "_all_mean_r"}
    for item in shortlist:
        assert outcomes[item.candidate_id]["r"] == pytest.approx(outcomes[item.candidate_id]["pnl_usd"] / item.metrics.max_loss_usd)
    assert -1.5 < outcomes["_all_mean_r"] < 1.5


def test_evaluate_reports_false_when_the_row_was_already_evaluated_elsewhere():
    service, _, repository = make_service()
    row, _ = stored_pick(service, repository)
    repository.get_settlement_index_price.return_value = (70500.0, 300)
    repository.save_strategy_pick_evaluation.return_value = False
    assert service.evaluate(row, now_utc=NOW) is False


def test_evaluate_waits_for_the_settlement_and_skips_evaluated_and_unusable_picks():
    service, _, repository = make_service()
    row, _ = stored_pick(service, repository)
    repository.get_settlement_index_price.return_value = (70500.0, 300)
    assert service.evaluate(row, now_utc=AS_OF) is False                                   # the expiry has not passed
    repository.get_settlement_index_price.return_value = (70500.0, 2)
    assert service.evaluate(row, now_utc=NOW) is False                                     # too few trades for a delivery price
    repository.get_settlement_index_price.return_value = (70500.0, 300)
    assert service.evaluate({**row, "evaluated_at": NOW}, now_utc=NOW) is False
    assert service.evaluate({**row, "status": "invalid_response"}, now_utc=NOW) is False
    repository.save_strategy_pick_evaluation.assert_not_called()


def test_a_venue_sized_pick_is_evaluated_with_the_venue_fees_at_its_stored_size_and_a_research_pick_with_deribit_fees():
    from coding.core.analytics.risk.venue import DERIVE_TAKER_FEES
    item = {"quantity": 0.02}
    fee = StrategyPickService._leg_fee(item, 60_000.0)
    assert fee(1_000.0) == pytest.approx(DERIVE_TAKER_FEES.fee_per_coin_usd(60_000.0, 1_000.0, 0.02))
    assert fee(1_000.0) == pytest.approx(0.5 / 0.02 + 18.0)                      # base fee per coin at this size + 0.03% of 60,000
    assert StrategyPickService._leg_fee({"quantity": None}, 60_000.0) is None and StrategyPickService._leg_fee({}, 60_000.0) is None


def test_a_unit_quantity_live_pick_is_stored_per_coin_with_the_metrics_maximum_loss_and_no_venue_warning(caplog):
    service, client, repository = make_service()
    service.unit_quantity = 1.0
    reply, shortlist = reply_for(service)
    client.messages.create.return_value = reply
    with caplog.at_level("WARNING"):
        result = run(service, mode=MODE_LIVE)
    row = repository.save_strategy_pick.call_args.args[0]
    chosen = next(item for item in shortlist if item.candidate_id == result.choice)
    assert row["quantity"] == 1.0 and row["max_loss_usd"] == pytest.approx(chosen.metrics.max_loss_usd)
    assert "No venue is configured" not in caplog.text


def test_a_stored_no_candidates_live_row_does_not_block_a_new_pick_within_the_hour_but_a_paid_one_does():
    free = {"id": 5, "status": "no_candidates", "choice": None, "quantity": None}
    paid = {"id": 6, "status": "ok", "choice": "C1", "quantity": 1.0}
    service, client, repository = make_service(stored=[free])
    reply, _ = reply_for(service)
    client.messages.create.return_value = reply
    assert run(service, mode=MODE_LIVE).stored_before is False and client.messages.create.call_count == 1
    service, client, repository = make_service(stored=[free, paid])
    assert run(service, mode=MODE_LIVE).stored_before is True and client.messages.create.call_count == 0
