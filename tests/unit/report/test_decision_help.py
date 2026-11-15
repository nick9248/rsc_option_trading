"""The Decision tab help: every constant and worked example recomputed against the decision engine itself."""

import statistics
from datetime import timedelta

import pytest

from coding.core.analytics.decision import decision_config as config
from coding.core.analytics.decision.decision_report import BREAK_DOWN, BREAK_UP, INSIDE, SCENARIO_ORDER
from coding.core.analytics.decision.evidence import EvidenceItem
from coding.core.analytics.decision.independence import group_contribution
from coding.core.analytics.decision.price_map import build_price_map
from coding.core.analytics.decision.scenario_engine import (
    _confidence, _variant, apply_freshness, build_decision_report, stances)
from coding.core.analytics.report import how_to_read as help_module
from coding.core.analytics.report.how_to_read import (
    SECTION_HELP, confidence_label, confidence_points, evidence_score, group_contribution_value, quality_after_age)
from coding.service.decision import evidence_extractors as extractors
from tests.unit.decision.evidence_builders import NOW, directional, level, magnet, regime
from tests.unit.decision.test_evidence_extractors import dealer_levels, open_interest_row
from tests.unit.report.test_how_to_read import case_of, topic_of

SPOT = help_module.EXAMPLE_SPOT
OPEN_INTEREST = "deribit_open_interest"
TRADES = "deribit_trades"


def example_items():
    """The put wall (open interest) and call wall (trades) of the 'two groups agree' example."""
    return [level("put", OPEN_INTEREST, help_module.DECISION_PUT_WALL, help_module.DECISION_WALL_QUALITY,
                  help_module.DECISION_WALL_STRENGTH),
            level("call", TRADES, help_module.DECISION_CALL_WALL, help_module.DECISION_WALL_QUALITY,
                  help_module.DECISION_WALL_STRENGTH)]


def test_the_decision_help_exists_with_its_four_topics():
    assert SECTION_HELP["decision"].section_key == "decision"
    assert len(SECTION_HELP["decision"].topics) == 4
    for title in ("Evidence and input groups", "The price map", "Scenarios, evidence score", "Decisions, what would change"):
        assert topic_of("decision", title)


def test_constants_match_the_decision_config():
    pairs = {
        "DECISION_SCORE_HALF_POINT": config.SCORE_HALF_POINT, "DECISION_CONFLICT_PENALTY": config.CONFLICT_PENALTY,
        "DECISION_GROUP_CONTRIBUTION_CAP": config.GROUP_CONTRIBUTION_CAP,
        "DECISION_MINIMUM_LEVEL_WEIGHT": config.MINIMUM_LEVEL_WEIGHT,
        "DECISION_MAXIMUM_LEVEL_DISTANCE_SHARE": config.MAXIMUM_LEVEL_DISTANCE_SHARE,
        "DECISION_LEVEL_BAND_SHARE": config.LEVEL_BAND_SHARE, "DECISION_FRESHNESS_LIMIT_HOURS": config.FRESHNESS_LIMIT_HOURS[OPEN_INTEREST],
        "DECISION_STALE_QUALITY_FACTOR": config.STALE_QUALITY_FACTOR, "DECISION_VERY_STALE_MULTIPLE": config.VERY_STALE_MULTIPLE,
        "DECISION_VERY_STALE_QUALITY_FACTOR": config.VERY_STALE_QUALITY_FACTOR,
        "DECISION_FAILED_CHECK_QUALITY_FACTOR": config.FAILED_CHECK_QUALITY_FACTOR,
        "DECISION_MINIMUM_AGREEING_CONTRIBUTION": config.MINIMUM_AGREEING_CONTRIBUTION,
        "DECISION_HIGH_MINIMUM_GROUPS": config.HIGH_CONFIDENCE_MINIMUM_GROUPS,
        "DECISION_LOW_MAXIMUM_GROUPS": config.LOW_CONFIDENCE_MAXIMUM_GROUPS,
        "DECISION_HIGH_CONFLICT_SHARE": config.HIGH_CONFLICT_SHARE, "DECISION_LOW_CONFLICT_SHARE": config.LOW_CONFLICT_SHARE,
        "DECISION_GOOD_MEAN_QUALITY": config.GOOD_MEAN_QUALITY, "DECISION_POOR_MEAN_QUALITY": config.POOR_MEAN_QUALITY,
        "DECISION_WIDE_MARGIN_POINTS": config.WIDE_MARGIN_POINTS, "DECISION_NARROW_MARGIN_POINTS": config.NARROW_MARGIN_POINTS,
        "DECISION_HIGH_MINIMUM_POINTS": config.HIGH_CONFIDENCE_MINIMUM_POINTS,
        "DECISION_LOW_MAXIMUM_POINTS": config.LOW_CONFIDENCE_MAXIMUM_POINTS,
        "DECISION_TAKER_MINIMUM_POINTS": config.TAKER_BAND_MINIMUM_POINTS}
    for name, value in pairs.items():
        assert getattr(help_module, name) == value, name
    assert all(limit == config.FRESHNESS_LIMIT_HOURS[OPEN_INTEREST] for limit in config.FRESHNESS_LIMIT_HOURS.values())
    assert config.DEFAULT_FRESHNESS_LIMIT_HOURS == 6.0 and config.OPEN_INTEREST_SHARE_FOR_FULL_STRENGTH == 0.25


def test_group_contribution_helper_matches_the_engine_and_the_independence_examples():
    def items(count, group, quality, strength):
        return [(EvidenceItem(f"i{index}", "source", group, "price_level", "text", NOW, strength, quality, price=1.0), strength)
                for index in range(count)]
    quality, value = help_module.DECISION_ITEM_QUALITY, help_module.DECISION_ITEM_VALUE
    one = group_contribution(items(1, OPEN_INTEREST, quality, value))
    two = group_contribution(items(2, OPEN_INTEREST, quality, value))
    assert one == pytest.approx(group_contribution_value([(quality, value)])) == pytest.approx(0.48)
    assert two == pytest.approx(one) and help_module._DECISION_TWO_ITEMS_ONE_GROUP == pytest.approx(0.48)
    assert help_module._DECISION_TWO_GROUPS == pytest.approx(0.96)
    example = case_of("decision", "Two items from one input group").example
    assert "0.48" in example and "the same as one item alone" in example
    assert "0.48 + 0.48 = 0.96" in case_of("decision", "Items from different input groups").example


def test_freshness_example_matches_the_engine():
    for age, expected in ((help_module.DECISION_STALE_AGE_HOURS, 0.3), (help_module.DECISION_VERY_STALE_AGE_HOURS, 0.06)):
        item = level("stale", OPEN_INTEREST, 70_000.0, help_module.DECISION_ITEM_QUALITY, 1.0, age_hours=age)
        engine_quality = apply_freshness([item], NOW)[0].quality
        assert engine_quality == pytest.approx(quality_after_age(help_module.DECISION_ITEM_QUALITY, age)) == pytest.approx(expected)
    fresh = level("fresh", OPEN_INTEREST, 70_000.0, 0.6, 1.0, age_hours=3.0)
    assert apply_freshness([fresh], NOW)[0].quality == 0.6 == quality_after_age(0.6, 3.0)       # exactly at the limit: fresh
    text = case_of("decision", "An item is older than its freshness limit").example
    assert "0.30" in text and "0.06" in text and "5 hours" in text and "13 hours" in text


def test_scenario_example_matches_the_engine():
    report = build_decision_report("BTC", NOW, SPOT, example_items())
    inside, break_up = report.scenario(INSIDE), report.scenario(BREAK_UP)
    assert report.top_scenario == INSIDE
    assert inside.evidence_score == pytest.approx(help_module._DECISION_INSIDE_SCORE) == pytest.approx(54.545, abs=0.001)
    assert break_up.evidence_score == help_module._DECISION_BREAK_UP_SCORE == 0.0
    assert inside.support == pytest.approx(1.2) and break_up.opposition == pytest.approx(0.6)
    assert inside.confidence == "medium" and help_module._DECISION_MEDIUM_POINTS == 3
    assert "+3 points in total -> medium" in inside.confidence_reasons[-1]
    assert evidence_score(1.2, 0.0) == pytest.approx(inside.evidence_score)
    example = case_of("decision", "Two groups agree, wide margin").example
    assert "54.5" in example and "1.20" in example and "+3: medium" in example and "score 0.0" in example


def engine_label(example, score=50.0):
    """The engine's own label and points for the five inputs of a help example (``scenario_engine._confidence``)."""
    groups, conflict, quality, stale, margin = example
    stale_reasons = ("stale: 4.0 h old (limit 3 h), quality x0.5",) if stale else ()
    supporting = EvidenceItem("support", "source", OPEN_INTEREST, "price_level", "text", NOW, 1.0, quality, price=1.0,
                              quality_reasons=stale_reasons)
    entry = {"key": INSIDE, "contributions": {f"group{index}": 0.5 for index in range(groups)},
             "item_support": 1.0 - conflict, "item_opposition": conflict, "supporting": ["support"],
             "score": score}
    other = {"key": BREAK_UP, "score": score - margin}
    label, reasons = _confidence(entry, [entry, other], {"support": supporting}, {INSIDE: "inside", BREAK_UP: "up"})
    points = int(reasons[-1].split(" points")[0])
    return label, points


@pytest.mark.parametrize("name", ["HIGH", "CAPPED", "CONFLICT", "ONE_GROUP"])
def test_confidence_examples_match_the_engine(name):
    example = getattr(help_module, f"DECISION_{name}_EXAMPLE")
    engine = engine_label(example)
    assert engine == (confidence_label(example[0], confidence_points(*example), 50.0, example[3]), confidence_points(*example))


def test_confidence_example_labels_in_the_text():
    expected = {"Three groups agree": ("high", 3), "A supporting item is stale": ("medium", 2),
                "The evidence conflicts": ("medium", 1), "Only one input group supports": ("low", 2)}
    for start, (label, points) in expected.items():
        example = case_of("decision", start).example
        assert example.endswith(f"{label}."), (start, example)
        assert f"{points:+d}" in example, (start, example)
    assert engine_label(help_module.DECISION_HIGH_EXAMPLE) == ("high", 3)
    assert engine_label(help_module.DECISION_CAPPED_EXAMPLE) == ("medium", 2)
    assert "+1, +1, 0, -1, +1 = +2" in case_of("decision", "A supporting item is stale").example
    assert "+1, -1, +1, 0, 0 = +1" in case_of("decision", "The evidence conflicts").example
    assert "high is still possible" in case_of("decision", "The evidence conflicts").reading
    assert engine_label(help_module.DECISION_CONFLICT_EXAMPLE) == ("medium", 1)
    assert engine_label(help_module.DECISION_ONE_GROUP_EXAMPLE) == ("low", 2)
    assert confidence_label(3, 2, 0.0, 0) == "low"          # a score of 0 is low whatever else holds


def test_price_map_examples_match_the_engine():
    window = help_module.zoom_window(SPOT, config.MAXIMUM_LEVEL_DISTANCE_SHARE)
    assert tuple(round(value) for value in window) == (59_500, 80_500)
    one_sided = build_price_map([level("put", OPEN_INTEREST, 66_000.0, 0.6, 1.0)], SPOT)
    assert one_sided.support is not None and one_sided.resistance is None
    report = build_decision_report("BTC", NOW, SPOT, [level("put", OPEN_INTEREST, 66_000.0, 0.6, 1.0)])
    assert {scenario.key for scenario in report.scenarios} == {INSIDE, BREAK_DOWN}
    assert report.scenario(INSIDE).title.startswith("stays above 66,000")
    assert report.scenario(BREAK_DOWN).title.startswith("breaks below 66,000")
    example = case_of("decision", "One side is unmapped").example
    assert "59,500 to 80,500" in example and "66,000" in example
    empty = build_decision_report("BTC", NOW, SPOT, [level("far", OPEN_INTEREST, 120_000.0, 0.6, 1.0)])
    assert empty.scenarios == [] and empty.top_scenario is None and empty.confidence == "low"


def test_weak_item_example_matches_the_price_map():
    hvl = level("hvl", OPEN_INTEREST, 66_000.0, help_module.DECISION_HVL_QUALITY, help_module.DECISION_HVL_STRENGTH)
    flip = level("flip", OPEN_INTEREST, 66_100.0, help_module.DECISION_WEAK_FLIP_QUALITY, help_module.DECISION_WEAK_FLIP_STRENGTH)
    alone = build_price_map([hvl], SPOT).levels[0]
    together = build_price_map([hvl, flip], SPOT).levels[0]
    assert alone.weight == pytest.approx(help_module._DECISION_HVL_WEIGHT) == pytest.approx(0.35) and alone.meaningful
    assert together.weight == pytest.approx(help_module._DECISION_WEAKENED_WEIGHT) == pytest.approx(0.19, abs=0.005)
    assert not together.meaningful and together.weight < config.MINIMUM_LEVEL_WEIGHT
    text = case_of("decision", "A weak item of the same group").example
    assert "0.35" in text and "0.19" in text


def test_failed_flow_lane_example_matches_the_extractor():
    items = {item.id: item for item in extractors.dealer_evidence(dealer_levels(gate_passed=False), open_interest_row(share=0.25), NOW)}
    wall = items["dealer.27NOV26.flow.put_wall"]
    assert wall.quality == pytest.approx(help_module._DECISION_FLOW_FAILED_QUALITY) == pytest.approx(0.12)
    assert items["dealer.27NOV26.flow.put_wall"].input_group == TRADES
    assert items["dealer.27NOV26.open_interest.put_wall"].quality == pytest.approx(0.6)
    alone = build_price_map([wall], 65_000.0).levels[0]
    assert not alone.meaningful and alone.weight == pytest.approx(0.12)
    assert "0.12" in case_of("decision", "The flow-based dealer lane failed").example
    assert "0.12" in case_of("decision", "A failed flow-based wall alone").example


def test_taker_ratio_minimum_points_example_matches_the_extractor():
    points = [(NOW - timedelta(hours=hours + 2), 1.0 + hours % 3 * 0.01)
              for hours in range(help_module.DECISION_TAKER_POINTS_FOUND)]
    item, reason = extractors.taker_ratio_evidence("BTCUSDT", points, NOW)
    assert item is None and f"only {help_module.DECISION_TAKER_POINTS_FOUND} rolling points" in reason
    assert f"at least {help_module.DECISION_TAKER_MINIMUM_POINTS} needed" in reason
    assert "240 hourly points" in case_of("decision", "A source is not computed").example


def test_help_states_the_documented_limits():
    text = " ".join(" ".join(limit for limit in topic.limits) for topic in SECTION_HELP["decision"].topics).lower()
    for phrase in ("not a forecast", "not calibrated", "probability", "quiet market", "inside", "not independent"):
        assert phrase in text, phrase
    summary = SECTION_HELP["decision"].summary.lower()
    assert "not a forecast" in summary and "not an order" in summary and "price target" in summary
