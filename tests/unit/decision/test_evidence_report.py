"""The evidence report: freshness, the price map, the reference lines, determinism and the JSON round trip. No scoring."""

import json
import random
from datetime import timedelta

import pytest

from coding.core.analytics.decision.decision_report import DecisionReport
from coding.core.analytics.decision.evidence import EvidenceItem
from coding.core.analytics.decision.evidence_report import apply_freshness, build_decision_report
from tests.unit.decision.evidence_builders import NOW, SPOT, directional, level, regime


def report_of(items, not_computed=()):
    return build_decision_report("BTC", NOW, SPOT, items, not_computed)


def two_sided_map():
    return [level("support", "g1", 95_000), level("resistance", "g2", 105_000), level("below", "g1", 90_000),
            level("above", "g2", 110_000)]


def test_the_report_has_no_scenarios_scores_or_confidence():
    data = report_of(two_sided_map()).to_dict()
    for removed in ("scenarios", "top_scenario", "confidence", "confidence_reasons", "what_would_change", "score_scale"):
        assert removed not in data
    assert "not a ranking of scenarios" in data["disclaimer"]


def test_reference_lines_are_levels_and_conditions_never_targets():
    report = report_of(two_sided_map())
    texts = [item.text for item in report.decisions]
    assert texts[0] == ("These are reference levels and conditions, not orders, not price targets and not a ranking of scenarios.")
    assert ("Reference range: 95,000 to 105,000 (5.0% below and 5.0% above the spot 100,000; support from g1, "
            "resistance from g2).") in texts
    assert ("Reference level for a hedge: 95,000 (nearest mapped level below, 5.0% under the spot); next mapped level "
            "below: 90,000 (10.0% under the spot).") in texts
    assert not any(item.kind in ("summary",) for item in report.decisions)


def test_a_one_sided_map_says_which_side_is_unmapped_and_invents_no_level():
    report = report_of([level("support", "g1", 95_000), level("far", "g2", 120_000)])
    texts = [item.text for item in report.decisions]
    assert "No meaningful level above within 15% of the spot: the upside is unmapped." in texts
    assert not any(item.kind == "reference_range" for item in report.decisions)
    assert report.price_map.left_out == ["far"]


def test_no_level_at_all_lists_both_sides_as_unmapped_and_the_not_computed_sources():
    report = report_of([directional("bull", "g1", "bullish")], not_computed=["dealer GEX: no chain"])
    kinds = [item.kind for item in report.decisions]
    assert kinds.count("unmapped") == 2
    assert "Not computed: dealer GEX: no chain." in [item.text for item in report.decisions]


def test_a_band_containing_the_spot_is_neither_support_nor_resistance():
    report = report_of([level("put_side", "g1", 99_600), level("call_side", "g2", 100_400), level("support", "g1", 95_000),
                        level("resistance", "g2", 105_000)])
    assert [band.evidence_ids for band in report.price_map.at_spot] == [["call_side", "put_side"]]
    assert report.price_map.support.price == 95_000 and report.price_map.resistance.price == 105_000
    assert any(item.text.startswith("The spot is inside a mapped band (99,600 to 100,400") for item in report.decisions)
    assert DecisionReport.from_dict(json.loads(json.dumps(report.to_dict()))) == report


def test_stale_data_loses_quality_with_the_reason_stated():
    # deribit_trades has a 3 h freshness limit: 5 h old -> x0.5, 13 h old (more than 4 x 3) -> x0.1
    fresh, stale, very_stale = (level(name, "deribit_trades", 95_000, age_hours=age) for name, age in
                                (("fresh", 0), ("stale", 5), ("very_stale", 13)))
    assessed = {item.id: item for item in apply_freshness([fresh, stale, very_stale], NOW)}
    assert assessed["fresh"].quality == 0.5 and assessed["fresh"].quality_reasons == ()
    assert assessed["stale"].quality == pytest.approx(0.25)
    assert assessed["stale"].quality_reasons == ("stale: 5.0 h old (limit 3 h), quality x0.5",)
    assert assessed["very_stale"].quality == pytest.approx(0.05)
    assert assessed["very_stale"].quality_reasons[0].startswith("stale (very): 13.0 h old")


def test_a_stale_item_shows_up_as_a_data_caveat():
    report = report_of([level("old", "deribit_trades", 95_000, age_hours=5)])
    assert any(item.kind == "data_caveat" and "stale: 5.0 h old (limit 3 h)" in item.text for item in report.decisions)


def test_evidence_newer_than_the_report_is_refused_and_times_must_be_aware():
    with pytest.raises(ValueError, match="lookahead"):
        report_of([level("future", "g1", 95_000, age_hours=-1)])
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


def test_same_input_gives_identical_json_whatever_the_order():
    items = two_sided_map() + [regime("wild", "g5", "expansion"), directional("lean", "g3", "bullish")]
    first = json.dumps(report_of(items).to_dict(), sort_keys=True)
    shuffled = list(items)
    random.Random(7).shuffle(shuffled)
    assert json.dumps(report_of(shuffled).to_dict(), sort_keys=True) == first


def test_the_report_survives_a_json_round_trip():
    items = two_sided_map() + [directional("lean", "g4", "bearish", horizon_days=2.5),
                               level("stale", "deribit_trades", 104_800, age_hours=4)]
    report = report_of(items, not_computed=["futures taker ratio: none"])
    text = json.dumps(report.to_dict(), allow_nan=False)
    assert DecisionReport.from_dict(json.loads(text)) == report


def test_items_exactly_at_the_report_time_are_accepted_and_the_limit_itself_is_fresh():
    item = level("now", "g1", 95_000)
    assert report_of([item]).evidence[0].as_of == NOW
    old = apply_freshness([level("old", "g1", 95_000, age_hours=6)], NOW)[0]
    assert old.quality_reasons == () and old.as_of == NOW - timedelta(hours=6)          # at the limit: fresh
