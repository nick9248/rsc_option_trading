"""Strategy tab: cards of the stored live picks, size and chart, shortlist, warnings, escaping; and the tab in the page."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from coding.core.analytics.unified_dashboard.strategy_panel import MAX_SELECTED_TRADES, StoredPick, build_strategy_panel, select_trades
from coding.service.analysis.unified_dashboard_service import UnifiedDashboardService

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)


def candidate(identifier, family, legs, entry_cash, max_loss, probability=0.6, expected=25.0):
    return {"id": identifier, "family": family, "legs": [{"action": a, "option_type": t, "strike": k, "instrument": f"X-{k}", "entry_price_usd": p}
                                                       for a, t, k, p in legs],
            "entry_cash_usd": entry_cash, "max_loss_usd": max_loss, "distributions": {"realised": {"probability_of_profit": probability,
                                                                                                   "expected_pnl_usd": expected}}}


CONDOR = candidate("C1", "iron_condor", [("buy", "put", 53000, 523), ("sell", "put", 55000, 813), ("sell", "call", 61000, 610),
                                         ("buy", "call", 63000, 320)], 580.0, 1420.0)
SPREAD = candidate("C2", "put_credit_spread", [("sell", "put", 55000, 813), ("buy", "put", 53000, 523)], 290.0, 1710.0, 0.8, 40.0)
LONG_PUT = candidate("C3", "long_put", [("buy", "put", 57000, 1400)], -1400.0, 1400.0, 0.35, -90.0)


def answer(**overrides):
    return json.dumps({"choice": "C1", "ranking": ["C1", "C2", "C3"], "view": "range_bound", "summary": "Sell the rich wings <b>x</b>.",
                       "reasons": ["VRP +14 vol points"], "risks": ["gap below 53,000"], "invalidation": "below 55,000", **overrides})


def stored(expiration="27NOV26", status="ok", choice="C1", response=None, hours_old=1.0, quantity=0.5, baseline="C2", as_of=None):
    return StoredPick(expiration, as_of or NOW - timedelta(hours=hours_old), 58000.0, "claude-opus-5-5", status, [CONDOR, SPREAD, LONG_PUT], choice,
                      ["C1", "C2", "C3"], answer() if response is None else response, quantity, 710.0, 500.0, baseline, 0.045)


def test_a_pick_shows_the_choice_size_chart_reasons_and_the_shortlist_in_the_models_order():
    panel = build_strategy_panel([stored()], NOW, "BTC")
    for text in ("27NOV26", "Range-bound", "Chosen: C1", "iron condor", "53,000", "Size from the loss budget: 0.5000 coins", "credit 290 USD",
                 "maximum loss 710 USD", "VRP +14 vol points", "gap below 53,000", "below 55,000", "1.0 h old", "not scored", "(chosen)", "(baseline)",
                 "Shortlist in the model"):
        assert text in panel.html
    assert panel.html.index("C1 (chosen)") < panel.html.index("C2 (baseline)") < panel.html.index("C3")
    assert 'data-stale="strategy-27NOV26"' in panel.html and set(panel.figures) == {"strategy-27NOV26"}
    assert "maximum profit 290 USD, maximum loss 710 USD, breakeven 54,420, 61,580" in panel.html   # credit 580 x 0.5 coins; breakevens are the short strikes -/+ 580


def with_expected(pick, expected, expiration):
    """A copy of the pick whose chosen candidate (C1) has this realised expected PnL."""
    candidates = [dict(c, distributions={"realised": {"probability_of_profit": 0.6, "expected_pnl_usd": expected}}) if c["id"] == "C1" else c
                  for c in pick.candidates]
    return StoredPick(**{**pick.__dict__, "candidates": candidates, "expiration": expiration})


def test_at_most_two_picks_are_selected_best_expected_pnl_per_risk_first_and_the_rest_are_alternatives():
    picks = [with_expected(stored(), expected, expiration) for expected, expiration in ((100.0, "24NOV26"), (400.0, "27NOV26"), (250.0, "4DEC26"))]
    assert [pick.expiration for pick in select_trades(picks, NOW)] == ["27NOV26", "4DEC26"] and MAX_SELECTED_TRADES == 2
    html_text = build_strategy_panel(picks, NOW, "BTC").html
    assert html_text.count("SELECTED TRADE") == 2 and html_text.count("alternative, not selected") == 1
    assert "Selected trades" in html_text and "27NOV26 (max loss 710 USD), 4DEC26 (max loss 710 USD)" in html_text and "1,420 USD is lost" in html_text
    assert "one bet, not as diversification" in html_text                     # same structure on the same view


def test_a_pick_with_a_non_positive_expectation_or_no_choice_is_never_selected():
    losing = with_expected(stored(), -5.0, "27NOV26")
    no_choice = stored("4DEC26", status="no_trade", choice="no_trade", quantity=None, response=answer(choice="no_trade", view="no_trade"))
    assert select_trades([losing, no_choice], NOW) == []
    assert "No pick is selected as a trade" in build_strategy_panel([losing, no_choice], NOW, "BTC").html


def test_the_tab_says_which_expiries_are_asked_and_that_the_others_are_not():
    html_text = build_strategy_panel([stored()], NOW, "BTC").html
    assert "nearest 3, 7, 14 and 30 days only" in html_text and "not a no-trade answer" in html_text and "Asked in this view: 27NOV26." in html_text


def test_model_text_is_escaped():
    html_text = build_strategy_panel([stored()], NOW, "BTC").html
    assert "<b>x</b>" not in html_text and "&lt;b&gt;x&lt;/b&gt;" in html_text


def test_the_baseline_note_says_when_the_model_agreed_with_it():
    assert "chose the same candidate" in build_strategy_panel([stored(baseline="C1")], NOW, "BTC").html
    assert "preferred C2" in build_strategy_panel([stored(baseline="C2")], NOW, "BTC").html


def test_an_old_pick_is_marked():
    html_text = build_strategy_panel([stored(hours_old=7.0)], NOW, "BTC").html
    assert "7.0 h old" in html_text and "Ask again before relying on it" in html_text


def test_no_trade_has_no_chart_and_still_shows_the_shortlist():
    panel = build_strategy_panel([stored(status="no_trade", choice="no_trade", quantity=None, response=answer(choice="no_trade", view="no_trade"))],
                                 NOW, "BTC")
    assert "chose no trade" in panel.html and "Shortlist in the model" in panel.html and panel.figures == {}


@pytest.mark.parametrize("status, text", [("refused", "declined"), ("invalid_response", "not usable"), ("no_candidates", "no usable candidate")])
def test_a_refused_unusable_or_empty_pick_is_stated(status, text):
    panel = build_strategy_panel([stored(status=status, choice=None, quantity=None, response="raw words <i>here</i>")], NOW, "BTC")
    assert text in panel.html and "<i>here</i>" not in panel.html and panel.figures == {}


def test_a_pick_whose_chosen_candidate_is_missing_is_not_half_shown():
    panel = build_strategy_panel([stored(choice="C9")], NOW, "BTC")
    assert "could not be read" in panel.html and "<table" not in panel.html and panel.figures == {}


def test_expired_expiries_are_left_out_the_nearest_comes_first_and_the_newest_pick_of_an_expiry_wins():
    page = build_strategy_panel([stored("25DEC26"), stored("20NOV26"), stored("27NOV26")], NOW, "BTC").html
    assert "20NOV26" not in page and page.index("27NOV26") < page.index("25DEC26")
    older = stored(response=answer(summary="OLDER"), as_of=NOW - timedelta(hours=3))
    newer = stored(response=answer(summary="NEWER"), as_of=NOW - timedelta(hours=1))
    page = build_strategy_panel([older, newer], NOW, "BTC").html
    assert "NEWER" in page and "OLDER" not in page


def test_nothing_stored_a_read_error_and_a_naive_time_are_handled():
    assert "No stored pick" in build_strategy_panel([], NOW, "BTC").html
    assert "could not be read" in build_strategy_panel([], NOW, "BTC", read_error="connection refused").html
    with pytest.raises(ValueError):
        build_strategy_panel([], datetime(2026, 11, 21, 12, 0), "BTC")


def test_the_page_has_a_strategy_tab_only_when_a_panel_is_given_and_its_figures_reach_the_page_data(real_result):
    import re
    from tests.unit.onchain_dashboard.test_unified_dashboard import PLOTLY_STAND_IN, make_report_page, tab_labels
    from coding.core.analytics.onchain_dashboard import build_onchain_dashboard_page
    from coding.core.analytics.unified_dashboard import build_unified_dashboard_page

    onchain = build_onchain_dashboard_page(real_result, PLOTLY_STAND_IN)
    without = build_unified_dashboard_page(make_report_page(), onchain, "BTC")
    with_panel = build_unified_dashboard_page(make_report_page(), onchain, "BTC", strategy_panel=build_strategy_panel([stored()], NOW, "BTC"))
    assert "Strategy" not in tab_labels(without)
    labels = tab_labels(with_panel)
    assert labels[labels.index("Strategy") + 1] == "Expiries" and "Chosen: C1" in with_panel and "%%" not in with_panel
    data = json.loads(re.search(r'<script type="application/json" id="report-data">(.*?)</script>', with_panel, re.DOTALL).group(1))
    assert "strategy-27NOV26" in data["figures"]


def pick_row(**overrides):
    pick = stored(expiration="25DEC27")
    row = {name: getattr(pick, name) for name in ("expiration", "as_of", "spot", "model", "status", "candidates", "choice", "ranking", "response",
                                                  "quantity", "max_loss_usd", "risk_per_trade_usd", "baseline_id", "cost_usd")}
    return {**row, "as_of": NOW, "mode": "live", **overrides}


def test_the_service_reads_the_live_picks_of_the_current_method_and_survives_a_database_error():
    repository = MagicMock()
    repository.get_strategy_picks.return_value = [pick_row()]
    panel = UnifiedDashboardService(lambda: repository)._strategy_panel("BTC")
    assert "Chosen: C1" in panel.html
    kwargs = repository.get_strategy_picks.call_args.kwargs
    assert kwargs["mode"] == "live" and kwargs["method_version"] == "pipeline-2"
    repository.get_strategy_picks.side_effect = RuntimeError("down")
    assert "could not be read: RuntimeError: down" in UnifiedDashboardService(lambda: repository)._strategy_panel("BTC").html
    assert UnifiedDashboardService()._strategy_panel("BTC") is None

    def cannot_connect():
        raise ConnectionError("database down")
    assert "ConnectionError: database down" in UnifiedDashboardService(cannot_connect)._strategy_panel("BTC").html


def test_a_stale_pick_is_never_selected_and_does_not_push_out_a_fresh_one():
    stale = with_expected(stored(hours_old=10.0), 900.0, "24NOV26")
    fresh = with_expected(stored(), 100.0, "27NOV26")
    assert select_trades([stale, fresh], NOW) == [fresh]
    assert build_strategy_panel([stale], NOW, "BTC").html.count("SELECTED TRADE") == 0


def test_a_missing_maximum_loss_makes_the_total_not_computed_instead_of_smaller():
    unknown = StoredPick(**{**with_expected(stored(), 100.0, "27NOV26").__dict__, "max_loss_usd": None})
    html_text = build_strategy_panel([unknown, with_expected(stored(), 90.0, "4DEC26")], NOW, "BTC").html
    assert "not computed is lost" in html_text and "710 USD is lost" not in html_text


def test_unknown_structures_are_not_called_one_bet_and_no_trade_picks_are_labelled_so():
    no_family = [StoredPick(**{**with_expected(stored(), 100.0, e).__dict__, "choice": "C1"}) for e in ("27NOV26", "4DEC26")]
    for pick in no_family:                                                  # remove the family of the chosen candidate
        pick.candidates[0].pop("family")
    assert "one bet" not in build_strategy_panel(no_family, NOW, "BTC").html
    html_text = build_strategy_panel([stored(status="no_trade", choice="no_trade", quantity=None, response=answer(choice="no_trade", view="no_trade"))], NOW, "BTC").html
    assert "27NOV26 - no trade" in html_text and "alternative" not in html_text.split("Asked in this view")[1]


def test_the_asked_note_names_the_same_targets_as_the_runner():
    from coding.service.strategy_pipeline.live_pick_runner import LIVE_TARGET_DAYS
    numbers = [f"{day:g}" for day in LIVE_TARGET_DAYS]
    assert f"nearest {', '.join(numbers[:-1])} and {numbers[-1]} days" in build_strategy_panel([stored()], NOW, "BTC").html
