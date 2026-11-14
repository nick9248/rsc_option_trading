"""Tests of the Decision tab: the pure fragments, the section builder with a fake DecisionMakerService, and the failure paths."""

import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from coding.core.analytics.decision.decision_report import (
    BREAK_DOWN, BREAK_UP, INSIDE, ChangeTrigger, DecisionItem, DecisionReport, ScenarioAssessment)
from coding.core.analytics.decision.evidence import EvidenceItem
from coding.core.analytics.decision.price_map import MapLevel, PriceMap
from coding.core.analytics.report import decision_fragments as fragments
from coding.service.report import decision_section
from coding.service.report.decision_section import NOT_RECORDED_NOTE, build_decision_section
from coding.service.report.report_sections import SECTION_BUILDERS, SectionContext
from coding.service.report.report_service import SYNC_HINT
from tests.unit.report.test_report_service import make_service, written_page

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
SPOT = 70_000.0


def map_level(price, groups=("deribit_open_interest",), ids=("a",), weight=0.6):
    return MapLevel(price, price - 100, price + 100, weight, list(groups), list(ids), True)


def make_evidence():
    return [
        EvidenceItem("dealer.put_wall", "Dealer GEX 27NOV26, open interest", "deribit_open_interest", "price_level",
                     "A put wall at 66,000 <script>alert(1)</script>. It does not say price holds.", NOW - timedelta(minutes=40),
                     0.5, 0.6, price=66_000.0, quality_reasons=("assumption-based", "stale: 4.0 h old")),
        EvidenceItem("option_flow.screen_net", "Option flow (Deribit taker trades)", "deribit_trades", "directional",
                     "The net taker flow leans bullish.", NOW - timedelta(minutes=5), 0.3, 0.5, direction="bullish"),
        EvidenceItem("menthorq.hvl", "MenthorQ replica", "deribit_open_interest", "price_level", "An HVL at 68,000.",
                     NOW - timedelta(hours=2), 0.5, 0.7, price=68_000.0),
        EvidenceItem("dealer.total_gex", "Dealer GEX, flow-based", "deribit_trades", "range_regime", "Total GEX is positive.",
                     NOW, 0.5, 0.06, regime="range_bound"),
    ]


def make_scenarios():
    return [
        ScenarioAssessment(INSIDE, "stays above 66,000 (no mapped level above within 15%: the upside is unmapped)", 66_000.0,
                           None, None, None, 54.5, 1.2, 0.0, {"deribit_open_interest": 0.6, "deribit_trades": 0.6},
                           ["dealer.put_wall", "option_flow.screen_net"], [], [1.0, 3.0], "medium",
                           ["0: 2 distinct input groups agree", "+3 points in total -> medium"]),
        ScenarioAssessment(BREAK_DOWN, "breaks below 66,000, no further mapped level below within 15%", None, None, 66_000.0,
                           None, 0.0, 0.0, 0.6, {"deribit_open_interest": -0.6}, [], ["dealer.put_wall"], None, "low",
                           ["-1: no distinct input group supports it"])]


def make_report(**changes):
    support = map_level(66_000.0, ids=("dealer.put_wall",))
    price_map = PriceMap(SPOT, 700.0, 10_500.0, [support, MapLevel(75_500.0, 75_400.0, 75_600.0, 0.1, ["deribit_trades"],
                                                                    ["x"], False)],
                         support, None, None, None, ["far.level"], [])
    report = DecisionReport(
        currency="BTC", as_of=NOW, reference_price=SPOT, score_scale="0-100, uncalibrated: relative support, not a probability",
        evidence=make_evidence(), price_map=price_map, scenarios=make_scenarios(), top_scenario=INSIDE, confidence="medium",
        confidence_reasons=["+3 points in total -> medium"],
        what_would_change=[ChangeTrigger("option_flow.screen_net", "if option_flow.screen_net turned bearish", BREAK_DOWN),
                           ChangeTrigger("dealer.put_wall", "if dealer.put_wall (price level at 66,000) were absent", None)],
        decisions=[DecisionItem("notice", "These are decision-support items, not orders and not price targets."),
                   DecisionItem("reference_hedge", "Reference level for a hedge: 66,000 (nearest mapped level below)."),
                   DecisionItem("data_caveat", "Data caveat: Option flow & <b>strikes</b>: stale.")],
        not_computed=["liquidation estimate: no BTCUSDT snapshot computed in the last 24 h <i>"],
        context={"spot_time": (NOW - timedelta(minutes=30)).isoformat(), "chain_snapshot": (NOW - timedelta(minutes=40)).isoformat(),
                 "caveats": ["the index price is old & far"]})
    return replace(report, **changes) if changes else report


# ---------------------------------------------------------------- fragments

def test_header_marks_the_top_scenario_and_says_the_score_is_uncalibrated():
    html_text = fragments.decision_header_fragment(make_report())
    assert "<mark>stays above 66,000 (no mapped level above within 15%: the upside is unmapped)</mark>" in html_text
    assert "Confidence: medium" in html_text
    assert "Evidence score 54 of 100 (uncalibrated, not a probability)" in html_text
    assert "uncalibrated, not a probability" in html_text
    assert html_text.count("<mark>") == 1


def test_header_without_a_top_scenario_says_so_and_gives_the_reason():
    report = make_report(top_scenario=None, confidence="low", confidence_reasons=["no mapped level on either side of the spot"])
    html_text = fragments.decision_header_fragment(report)
    assert "<mark>No scenario has net support</mark>" in html_text and "no mapped level on either side" in html_text
    assert "Confidence: low" in html_text


def test_every_fragment_escapes_its_text_and_never_writes_a_dollar_sign():
    page = fragments.decision_body_fragment(make_report())
    assert "<script>" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<b>" not in page and "&lt;b&gt;strikes&lt;/b&gt;" in page
    assert "<i>" not in page and "&lt;i&gt;" in page
    assert "$" not in page
    assert "index price is old &amp; far" in page


def test_price_map_one_sided_is_stated_plainly():
    html_text = fragments.price_map_fragment(make_report())
    assert "One-sided map: the upside is unmapped" in html_text and "within 15%" in html_text
    assert "unmapped: no meaningful level within 15% of the spot" in html_text
    for text in ("66,000", "70,000", "-5.7%", "0.60", "dealer.put_wall", "deribit_open_interest"):
        assert text in html_text, text
    assert "2 bands within 15% of the spot, 1 meaningful (weight at least 0.3); 1 price levels beyond" in html_text
    assert html_text.index("Resistance") < html_text.index("Spot") < html_text.index("Support (nearest")


def test_price_map_downside_unmapped_and_neither_side_mapped():
    price_map = make_report().price_map
    resistance_only = replace(price_map, support=None, resistance=map_level(74_000.0), next_above=map_level(76_000.0))
    assert "One-sided map: the downside is unmapped" in fragments.price_map_fragment(make_report(price_map=resistance_only))
    nothing = replace(price_map, support=None, levels=[])
    html_text = fragments.price_map_fragment(make_report(price_map=nothing))
    assert "Neither side is mapped" in html_text and "no scenario can be built" in html_text
    assert "One-sided" not in html_text


def test_price_map_lists_a_band_at_the_spot():
    price_map = replace(make_report().price_map, at_spot=[MapLevel(70_000.0, 69_900.0, 70_100.0, 0.5, ["deribit_trades"], ["z"], True)])
    assert "At the spot (the band contains the spot)" in fragments.price_map_fragment(make_report(price_map=price_map))


def test_scenarios_table_has_scores_labels_evidence_and_reasons():
    html_text = fragments.scenarios_fragment(make_report())
    for text in ("54.5", "medium", "low", "dealer.put_wall, option_flow.screen_net", "deribit_open_interest +0.60",
                 "<li>0: 2 distinct input groups agree</li>", "-1: no distinct input group supports it",
                 "do not add up to 100", "not a probability"):
        assert text in html_text, text
    assert "none" in html_text                       # the break down has no supporting evidence: an empty list, said as such
    assert "No scenario" in fragments.scenarios_fragment(make_report(scenarios=[]))


def test_decisions_are_listed_exactly_as_produced_with_the_note():
    report = make_report()
    html_text = fragments.decisions_fragment(report)
    assert "Decision support, not orders, not targets." in html_text
    assert html_text.count("<li>") == len(report.decisions) == 3
    assert "<strong>reference hedge</strong> Reference level for a hedge: 66,000 (nearest mapped level below)." in html_text


def test_evidence_table_is_grouped_by_input_group_with_every_column():
    html_text = fragments.evidence_fragment(make_report())
    for header in ("Id", "Source", "Kind", "Direction or regime", "Strength", "Quality", "Quality reasons", "As of (UTC)",
                   "Says / does not say"):
        assert f">{header}</th>" in html_text, header
    assert "count once" in html_text or "counts a group once" in html_text
    open_interest_row = html_text.index("deribit_open_interest: 2 items, counted once")
    trades_row = html_text.index("deribit_trades: 2 items, counted once")
    assert open_interest_row < trades_row
    positions = {item_id: html_text.index(f'<th scope="row">{item_id}</th>')
                 for item_id in ("dealer.put_wall", "menthorq.hvl", "option_flow.screen_net", "dealer.total_gex")}
    assert open_interest_row < positions["dealer.put_wall"] < positions["menthorq.hvl"] < trades_row
    assert trades_row < positions["option_flow.screen_net"] and trades_row < positions["dealer.total_gex"]
    for text in ("assumption-based; stale: 4.0 h old", "2026-11-14 11:20 UTC", "bullish", "range-bound", "0.06", "0.50", "66,000",
                 "not applicable"):
        assert text in html_text, text
    assert fragments.evidence_fragment(make_report(evidence=[])) == "<p>No evidence was computed.</p>"


def test_what_would_change_lists_the_triggers_and_says_when_there_are_none():
    html_text = fragments.change_triggers_fragment(make_report())
    assert "if option_flow.screen_net turned bearish" in html_text and "breaks below" in html_text
    assert "no scenario with net support" in html_text
    assert "No single item&#x27;s flip or removal changes the top scenario." in fragments.change_triggers_fragment(
        make_report(what_would_change=[]))


def test_caveats_and_freshness_list_not_computed_caveats_and_ages():
    html_text = fragments.caveats_fragment(make_report())
    assert "liquidation estimate: no BTCUSDT snapshot computed in the last 24 h" in html_text
    assert "the index price is old &amp; far" in html_text
    assert "Index price (spot)" in html_text and "0.5 h" in html_text            # 30 minutes
    assert "Option chain snapshot" in html_text and "0.7 h" in html_text         # 40 minutes
    assert "Dealer GEX 27NOV26, open interest" in html_text and "MenthorQ replica" in html_text and "2.0 h" in html_text
    clean = fragments.caveats_fragment(make_report(not_computed=[], context={"caveats": []}))
    assert "Every source was computed." in clean and "None." in clean
    assert clean.count("not computed") >= 2          # no time in the context: the spot and the chain snapshot say so, never 0 h


def test_a_context_without_times_gives_not_computed_ages():
    html_text = fragments.caveats_fragment(make_report(context={}))
    rows = re.findall(r"<tr><th scope=\"row\">(Index price \(spot\)|Option chain snapshot)</th><td>([^<]*)</td><td class=\"num\">([^<]*)</td>",
                      html_text)
    assert rows == [("Index price (spot)", "not computed", "not computed"), ("Option chain snapshot", "not computed", "not computed")]


# ---------------------------------------------------------------- the section

class FakeDecisionMakerService:
    """Stands in for DecisionMakerService; records how it is built and called; any write raises."""

    instances = []
    outcome = None

    def __init__(self, repository=None, clock=None):
        self.repository, self.clock = repository, clock
        self.calls = []
        FakeDecisionMakerService.instances.append(self)

    def build_report(self, currency):
        self.calls.append((currency, self.clock()))
        if isinstance(FakeDecisionMakerService.outcome, Exception):
            raise FakeDecisionMakerService.outcome
        return FakeDecisionMakerService.outcome

    def save_snapshot(self, report):
        raise AssertionError("the report must never record a decision")


@pytest.fixture()
def fake_service(monkeypatch):
    FakeDecisionMakerService.instances = []
    FakeDecisionMakerService.outcome = make_report()
    monkeypatch.setattr(decision_section, "DecisionMakerService", FakeDecisionMakerService)
    return FakeDecisionMakerService


def test_section_calls_the_service_once_with_the_report_clock_and_renders_only_its_result(fake_service):
    repository = object()
    context = SectionContext("BTC", NOW, repository)
    section = build_decision_section(context)
    (service,) = fake_service.instances
    assert service.repository is repository and service.calls == [("BTC", NOW)]
    assert section.key == "decision" and section.title == "Decision" and not section.failed
    assert section.body_html == fragments.decision_body_fragment(make_report())
    assert "Report time 2026-11-14 12:00 UTC; spot 70,000." == section.as_of
    assert NOT_RECORDED_NOTE in section.notes and "scripts/decision_maker.py --save" in NOT_RECORDED_NOTE
    assert make_report().disclaimer in section.notes
    assert section.figures == {} and section.switchers == {} and section.pages == {}      # no plotly.js needed


def test_decision_is_the_last_section_and_the_others_are_unchanged():
    assert [key for key, _, _ in SECTION_BUILDERS] == ["menthorq", "futures-flow", "option-flow", "dealer-levels", "levels", "decision"]
    assert SECTION_BUILDERS[-1][1:] == ("Decision", build_decision_section)


def test_a_lookup_error_becomes_a_missing_data_section_with_the_sync_hint(tmp_path, fake_service):
    fake_service.outcome = LookupError("no BTC option-chain snapshot with an index price is stored")
    service = make_service(tmp_path, builders=[("decision", "Decision", build_decision_section)])
    result = service.generate("BTC", skip_sync=True)
    page = written_page(result)
    assert result.failed_sections == ["Decision"]
    assert "No BTC option-chain snapshot with an index price is stored." in page and SYNC_HINT in page
    assert "How to read this tab" in page and "Evidence and input groups" in page          # the help stays on a failed tab


def test_any_other_error_shows_its_type_and_message(tmp_path, fake_service):
    fake_service.outcome = RuntimeError("boom")
    result = make_service(tmp_path, builders=[("decision", "Decision", build_decision_section)]).generate("BTC", skip_sync=True)
    page = written_page(result)
    assert result.failed_sections == ["Decision"]
    assert "Decision failed with RuntimeError: boom." in page and SYNC_HINT not in page


def test_a_built_decision_section_is_in_the_page_with_its_help_and_never_saves(tmp_path, fake_service):
    result = make_service(tmp_path, builders=[("decision", "Decision", build_decision_section)]).generate("BTC", skip_sync=True)
    page = written_page(result)
    assert result.failed_sections == [] and [outcome.key for outcome in result.sections] == ["decision"]
    assert "Best-supported scenario: <mark>" in page and "uncalibrated, not a probability" in page
    assert "How to read this tab" in page and "Evidence and input groups" in page
    assert fake_service.instances[0].calls[0][1] == result.generated_at


def test_the_section_module_never_refers_to_saving_a_snapshot():
    import inspect
    assert "save_snapshot" not in inspect.getsource(decision_section)
