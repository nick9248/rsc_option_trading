"""The Decision tab help rules (what is meaningful, the band price, the taker band) against the evidence report code."""

import statistics
from datetime import timedelta

import pytest

from coding.core.analytics.decision import decision_config as config
from coding.core.analytics.decision.evidence_report import apply_freshness, build_decision_report
from coding.core.analytics.decision.price_map import build_price_map
from coding.core.analytics.report import how_to_read as help_module
from coding.service.decision import evidence_extractors as extractors
from tests.unit.decision.evidence_builders import NOW, level
from tests.unit.report.test_how_to_read import all_text, case_of, topic_of

SPOT = help_module.EXAMPLE_SPOT
OPEN_INTEREST = "deribit_open_interest"
TRADES = "deribit_trades"


def term_of(topic_title, term_name):
    """The meaning of one term of a Decision topic."""
    return next(term.meaning for term in topic_of("decision", topic_title).terms if term.term == term_name)


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


def test_the_only_meaningful_band_containing_the_spot_leaves_both_sides_unmapped():
    report = build_decision_report("BTC", NOW, SPOT, [level("here", OPEN_INTEREST, SPOT, 0.6, 1.0)])
    assert report.price_map.at_spot and report.price_map.support is None and report.price_map.resistance is None
    assert [item.kind for item in report.decisions].count("unmapped") == 2
    assert "both sides are unmapped and there is no reference range" in case_of("decision", "The spot is inside a band").reading


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


def test_no_items_without_an_error_and_the_old_spot_is_described():
    assert "neither in the evidence table nor under 'Sources not computed'" in case_of(
        "decision", "A source produces no items").reading
    assert "current price" in case_of("decision", "The spot is old").example
    assert not any("today's" in text for _, text in all_text())
