"""
Generalization: a brand-new source is just an ``EvidenceItem`` (no engine change), and a missing source degrades the
confidence with a stated reason instead of crashing.
"""

import pytest

from coding.core.analytics.decision.decision_report import BREAK_UP, INSIDE
from coding.core.analytics.decision.evidence import DIRECTIONAL, PRICE_LEVEL, EvidenceItem
from coding.core.analytics.decision.scenario_engine import build_decision_report
from tests.unit.decision.evidence_builders import NOW, SPOT, directional, level


def baseline():
    """Support (g1), resistance (g2), balanced flow (g3), quality 0.5 each."""
    return [level("support", "g1", 95_000), level("resistance", "g2", 105_000), directional("balanced", "g3", "neutral")]


def report_of(items):
    return build_decision_report("BTC", NOW, SPOT, items)


def test_a_brand_new_directional_source_changes_the_output_without_any_engine_change():
    new_source = EvidenceItem(
        id="exchange_outflow.net", source="Hypothetical exchange outflow (on-chain)", input_group="onchain_exchange_flows",
        kind=DIRECTIONAL, statement="Coins left exchanges on net; it does not say who bought them.", as_of=NOW,
        strength=1.0, quality=0.8, direction="bullish", horizon_days=7.0)
    before, after = report_of(baseline()), report_of(baseline() + [new_source])
    # break up before: g2 -0.5 -> 0. After: + 1 x 0.8 = 0.8 from the new group -> net 0.3 -> 23.1
    assert before.scenario(BREAK_UP).evidence_score == 0.0
    assert after.scenario(BREAK_UP).evidence_score == pytest.approx(100 * 0.3 / 1.3)
    assert after.scenario(BREAK_UP).group_contributions["onchain_exchange_flows"] == pytest.approx(0.8)
    assert "exchange_outflow.net" in after.scenario(BREAK_UP).supporting_evidence_ids
    # it also opposes inside: -0.5 x 0.8 = -0.4 -> net 1.1 -> 52.4 (60 before)
    assert after.scenario(INSIDE).evidence_score == pytest.approx(100 * 1.1 / 2.1)
    assert after.scenario(INSIDE).opposing_evidence_ids == ["exchange_outflow.net"]


def test_a_brand_new_level_source_moves_the_map():
    new_level = EvidenceItem(
        id="volume_profile.node", source="Hypothetical volume profile", input_group="exchange_volume_profile",
        kind=PRICE_LEVEL, statement="A high-volume node; it does not say the price will stop there.", as_of=NOW,
        strength=1.0, quality=0.6, price=102_000)
    report = report_of(baseline() + [new_level])
    # 102,000 weighs 0.6 >= 0.3 and is nearer than 105,000, so it becomes the resistance
    assert report.price_map.resistance.price == 102_000
    assert report.price_map.next_above.price == 105_000
    assert report.scenario(INSIDE).title == "stays between 95,000 and 102,000"


def test_removing_a_source_degrades_the_confidence_with_the_reason():
    full = report_of(baseline())
    assert full.confidence == "high"
    without_flow = report_of(baseline()[:2])
    # inside: 0.5 + 0.5 = 1.0 -> 50; two groups agree -> medium
    assert without_flow.confidence == "medium"
    assert without_flow.confidence_reasons[0] == ("0: 2 independent input groups agree (g1, g2); 3 are needed for high")
    only_support = report_of(baseline()[:1])
    assert only_support.confidence == "low"
    assert only_support.confidence_reasons[0] == "-1: only 1 independent input group supports it (g1)"
    nothing = report_of([])
    assert nothing.scenarios == [] and nothing.confidence == "low" and nothing.top_scenario is None


def test_two_items_of_one_group_do_not_raise_the_confidence_like_two_groups():
    # a second balanced item in g3 is the same opinion: still 3 groups, the same inside score
    same_group = report_of(baseline() + [directional("balanced_again", "g3", "neutral")])
    assert same_group.scenario(INSIDE).evidence_score == pytest.approx(report_of(baseline()).scenario(INSIDE).evidence_score)
    # in a new group it counts: 0.5 more support -> 2.0 -> 66.7
    other_group = report_of(baseline() + [directional("balanced_elsewhere", "g4", "neutral")])
    assert other_group.scenario(INSIDE).evidence_score == pytest.approx(100 * 2.0 / 3.0)
