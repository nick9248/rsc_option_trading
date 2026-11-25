"""Strategy tab: cards of the stored live picks, size and chart, shortlist, warnings, escaping; and the tab in the page."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from coding.core.analytics.unified_dashboard.strategy_panel import MAX_SELECTED_TRADES, StoredPick, build_strategy_panel, select_trades
from coding.service.analysis.unified_dashboard_service import UnifiedDashboardService

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)


def candidate(identifier, family, legs, entry_cash, max_loss, probability=0.6, expected=25.0, score=0.01):
    return {"id": identifier, "family": family, "legs": [{"action": a, "option_type": t, "strike": k, "instrument": f"X-{k}", "entry_price_usd": p}
                                                       for a, t, k, p in legs],
            "entry_cash_usd": entry_cash, "max_loss_usd": max_loss, "baseline_score": score,
            "distributions": {"realised": {"probability_of_profit": probability, "expected_pnl_usd": expected},
                              "implied_t": {"probability_of_profit": probability, "expected_pnl_usd": expected - 11},
                              "har_t": {"probability_of_profit": probability, "expected_pnl_usd": expected + 13}}}


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


def with_score(pick, score, expiration, realised=25.0):
    """A copy of the pick whose chosen candidate (C1) has this score (and this realised-volatility expected PnL, which must not matter)."""
    candidates = [dict(c, baseline_score=score, distributions={**c["distributions"], "realised": {"probability_of_profit": 0.6, "expected_pnl_usd": realised}})
                  if c["id"] == "C1" else c for c in pick.candidates]
    return StoredPick(**{**pick.__dict__, "candidates": candidates, "expiration": expiration})


def test_at_most_two_picks_are_selected_best_expected_pnl_per_risk_first_and_the_rest_are_alternatives():
    picks = [with_score(stored(), score, expiration) for score, expiration in ((0.01, "24NOV26"), (0.04, "27NOV26"), (0.025, "4DEC26"))]
    assert [pick.expiration for pick in select_trades(picks, NOW)] == ["27NOV26", "4DEC26"] and MAX_SELECTED_TRADES == 2
    html_text = build_strategy_panel(picks, NOW, "BTC").html
    assert html_text.count("SELECTED TRADE") == 2 and html_text.count("alternative, not selected") == 1
    assert "Selected trades" in html_text and "27NOV26 (max loss 710 USD), 4DEC26 (max loss 710 USD)" in html_text and "1,420 USD is lost" in html_text
    assert "one bet, not as diversification" in html_text                     # same structure on the same view


def test_a_pick_with_a_non_positive_expectation_or_no_choice_is_never_selected():
    losing = with_score(stored(), -0.0005, "27NOV26")
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


@pytest.mark.parametrize("status, text", [("refused", "declined"), ("invalid_response", "not usable"), ("no_candidates", "no eligible structure")])
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
    assert kwargs["mode"] == "live" and kwargs["method_version"] == "pipeline-5"
    repository.get_strategy_picks.side_effect = RuntimeError("down")
    assert "could not be read: RuntimeError: down" in UnifiedDashboardService(lambda: repository)._strategy_panel("BTC").html
    assert UnifiedDashboardService()._strategy_panel("BTC") is None

    def cannot_connect():
        raise ConnectionError("database down")
    assert "ConnectionError: database down" in UnifiedDashboardService(cannot_connect)._strategy_panel("BTC").html


def test_a_stale_pick_is_never_selected_and_does_not_push_out_a_fresh_one():
    stale = with_score(stored(hours_old=10.0), 0.09, "24NOV26")
    fresh = with_score(stored(), 0.01, "27NOV26")
    assert select_trades([stale, fresh], NOW) == [fresh]
    assert build_strategy_panel([stale], NOW, "BTC").html.count("SELECTED TRADE") == 0


def test_a_missing_maximum_loss_makes_the_total_not_computed_instead_of_smaller():
    unknown = StoredPick(**{**with_score(stored(), 0.01, "27NOV26").__dict__, "max_loss_usd": None})
    html_text = build_strategy_panel([unknown, with_score(stored(), 0.009, "4DEC26")], NOW, "BTC").html
    assert "not computed is lost" in html_text and "710 USD is lost" not in html_text


def test_unknown_structures_are_not_called_one_bet_and_no_trade_picks_are_labelled_so():
    no_family = [StoredPick(**{**with_score(stored(), 100.0, e).__dict__, "choice": "C1"}) for e in ("27NOV26", "4DEC26")]
    for pick in no_family:                                                  # remove the family of the chosen candidate
        pick.candidates[0].pop("family")
    assert "one bet" not in build_strategy_panel(no_family, NOW, "BTC").html
    html_text = build_strategy_panel([stored(status="no_trade", choice="no_trade", quantity=None, response=answer(choice="no_trade", view="no_trade"))], NOW, "BTC").html
    assert "27NOV26 - no trade" in html_text and "alternative" not in html_text.split("Asked in this view")[1]


def test_the_asked_note_names_the_same_targets_as_the_runner():
    from coding.service.strategy_pipeline.live_pick_runner import LIVE_TARGET_DAYS
    numbers = [f"{day:g}" for day in LIVE_TARGET_DAYS]
    assert f"nearest {', '.join(numbers[:-1])} and {numbers[-1]} days" in build_strategy_panel([stored()], NOW, "BTC").html


def test_a_per_coin_pick_shows_what_derives_smallest_order_means():
    one_coin = StoredPick(**{**stored(quantity=1.0).__dict__, "max_loss_usd": 1420.0})
    html_text = build_strategy_panel([one_coin], NOW, "BTC").html
    assert "Per 1 coin per leg (scale-free" in html_text and "smallest order (0.01 coins per leg)" in html_text
    # hand calculation: loss 1,420 x 0.01 = 14.20; fees = 4 legs x (0.5 + min(0.0003 x 58,000 x 0.01 = 0.174, 12.5% x price x 0.01)); capital = 14.20 / 0.05 = 284
    assert "maximum loss 14.20 USD" in html_text and "about 284 USD" in html_text and "Deribit-style fees" in html_text


def test_a_per_coin_pick_without_a_maximum_loss_does_not_break_the_panel():
    broken = StoredPick(**{**stored(quantity=1.0).__dict__, "max_loss_usd": 710.0})
    broken.candidates[0]["max_loss_usd"] = None
    html_text = build_strategy_panel([broken], NOW, "BTC").html
    assert "Derive" in html_text


def test_selection_follows_the_score_not_the_recent_volatility_view_and_the_table_shows_the_score_and_all_three_models():
    rich_under_one_model = with_score(stored(), -0.02, "27NOV26", realised=900.0)                     # rich under recent volatility, bad under another model
    modest = with_score(stored(), 0.03, "4DEC26", realised=-5.0)
    assert [pick.expiration for pick in select_trades([rich_under_one_model, modest], NOW)] == ["4DEC26"]
    without_score = StoredPick(**{**modest.__dict__, "candidates": [{k: v for k, v in c.items() if k != "baseline_score"} for c in modest.candidates]})
    assert select_trades([without_score], NOW) == []
    html_text = build_strategy_panel([modest], NOW, "BTC").html
    for text in ("Score", "+0.0300", "P(profit), implied fat tails", "60%", "Expected PnL, recent volatility", "Expected PnL, implied fat tails", "Expected PnL, forecast fat tails", "+14", "+38",
                 "best score", "per 1 coin per leg"):
        assert text in html_text, text
    assert "realised view" not in html_text


def test_an_expiry_with_nothing_to_offer_is_shown_as_not_asked_never_as_asked_or_no_trade():
    empty = stored("27NOV26", status="no_candidates", choice=None, quantity=None, response="")
    html_text = build_strategy_panel([empty, with_score(stored(), 0.02, "4DEC26")], NOW, "BTC").html
    assert "27NOV26 - not asked" in html_text and "27NOV26 - no trade" not in html_text
    assert "Asked in this view: 4DEC26." in html_text and "not asked, because no eligible structure had a positive score: 27NOV26." in html_text
    assert "Asked in this view: none." in build_strategy_panel([empty], NOW, "BTC").html


def test_the_loss_total_of_per_coin_picks_says_it_is_at_one_coin_per_leg():
    one_coin = with_score(stored(quantity=1.0), 0.02, "27NOV26")
    sized = with_score(stored(quantity=0.5), 0.02, "27NOV26")
    assert "lose the whole maximum at 1 coin per leg (scale it to your own size), 710 USD is lost" in build_strategy_panel([one_coin], NOW, "BTC").html
    assert "lose the whole maximum, 710 USD is lost" in build_strategy_panel([sized], NOW, "BTC").html
