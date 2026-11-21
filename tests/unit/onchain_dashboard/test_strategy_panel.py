"""Strategy tab: cards of the stored suggestions, ages, warnings, escaping; and the tab in the page."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from coding.core.analytics.unified_dashboard.strategy_panel import StoredSuggestion, build_strategy_panel
from coding.service.analysis.unified_dashboard_service import UnifiedDashboardService

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)


def answer(**overrides):
    return json.dumps({"view": "range_bound", "strategy_name": "Iron condor <b>x</b>", "summary": "Sell rich wings.",
                       "legs": [{"action": "sell", "option_type": "put", "strike": 55000, "quantity": 1},
                                {"action": "buy", "option_type": "put", "strike": 53000, "quantity": 1}],
                       "reasons": ["VRP +14 vol points"], "risks": ["gap below 53,000"], "invalidation": "below 55,000", **overrides})


def stored(expiration="27NOV26", status="ok", response=None, hours_old=1.0, net=-290.0, facts_as_of=True, computed_offset=0.0):
    as_of = NOW - timedelta(hours=hours_old)
    return StoredSuggestion(expiration, as_of + timedelta(hours=computed_offset), as_of if facts_as_of else None, 58000.0,
                            "claude-opus-5-5", status, answer() if response is None else response, net)


def test_a_good_suggestion_shows_its_legs_net_premium_reasons_and_risks():
    page = build_strategy_panel([stored()], NOW, "BTC")
    for text in ("27NOV26", "Range-bound", "55,000", "53,000", "290 USD received", "VRP +14 vol points", "gap below 53,000", "below 55,000",
                 "1.0 h old", "not scored", "never made automatically"):
        assert text in page


def test_model_text_is_escaped():
    page = build_strategy_panel([stored()], NOW, "BTC")
    assert "<b>x</b>" not in page and "&lt;b&gt;x&lt;/b&gt;" in page


def test_old_facts_are_marked_and_a_missing_facts_time_is_said():
    old = build_strategy_panel([stored(hours_old=7.0)], NOW, "BTC")
    assert "7.0 h old" in old and "Ask again before relying on this" in old
    assert "time of the facts was not recorded" in build_strategy_panel([stored(facts_as_of=False)], NOW, "BTC")


def test_a_rejected_suggestion_says_do_not_use():
    assert "Do not use" in build_strategy_panel([stored(status="legs_rejected", net=None)], NOW, "BTC")


@pytest.mark.parametrize("status, text", [("refused", "declined"), ("invalid_response", "not usable")])
def test_a_refused_or_unusable_answer_is_stated_with_the_raw_text(status, text):
    page = build_strategy_panel([stored(status=status, response="raw words <i>here</i>")], NOW, "BTC")
    assert text in page and "raw words &lt;i&gt;here&lt;/i&gt;" in page


def test_no_trade_has_no_legs_table_or_premium():
    page = build_strategy_panel([stored(response=answer(view="no_trade", strategy_name="No trade", legs=[]), net=None)], NOW, "BTC")
    assert "No trade" in page and "No trade: No trade" not in page and "<table" not in page and "Net at the quotes" not in page


def test_expired_expiries_are_left_out_and_the_nearest_comes_first():
    page = build_strategy_panel([stored("25DEC26"), stored("20NOV26"), stored("27NOV26")], NOW, "BTC")
    assert "20NOV26" not in page and page.index("27NOV26") < page.index("25DEC26")


def test_the_newest_of_two_answers_for_an_expiry_is_shown():
    older = stored(response=answer(strategy_name="OLDER"), computed_offset=-1.0)
    newer = stored(response=answer(strategy_name="NEWER"))
    page = build_strategy_panel([older, newer], NOW, "BTC")
    assert "NEWER" in page and "OLDER" not in page


def test_nothing_stored_and_a_read_error_are_both_said():
    assert "No stored suggestion" in build_strategy_panel([], NOW, "BTC")
    assert "could not be read" in build_strategy_panel([], NOW, "BTC", read_error="connection refused")


def test_a_naive_time_is_refused():
    with pytest.raises(ValueError):
        build_strategy_panel([], datetime(2026, 11, 21, 12, 0), "BTC")


def test_the_page_has_a_strategy_tab_only_when_a_panel_is_given(real_result):
    from tests.unit.onchain_dashboard.test_unified_dashboard import PLOTLY_STAND_IN, make_report_page, tab_labels
    from coding.core.analytics.onchain_dashboard import build_onchain_dashboard_page
    from coding.core.analytics.unified_dashboard import build_unified_dashboard_page

    onchain = build_onchain_dashboard_page(real_result, PLOTLY_STAND_IN)
    without = build_unified_dashboard_page(make_report_page(), onchain, "BTC")
    with_panel = build_unified_dashboard_page(make_report_page(), onchain, "BTC", strategy_panel_html=build_strategy_panel([stored()], NOW, "BTC"))

    assert "Strategy" not in tab_labels(without)
    labels = tab_labels(with_panel)
    assert labels[labels.index("Strategy") - 1] == "Evidence" or "Strategy" in labels
    assert "Iron condor" in with_panel and "%%" not in with_panel


def test_the_service_reads_the_rows_and_survives_a_database_error():
    repository = MagicMock()
    repository.get_latest_strategy_suggestions.return_value = [
        {"expiration": "25DEC27", "computed_at": NOW, "facts_as_of": NOW, "reference_price": 58000.0, "model": "m", "status": "ok",
         "response": answer(), "net_premium_usd": -290.0}]
    assert "Iron condor" in UnifiedDashboardService(repository)._strategy_panel("BTC")
    repository.get_latest_strategy_suggestions.side_effect = RuntimeError("down")
    assert "could not be read: RuntimeError: down" in UnifiedDashboardService(repository)._strategy_panel("BTC")
    assert UnifiedDashboardService()._strategy_panel("BTC") is None
