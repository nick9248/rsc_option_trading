"""The Decision tab help rules (what is meaningful, how an item counts, the score, the taker band) against the engine."""

import statistics
from datetime import timedelta

import pytest

from coding.core.analytics.decision import decision_config as config
from coding.core.analytics.decision.decision_report import BREAK_DOWN, BREAK_UP, INSIDE, SCENARIO_ORDER
from coding.core.analytics.decision.price_map import build_price_map
from coding.core.analytics.decision.scenario_engine import _confidence, _variant, apply_freshness, build_decision_report, stances
from coding.core.analytics.report import how_to_read as help_module
from coding.core.analytics.report.how_to_read import evidence_score
from coding.service.decision import evidence_extractors as extractors
from tests.unit.decision.evidence_builders import NOW, directional, level, regime
from tests.unit.report.test_decision_help import example_items
from tests.unit.report.test_how_to_read import all_text, case_of, topic_of

SPOT = help_module.EXAMPLE_SPOT
OPEN_INTEREST = "deribit_open_interest"
TRADES = "deribit_trades"
BINANCE = "binance_perpetual_candles"


def term_of(topic_title, term_name):
    """The meaning of one term of a Decision topic."""
    return next(term.meaning for term in topic_of("decision", topic_title).terms if term.term == term_name)


def both_bounds_map():
    return build_price_map(example_items(), SPOT)       # put wall 66,000 and call wall 74,000, both meaningful


def support_only_map():
    return build_price_map([level("put", OPEN_INTEREST, 66_000.0, 0.6, 1.0)], SPOT)


KEYS = [INSIDE, BREAK_UP, BREAK_DOWN]


def test_meaningful_examples_match_the_engine():
    assert help_module.DECISION_FLOW_STRIKE_QUALITY == config.FLOW_STRIKE_LEVEL_QUALITY
    assert help_module.DECISION_DEALER_FLIP_STRENGTH == config.DEALER_FLIP_STRENGTH
    assert help_module.DECISION_WALL_QUALITY == config.OPEN_INTEREST_DEALER_LEVEL_QUALITY
    assert help_module.DECISION_HVL_STRENGTH == config.MENTHORQ_FLIP_STRENGTH
    assert help_module.DECISION_HVL_QUALITY == config.MENTHORQ_LEVEL_QUALITY
    top_strike = level("strike", TRADES, 66_000.0, config.FLOW_STRIKE_LEVEL_QUALITY, 1.0)
    flip = level("flip", OPEN_INTEREST, 66_000.0, config.OPEN_INTEREST_DEALER_LEVEL_QUALITY, config.DEALER_FLIP_STRENGTH)
    failed_wall = level("wall", TRADES, 66_000.0, config.FLOW_DEALER_LEVEL_QUALITY * config.FAILED_CHECK_QUALITY_FACTOR, 1.0)
    stale_hvl = apply_freshness([level("hvl", OPEN_INTEREST, 66_000.0, config.MENTHORQ_LEVEL_QUALITY,
                                       config.MENTHORQ_FLIP_STRENGTH, age_hours=5.0)], NOW)[0]
    engine = {name: build_price_map([item], SPOT).levels[0] for name, item in
              (("strike", top_strike), ("flip", flip), ("wall", failed_wall), ("hvl", stale_hvl))}
    assert [engine[name].meaningful for name in ("strike", "flip", "wall", "hvl")] == [True, True, False, False]
    assert engine["strike"].weight == pytest.approx(help_module._DECISION_FLOW_STRIKE_WEIGHT) == pytest.approx(0.35)
    assert engine["flip"].weight == pytest.approx(help_module._DECISION_FLIP_WEIGHT) == pytest.approx(0.30)
    assert engine["wall"].weight == pytest.approx(help_module._DECISION_FAILED_WALL_WEIGHT) == pytest.approx(0.12)
    assert engine["hvl"].weight == pytest.approx(help_module._DECISION_STALE_HVL_WEIGHT) == pytest.approx(0.175)
    text = term_of("The price map", "Meaningful")
    for expected in ("0.35", "0.30", "0.12", "0.175", "exactly 0.3 counts"):
        assert expected in text, expected
    assert "stays below that" not in text


def test_band_price_is_the_strength_times_quality_weighted_mean():
    low = level("low", OPEN_INTEREST, 66_000.0, 0.6, 1.0)
    high = level("high", TRADES, 66_500.0, 0.3, 1.0)
    band = build_price_map([low, high], SPOT).levels[0]
    assert band.price == pytest.approx((66_000.0 * 0.6 + 66_500.0 * 0.3) / 0.9)
    assert "weighted by strength times quality" in term_of("The price map", "Band price")


def test_the_only_meaningful_band_containing_the_spot_leaves_no_scenario():
    report = build_decision_report("BTC", NOW, SPOT, [level("here", OPEN_INTEREST, SPOT, 0.6, 1.0)])
    assert report.price_map.at_spot and report.price_map.support is None and report.price_map.resistance is None
    assert report.scenarios == [] and report.top_scenario is None
    assert "both sides are unmapped and there is no scenario" in case_of("decision", "The spot is inside a band").reading


def test_how_an_item_counts_matches_stances():
    two_sided, one_sided = both_bounds_map(), support_only_map()
    bullish = directional("b", BINANCE, "bullish", strength=1.0)
    bearish = directional("e", BINANCE, "bearish", strength=1.0)
    # a lean opposes inside at half strength, unless it points toward the unmapped side
    assert stances(bullish, KEYS, two_sided) == {INSIDE: -0.5, BREAK_UP: 1.0, BREAK_DOWN: -1.0}
    assert stances(bearish, KEYS, two_sided) == {INSIDE: -0.5, BREAK_UP: -1.0, BREAK_DOWN: 1.0}
    assert stances(bullish, [INSIDE, BREAK_DOWN], one_sided)[INSIDE] == 0.0         # the upside is unmapped
    assert stances(bearish, [INSIDE, BREAK_DOWN], one_sided)[INSIDE] == -0.5
    # a range-bound hint opposes each break at half strength
    assert stances(regime("r", OPEN_INTEREST, "range_bound"), KEYS, two_sided) == {INSIDE: 1.0, BREAK_UP: -0.5, BREAK_DOWN: -0.5}
    # an expansion hint supports each break at half strength; it opposes inside fully (two sides) or at half (one side)
    expansion = regime("x", OPEN_INTEREST, "expansion")
    assert stances(expansion, KEYS, two_sided) == {INSIDE: -1.0, BREAK_UP: 0.5, BREAK_DOWN: 0.5}
    assert stances(expansion, [INSIDE, BREAK_DOWN], one_sided) == {INSIDE: -0.5, BREAK_DOWN: 0.5}
    text = term_of("Scenarios, evidence score", "How an item counts")
    for expected in ("at half strength", "unless it points toward the unmapped side"):
        assert expected in text, expected


def test_score_description_and_the_cap_of_75_with_three_groups():
    assert evidence_score(3.0, 0.0) == pytest.approx(75.0) and config.GROUP_CONTRIBUTION_CAP == 1.0
    text = term_of("Scenarios, evidence score", "Evidence score")
    assert "net into one contribution" in text and "at most 75 today" in text and "(not below 0)" in text
    assert len(config.GROUP_WEIGHTS) == 3


def test_a_scenario_without_supporting_items_skips_the_quality_and_stale_criteria():
    report = build_decision_report("BTC", NOW, SPOT, example_items())
    break_up = report.scenario(BREAK_UP)
    assert break_up.supporting_evidence_ids == []
    assert not any("mean quality" in reason or "freshness limit" in reason for reason in break_up.confidence_reasons)
    assert "skips this criterion and the stale one" in term_of("Scenarios, evidence score", "Mean quality")


def test_tied_scores_are_ordered_and_a_tied_top_has_margin_zero():
    assert SCENARIO_ORDER == (INSIDE, BREAK_UP, BREAK_DOWN)
    items = {"support": level("support", OPEN_INTEREST, 66_000.0, 0.6, 1.0)}
    entry = {"key": INSIDE, "contributions": {"a": 0.5, "b": 0.5, "c": 0.5}, "item_support": 1.0, "item_opposition": 0.0,
             "supporting": ["support"], "score": 40.0}
    other = {"key": BREAK_UP, "score": 40.0}
    _, reasons = _confidence(entry, [entry, other], items, {INSIDE: "inside", BREAK_UP: "up"})
    assert "-1: leads the next scenario by only 0.0 points" in reasons
    assert "margin of 0" in case_of("decision", "Two scenarios have the same score").reading


def test_what_would_change_reverses_directional_and_removes_balanced_items_and_levels():
    assert _variant(directional("d", BINANCE, "bullish"))[0].direction == "bearish"
    assert _variant(regime("r", OPEN_INTEREST, "range_bound"))[0].regime == "expansion"
    assert _variant(directional("n", BINANCE, "neutral"))[0] is None
    assert _variant(level("l", OPEN_INTEREST, 66_000.0))[0] is None
    assert "reversed (bullish or bearish items, range hints) or removed (balanced items, levels)" in term_of(
        "Decisions, what would change", "What would change this")


def test_an_item_newer_than_the_report_time_raises():
    with pytest.raises(ValueError, match="lookahead"):
        build_decision_report("BTC", NOW, SPOT, [level("future", OPEN_INTEREST, 66_000.0, age_hours=-1.0)])
    assert "raise an error" in term_of("Evidence and input groups", "As of")


def taker_points(latest, others):
    """300 hourly points; the newest has the ratio ``latest``, the others cycle through ``others``."""
    return [(NOW - timedelta(hours=hours + 2), latest if hours == 0 else others[hours % len(others)]) for hours in range(300)]


def test_taker_band_uses_the_sample_standard_deviation_and_the_stated_strengths():
    others = [0.95, 1.0, 1.05, 1.0, 0.98]
    outside, _ = extractors.taker_ratio_evidence("BTCUSDT", taker_points(1.3, others), NOW)
    ratios = [ratio for _, ratio in taker_points(1.3, others)]
    z = (1.3 - statistics.mean(ratios)) / statistics.stdev(ratios)
    population_z = (1.3 - statistics.mean(ratios)) / statistics.pstdev(ratios)
    assert z > config.TAKER_BAND_STANDARD_DEVIATIONS and z != pytest.approx(population_z)
    assert outside.direction == "bullish" and outside.strength == pytest.approx(min(1.0, z / 3))
    inside_latest = statistics.mean(ratios) + 0.4 * statistics.stdev(ratios)
    inside, _ = extractors.taker_ratio_evidence("BTCUSDT", taker_points(inside_latest, others), NOW)
    inside_ratios = [ratio for _, ratio in taker_points(inside_latest, others)]
    inside_z = (inside_latest - statistics.mean(inside_ratios)) / statistics.stdev(inside_ratios)
    assert inside.direction == "neutral" and inside.strength == pytest.approx(1 - abs(inside_z))
    text = term_of("Evidence and input groups", "Taker band")
    for expected in ("sample standard deviation", "population standard deviation", "|z| / 3", "1 - |z|", "30-day mean"):
        assert expected in text, expected


def test_selection_and_priors_are_stated():
    assert config.FLOW_STRIKES_PER_SIDE == 2
    text = term_of("Evidence and input groups", "Selection")
    for expected in ("nearest monthly expiry and the one with the most open interest", "call resistance, put support and HVL",
                     "top 2 strikes", "24 hour taker ratio"):
        assert expected in text, expected
    assert "selected signals of the other tabs" in help_module.SECTION_HELP["decision"].summary
    limits = " ".join(topic_of("decision", "Evidence and input groups").limits)
    for expected in ("contrarian", "share the open interest"):
        assert expected in limits, expected


def test_no_items_without_an_error_and_the_reference_lines_are_described():
    assert "neither in the evidence table nor under 'Sources not computed'" in case_of(
        "decision", "A source produces no items").reading
    assert "still listed; they describe the map" in case_of("decision", "No scenario has net support").reading
    assert "current price" in case_of("decision", "The spot is old").example
    assert not any("today's" in text for _, text in all_text())
