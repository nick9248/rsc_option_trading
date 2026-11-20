"""
The scenario engine with hand-calculated expectations. Spot 100,000 (band 1,000, window 15 %); config priors as of
writing: score = 100 x net / (net + 1), conflict penalty 1, group cap 1, inside opposition of a one-sided lean 0.5.
"""

import json
import random
from datetime import timedelta

import pytest

from coding.core.analytics.decision.decision_report import BREAK_DOWN, BREAK_UP, INSIDE, DecisionReport
from coding.core.analytics.decision.evidence import EvidenceItem
from coding.core.analytics.decision.scenario_engine import apply_freshness, build_decision_report
from tests.unit.decision.evidence_builders import NOW, SPOT, directional, level, magnet, regime


def report_of(items, not_computed=()):
    return build_decision_report("BTC", NOW, SPOT, items, not_computed)


def test_scores_follow_the_hand_calculation():
    items = [level("support", "g1", 95_000, quality=0.8), level("resistance", "g2", 105_000),
             directional("lean", "g3", "bullish", quality=0.6, strength=0.5)]
    report = report_of(items)
    inside, up, down = report.scenario(INSIDE), report.scenario(BREAK_UP), report.scenario(BREAK_DOWN)
    # inside: g1 +1 x 0.8 = 0.8; g2 +0.5; g3 value -0.5 x 0.5 = -0.25, x quality 0.6 = -0.15
    assert inside.group_contributions == pytest.approx({"g1": 0.8, "g2": 0.5, "g3": -0.15})
    assert (inside.support, inside.opposition) == pytest.approx((1.3, 0.15))
    assert inside.evidence_score == pytest.approx(100 * 1.15 / 2.15)            # 53.49
    # break up: g2 -0.5, g3 +0.5 x 0.6 = +0.3 -> net max(0, 0.3 - 0.5) = 0
    assert up.group_contributions == pytest.approx({"g2": -0.5, "g3": 0.3}) and up.evidence_score == 0.0
    # break down: g1 -0.8, g3 -0.3 -> no support
    assert down.evidence_score == 0.0 and down.supporting_evidence_ids == []
    assert report.top_scenario == INSIDE and [item.key for item in report.scenarios] == [INSIDE, BREAK_UP, BREAK_DOWN]
    assert inside.supporting_evidence_ids == ["resistance", "support"] and inside.opposing_evidence_ids == ["lean"]
    # confidence: 2 groups agree (0), item conflict 0.15 / (0.8 + 0.5 + 0.15) = 10.3 % (0), mean quality of the
    # supporting items (0.8 + 0.5) / 2 = 0.65 (+1), fresh (0), margin 53.5 (+1) -> 2 points, but 2 groups < 3 -> medium
    assert inside.confidence == "medium"
    assert inside.confidence_reasons[0].startswith("0: 2 distinct input groups agree")
    assert "10%" in inside.confidence_reasons[1]
    assert inside.confidence_reasons[-1] == "+2 points in total -> medium"
    assert up.confidence == "low" and down.confidence == "low"


def three_group_range():
    """Support (g1), resistance (g2) and balanced flow (g3), all quality 0.5: inside 0.5 + 0.5 + 0.5 = 1.5 -> score 60."""
    return [level("support", "g1", 95_000), level("resistance", "g2", 105_000), directional("balanced", "g3", "neutral")]


def test_three_agreeing_groups_give_high_confidence():
    report = report_of(three_group_range())
    inside = report.scenario(INSIDE)
    assert inside.evidence_score == pytest.approx(60.0)
    # +1 groups, +1 no conflict, 0 quality 0.5, 0 fresh, +1 margin 60 -> 3 points and 3 groups -> high
    assert inside.confidence == "high" and report.confidence == "high"
    assert inside.confidence_reasons[-1] == "+3 points in total -> high"


def test_conflicting_evidence_lowers_the_confidence_with_the_reason():
    conflicting = [directional("bull1", "g4", "bullish"), directional("bull2", "g6", "bullish"),
                   directional("bear1", "g5", "bearish"), directional("bear2", "g7", "bearish")]
    report = report_of(three_group_range() + conflicting)
    inside = report.scenario(INSIDE)
    # each one-sided item opposes inside by 0.5 x 1 = 0.5 at quality 0.5 -> -0.25 per group: opposition 1.0, net 0.5
    assert inside.evidence_score == pytest.approx(100 * 0.5 / 1.5)
    # item conflict: 4 x 0.5 x 0.5 = 1.0 against 3 x 0.5 x 1 = 1.5 -> 1.0 / 2.5 = 40 % > 35 % -> -1
    assert "-1: 40% of the evidence speaking to it" in inside.confidence_reasons[1]
    # break up: g2 -0.5, two bullish +0.5 each, two bearish -0.5 each -> support 1.0 < opposition 1.5 -> 0; margin 33.3 (+1)
    assert report.scenario(BREAK_UP).evidence_score == 0.0
    # +1 groups, -1 conflict, 0 quality, 0 fresh, +1 margin -> 1 point -> medium (high without the conflict)
    assert inside.confidence == "medium"


def test_a_tie_is_broken_by_the_fixed_order_and_a_narrow_margin_costs_a_point():
    items = [level("support", "g1", 95_000), level("resistance", "g2", 105_000),
             directional("bull", "g3", "bullish", quality=1.0)]
    report = report_of(items)
    # inside: 0.5 + 0.5 - 0.5 x 1 = net 0.5 -> 33.3; break up: 1.0 - 0.5 = 0.5 -> 33.3: a tie, inside comes first
    assert report.scenario(INSIDE).evidence_score == pytest.approx(report.scenario(BREAK_UP).evidence_score)
    assert report.top_scenario == INSIDE
    assert "-1: leads the next scenario by only 0.0 points" in report.scenario(INSIDE).confidence_reasons
    assert report.scenario(BREAK_UP).confidence_reasons[-2].startswith("-1: 0.0 points below the top scenario")


def test_one_sided_map_gives_honest_scenarios_and_no_invented_level():
    report = report_of([level("support", "g1", 95_000), level("far", "g2", 120_000)])
    assert [item.key for item in report.scenarios] == [INSIDE, BREAK_DOWN]
    assert report.scenario(INSIDE).title == "stays above 95,000 (no mapped level above within 15%: the upside is unmapped)"
    assert report.scenario(INSIDE).upper is None
    assert report.scenario(BREAK_DOWN).title == "breaks below 95,000, no further mapped level below within 15%"
    texts = [item.text for item in report.decisions]
    assert "No meaningful level above within 15% of the spot: the upside is unmapped." in texts
    assert not any(item.kind == "reference_range" for item in report.decisions)
    assert report.price_map.left_out == ["far"]


def test_no_level_at_all_gives_no_scenario_and_low_confidence():
    report = report_of([directional("bull", "g1", "bullish")], not_computed=["dealer GEX: no chain"])
    assert report.scenarios == [] and report.top_scenario is None and report.confidence == "low"
    assert report.confidence_reasons == ["no mapped level on either side of the spot"]
    kinds = [item.kind for item in report.decisions]
    assert kinds.count("unmapped") == 2
    assert "Not computed: dealer GEX: no chain." in [item.text for item in report.decisions]


def test_regime_and_magnets_follow_their_rules():
    items = [level("support", "g1", 95_000), level("resistance", "g2", 105_000), regime("damped", "g3", "range_bound"),
             magnet("pool_above", "g4", 106_000), magnet("pool_inside", "g5", 102_000)]
    report = report_of(items)
    # range_bound: inside +1 x 0.5 = 0.5; each break -0.5 x 1 x 0.5 = -0.25
    assert report.scenario(INSIDE).group_contributions["g3"] == pytest.approx(0.5)
    assert report.scenario(BREAK_UP).group_contributions["g3"] == pytest.approx(-0.25)
    # pool above the resistance: proximity 1 - 6,000 / 10,000 = 0.4 -> break up +0.4 x 0.5 = 0.2
    assert report.scenario(BREAK_UP).group_contributions["g4"] == pytest.approx(0.2)
    # pool inside the range: proximity 1 - 2,000 / 10,000 = 0.8, half -> inside +0.4 x 0.5 = 0.2
    assert report.scenario(INSIDE).group_contributions["g5"] == pytest.approx(0.2)
    assert "g4" not in report.scenario(BREAK_DOWN).group_contributions


def test_expansion_regime_opposes_inside_and_supports_both_breaks():
    report = report_of([level("support", "g1", 95_000), level("resistance", "g2", 105_000), regime("wild", "g3", "expansion")])
    assert report.scenario(INSIDE).group_contributions["g3"] == pytest.approx(-0.5)
    assert report.scenario(BREAK_UP).group_contributions["g3"] == pytest.approx(0.25)
    assert report.scenario(BREAK_DOWN).group_contributions["g3"] == pytest.approx(0.25)


def test_stale_data_loses_quality_and_lowers_confidence():
    # deribit_trades has a 3 h freshness limit: 5 h old -> x0.5, 13 h old (more than 4 x 3) -> x0.1
    fresh, stale, very_stale = (level(name, "deribit_trades", 95_000, age_hours=age) for name, age in
                                (("fresh", 0), ("stale", 5), ("very_stale", 13)))
    assessed = {item.id: item for item in apply_freshness([fresh, stale, very_stale], NOW)}
    assert assessed["fresh"].quality == 0.5 and assessed["fresh"].quality_reasons == ()
    assert assessed["stale"].quality == pytest.approx(0.25)
    assert assessed["stale"].quality_reasons == ("stale: 5.0 h old (limit 3 h), quality x0.5",)
    assert assessed["very_stale"].quality == pytest.approx(0.05)
    assert assessed["very_stale"].quality_reasons[0].startswith("stale (very): 13.0 h old")

    report = report_of(three_group_range()[:2] + [directional("balanced", "deribit_trades", "neutral", age_hours=5)])
    inside = report.scenario(INSIDE)
    assert "-1: 1 supporting item(s) are stale (balanced)" in inside.confidence_reasons
    # +1 groups, +1 no conflict, 0 quality (0.5 + 0.5 + 0.25) / 3 = 0.42, -1 stale, +1 margin -> 2 points, which would be
    # high, but a stale supporting item caps the label at medium (the same evidence fresh is high, test above)
    assert inside.confidence == "medium"
    assert "capped at medium: high needs fresh supporting evidence" in inside.confidence_reasons


def test_evidence_newer_than_the_report_is_refused_and_times_must_be_aware():
    future = level("future", "g1", 95_000, age_hours=-1)
    with pytest.raises(ValueError, match="lookahead"):
        report_of([future])
    with pytest.raises(ValueError):
        build_decision_report("BTC", NOW.replace(tzinfo=None), SPOT, [])
    with pytest.raises(ValueError):
        EvidenceItem("x", "s", "g", "price_level", "t", NOW.replace(tzinfo=None), 1.0, 1.0, price=1.0)


def test_bad_items_are_refused():
    for bad in ({"strength": float("nan")}, {"quality": 1.5}, {"kind": "guess"}, {"price": None}):
        arguments = {"id": "x", "source": "s", "input_group": "g", "kind": "price_level", "statement": "t", "as_of": NOW,
                     "strength": 1.0, "quality": 1.0, "price": 1.0, **bad}
        with pytest.raises(ValueError):
            EvidenceItem(**arguments)
    with pytest.raises(ValueError, match="duplicate"):
        report_of([level("same", "g1", 95_000), level("same", "g2", 96_000)])


def test_what_would_change_lists_the_items_whose_flip_or_removal_changes_the_top():
    items = [level("support", "g1", 95_000), level("resistance", "g2", 105_000),
             directional("bull_strong", "g3", "bullish", quality=1.0), directional("bull_weak", "g4", "bullish", quality=0.3)]
    report = report_of(items)
    # break up: 1.0 + 0.3 - 0.5 = 0.8 -> 44.4; inside: 1.0 - 0.5 - 0.15 = 0.35 -> 25.9
    assert report.top_scenario == BREAK_UP
    triggers = {trigger.evidence_id: trigger for trigger in report.what_would_change}
    # bull_strong bearish: break up 0.3 - 1.0 - 0.5 < 0 -> inside leads. bull_weak bearish: break up 1.0 - 0.3 - 0.5 = 0.2
    # (16.7) < inside 0.35 (25.9). Without the resistance there is no break up; "stays above 95,000" is open upward, so the
    # bullish items no longer oppose it: inside = the support's 0.5 -> 33.3 leads.
    assert sorted(triggers) == ["bull_strong", "bull_weak", "resistance"]
    assert triggers["bull_strong"].change == "if bull_strong turned bearish" and triggers["bull_strong"].new_top_scenario == INSIDE
    assert triggers["resistance"].new_top_scenario == INSIDE
    assert any(item.text.startswith("What would change the top scenario: ") for item in report.decisions)


def test_same_input_gives_identical_json_whatever_the_order():
    items = three_group_range() + [magnet("pool", "g4", 103_000), regime("wild", "g5", "expansion")]
    first = json.dumps(report_of(items).to_dict(), sort_keys=True)
    shuffled = list(items)
    random.Random(7).shuffle(shuffled)
    assert json.dumps(report_of(shuffled).to_dict(), sort_keys=True) == first
    assert json.dumps(report_of(items).to_dict(), sort_keys=True) == first


def test_the_report_survives_a_json_round_trip():
    items = three_group_range() + [directional("lean", "g4", "bearish", horizon_days=2.5), magnet("pool", "g5", 97_000),
                                   level("stale", "deribit_trades", 104_800, age_hours=4)]
    report = report_of(items, not_computed=["futures taker ratio: none"])
    text = json.dumps(report.to_dict(), allow_nan=False)
    assert DecisionReport.from_dict(json.loads(text)) == report


def test_decisions_are_reference_levels_and_conditions_never_targets():
    report = report_of(three_group_range() + [level("below", "g1", 90_000), level("above", "g2", 110_000)])
    texts = [item.text for item in report.decisions]
    assert texts[0] == "These are decision-support items (reference levels and conditions), not orders and not price targets."
    assert ("Reference range for a strategy: 95,000 to 105,000 (5.0% below and 5.0% above the spot 100,000; support from g1, "
            "resistance from g2).") in texts
    assert ("Reference level for a hedge: 95,000 (nearest mapped level below, 5.0% under the spot); next mapped level "
            "below: 90,000 (10.0% under the spot).") in texts
    assert not any("target" in text.lower() and "not price targets" not in text for text in texts)
    assert "not a probability" in report.score_scale and "not a forecast" in report.disclaimer
    assert report.scenario(BREAK_UP).next_level == 110_000


def test_horizon_of_a_scenario_is_the_range_of_its_supporting_items():
    items = three_group_range()[:2] + [directional("short", "g3", "neutral", horizon_days=1.0),
                                       directional("long", "g4", "neutral", horizon_days=12.5)]
    assert report_of(items).scenario(INSIDE).horizon_days == [1.0, 12.5]
    assert report_of(three_group_range()).scenario(INSIDE).horizon_days is None


def test_items_exactly_at_the_report_time_are_accepted():
    item = level("now", "g1", 95_000)
    assert item.as_of == NOW
    assert report_of([item]).evidence[0].as_of == NOW
    assert apply_freshness([level("old", "g1", 95_000, age_hours=6)], NOW)[0].quality_reasons == ()     # at the limit: fresh
    assert NOW - timedelta(hours=6) == apply_freshness([level("old", "g1", 95_000, age_hours=6)], NOW)[0].as_of
