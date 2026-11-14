"""Edge cases found in review: one-sided maps, a band at the spot, JSON-safe details, replay windows, non-finite totals."""

import json
from datetime import timedelta

import pytest

from coding.core.analytics.decision.decision_report import BREAK_DOWN, INSIDE, DecisionReport
from coding.core.analytics.decision.evidence import EvidenceItem
from coding.core.analytics.decision.scenario_engine import build_decision_report
from coding.service.decision import evidence_extractors as extractors
from tests.unit.decision.evidence_builders import NOW, SPOT, directional, level, regime
from tests.unit.decision.test_evidence_extractors import by_id, dealer_levels, open_interest_row


def report_of(items):
    return build_decision_report("BTC", NOW, SPOT, items)


def test_on_a_one_sided_map_a_lean_toward_the_unmapped_side_does_not_oppose_inside():
    # support only: "stays above 95,000"; bullish points to the unmapped upside, so it does not oppose inside
    bullish = report_of([level("support", "g1", 95_000), directional("bull", "g2", "bullish", quality=1.0)])
    assert "g2" not in bullish.scenario(INSIDE).group_contributions
    assert bullish.scenario(INSIDE).evidence_score == pytest.approx(100 * 0.5 / 1.5)        # the support alone: 0.5
    # bearish still opposes inside (-0.5 x 1 x 1) and supports the break down (+1)
    bearish = report_of([level("support", "g1", 95_000), directional("bear", "g2", "bearish", quality=1.0)])
    assert bearish.scenario(INSIDE).group_contributions["g2"] == pytest.approx(-0.5)
    assert bearish.scenario(BREAK_DOWN).group_contributions["g2"] == pytest.approx(1.0)
    # expansion opposes a one-sided inside only half: -0.5 x strength 1 x quality 0.5 = -0.25
    expansion = report_of([level("support", "g1", 95_000), regime("wild", "g2", "expansion")])
    assert expansion.scenario(INSIDE).group_contributions["g2"] == pytest.approx(-0.25)


def test_a_band_containing_the_spot_is_neither_support_nor_resistance():
    report = report_of([level("put_side", "g1", 99_600), level("call_side", "g2", 100_400), level("support", "g1", 95_000),
                        level("resistance", "g2", 105_000)])
    assert [band.evidence_ids for band in report.price_map.at_spot] == [["call_side", "put_side"]]
    assert report.price_map.support.price == 95_000 and report.price_map.resistance.price == 105_000
    assert any(item.text.startswith("The spot is inside a mapped band (99,600 to 100,400") for item in report.decisions)
    assert DecisionReport.from_dict(json.loads(json.dumps(report.to_dict()))) == report


def test_details_must_be_json_safe():
    with pytest.raises(ValueError, match="JSON"):
        EvidenceItem("x", "s", "g", "directional", "t", NOW, 1.0, 1.0, direction="neutral", details={"z": float("nan")})
    with pytest.raises(ValueError, match="JSON"):
        EvidenceItem("x", "s", "g", "directional", "t", NOW, 1.0, 1.0, direction="neutral", details={"when": NOW})


def test_taker_ratio_ignores_a_window_that_has_not_closed_by_the_clock():
    # replay at 12:30: the point of 12:00 covers the hour up to 13:00, which has not closed yet
    replay = NOW + timedelta(minutes=30)
    points = [(NOW - timedelta(hours=offset), 1.0 + 0.01 * (offset % 5)) for offset in range(300, -1, -1)]
    item, _ = extractors.taker_ratio_evidence("BTCUSDT", points, replay)
    assert item.as_of == NOW <= replay
    assert item.details["points"] == 300                       # 301 points given, the unclosed 12:00 one left out


def test_a_non_finite_total_gex_gives_no_regime_item():
    items = by_id(extractors.dealer_evidence(dealer_levels(total_open_interest_gex=float("nan")), open_interest_row(), NOW))
    assert "dealer.27NOV26.open_interest.total_gex_sign" not in items
