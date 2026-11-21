"""Shortlist (filters, one per family limit, no ranking leak), parsing of the answer, and the report of the picks."""

import json
from datetime import datetime, timezone

import pytest

from coding.core.analytics.candidates.distribution import LognormalDistribution
from coding.core.analytics.candidates.generator import FAMILIES, generate_candidates
from coding.core.analytics.candidates.metrics import compute_metrics
from coding.core.analytics.strategy_pipeline.pick_report import summarize_picks
from coding.core.analytics.strategy_pipeline.shortlist import (
    BASELINE_RULE, NO_TRADE, PER_FAMILY, baseline_id, build_shortlist, candidate_record, parse_pick, render_shortlist)
from tests.unit.candidates.test_candidates import DAYS, FORWARD, VOL, chain


def scored(half_spread=2.0, realised_vol=0.40):
    distributions = {"realised": LognormalDistribution(FORWARD, realised_vol, DAYS), "implied": LognormalDistribution(FORWARD, VOL, DAYS)}
    return [(candidate, compute_metrics(candidate, FORWARD, distributions)) for candidate in generate_candidates(chain(half_spread=half_spread), FORWARD)]


def test_the_shortlist_has_at_most_two_per_family_and_every_family():
    shortlist = build_shortlist(scored())
    assert {item.candidate.family for item in shortlist} == set(FAMILIES)
    for family in FAMILIES:
        assert sum(1 for item in shortlist if item.candidate.family == family) <= PER_FAMILY
    assert [item.candidate_id for item in shortlist] == [f"C{number}" for number in range(1, len(shortlist) + 1)]


def test_the_order_and_ids_do_not_reveal_the_baseline_ranking():
    shortlist = build_shortlist(scored())
    order = [(item.candidate.family, sorted(leg.strike for leg in item.candidate.legs)) for item in shortlist]
    assert order == sorted(order)                                   # by family and strikes, whatever the scores


def test_the_baseline_is_the_best_scoring_shortlisted_candidate():
    shortlist = build_shortlist(scored())
    best = max(shortlist, key=lambda item: item.score)
    assert baseline_id(shortlist) == best.candidate_id and baseline_id([]) is None


def test_candidates_whose_costs_eat_the_risk_are_dropped():
    wide = build_shortlist(scored(half_spread=400.0))                 # absurd spreads: almost nothing passes
    tight = build_shortlist(scored(half_spread=1.0))
    assert len(wide) < len(tight)
    for item in wide:
        assert item.metrics.spread_cost_usd + item.metrics.trading_fees_usd <= 0.15 * item.metrics.max_loss_usd


def test_the_text_shows_legs_costs_and_both_views_and_stored_records_are_plain_data():
    shortlist = build_shortlist(scored())
    text = render_shortlist(shortlist)
    assert text.startswith("## Shortlist") and "C1 " in text and "realised-vol view: P(profit)" in text
    assert "market view: expected PnL" in text and "implied-vol view" not in text            # exact, no flat-volatility artefact
    assert "max profit unlimited" in text
    json.dumps([candidate_record(item) for item in shortlist], allow_nan=False)
    for item in shortlist:
        record = candidate_record(item)
        assert record["market_expected_pnl_usd"] == pytest.approx(-(item.metrics.spread_cost_usd + item.metrics.trading_fees_usd))
    assert BASELINE_RULE == "expected_pnl_per_risk_realised_vol"


IDS = ["C1", "C2", "C3"]


def answer(**overrides):
    return json.dumps({"choice": "C2", "ranking": ["C2", "C1", "C3"], "view": "range_bound", "summary": "s", "reasons": ["r"], "risks": ["k"],
                       "invalidation": "i", **overrides})


def test_a_valid_answer_is_parsed():
    pick = parse_pick(answer(), IDS)
    assert pick.choice == "C2" and pick.ranking == ("C2", "C1", "C3")
    assert parse_pick(answer(choice=NO_TRADE, view="no_trade"), IDS).choice == NO_TRADE


@pytest.mark.parametrize("text", ["not json", "{}", answer(choice="C9", ranking=["C9", "C1", "C3"]), answer(ranking=["C2", "C1"]),
                                  answer(ranking=["C2", "C2", "C1"]), answer(choice="C1"), answer(view="moon"), answer(summary=5),
                                  json.dumps([1])])
def test_a_bad_answer_is_refused(text):
    with pytest.raises(ValueError):
        parse_pick(text, IDS)


def evaluated(choice, model_r, baseline_r, shortlist_r=0.0, all_r=0.0, baseline="C1"):
    outcomes = {"C1": {"pnl_usd": 0.0, "r": baseline_r}, "C2": {"pnl_usd": 0.0, "r": model_r}, "_shortlist_mean_r": shortlist_r, "_all_mean_r": all_r}
    return {"choice": choice, "baseline_id": baseline, "outcomes": outcomes, "status": "no_trade" if choice == NO_TRADE else "ok"}


def test_the_report_compares_the_model_with_every_baseline_pair_by_pair_and_counts_no_trade_as_zero():
    rows = [evaluated("C2", 0.5, 0.1), evaluated("C2", -1.0, -0.2), evaluated(NO_TRADE, 9.9, -0.5), evaluated("C2", 0.3, 0.3)]
    report = summarize_picks(rows)
    assert report.picks == 4 and report.no_trade_share == 0.25
    assert report.mean_model_r == pytest.approx((0.5 - 1.0 + 0.0 + 0.3) / 4)                    # no trade risked nothing: 0, not the 9.9 of C2
    assert report.mean_baseline_r == pytest.approx((0.1 - 0.2 - 0.5 + 0.3) / 4)
    assert report.model_minus_baseline == pytest.approx(report.mean_model_r - report.mean_baseline_r)
    assert report.model_beat_baseline_share == pytest.approx(0.5)                               # beat on rows 1 and 3, tied on 4 (not a beat)


def test_picks_without_an_evaluation_or_a_usable_choice_are_left_out_and_an_empty_report_has_none():
    report = summarize_picks([{"choice": "C2", "baseline_id": "C1", "outcomes": None}, {"choice": None, "baseline_id": "C1", "outcomes": {}},
                              evaluated("C7", 1.0, 1.0)])
    assert report.picks == 0 and report.mean_model_r is None and report.model_minus_baseline_t is None


def test_refused_unusable_and_waiting_picks_are_counted_apart_not_dropped():
    rows = [evaluated("C2", 0.5, 0.1), {"status": "invalid_response", "choice": None, "baseline_id": "C1", "outcomes": None},
            {"status": "refused", "choice": None, "baseline_id": "C1", "outcomes": None},
            {"status": "ok", "choice": "C2", "baseline_id": "C1", "outcomes": None}]
    report = summarize_picks(rows)
    assert report.picks == 1 and report.unusable_answers == 2 and report.not_yet_evaluated == 1


def test_a_stored_record_never_holds_a_non_finite_number():
    from dataclasses import replace
    item = build_shortlist(scored())[0]
    broken = replace(item, metrics=replace(item.metrics, spread_cost_usd=float("nan"), breakevens=(float("inf"),)))
    record = candidate_record(broken)
    assert record["spread_cost_usd"] is None and record["breakevens"] == [None]
    json.dumps(record, allow_nan=False)


def test_the_market_view_is_minus_the_costs_for_every_candidate_whatever_the_skew():
    # a chain whose puts are richer than a flat volatility would price them (skew): the flat lognormal "implied view" would show a positive
    # expected PnL for selling them; the market view must stay exactly minus the costs
    for item in build_shortlist(scored()):
        line = next(part for part in render_shortlist([item]).split(" | ") if part.startswith("market view"))
        expected = -(item.metrics.spread_cost_usd + item.metrics.trading_fees_usd)
        assert f"{expected:+,.0f}" in line


def test_the_market_view_agrees_with_the_numerical_expectation_on_a_chain_priced_at_one_volatility():
    # independent check of the identity: on a chain priced at ONE volatility, the grid expectation of the PnL under that volatility equals
    # minus the costs, up to the small delivery fees (0.015% of the settlement price per leg in the money)
    distributions = {"implied": LognormalDistribution(FORWARD, VOL, DAYS)}
    for candidate in generate_candidates(chain(half_spread=3.0), FORWARD)[:80:9]:
        metrics = compute_metrics(candidate, FORWARD, distributions)
        numerical = metrics.by_distribution["implied"].expected_pnl_usd
        assert numerical == pytest.approx(metrics.market_expected_pnl_usd, abs=0.00015 * FORWARD * len(candidate.legs) + 2.0)
