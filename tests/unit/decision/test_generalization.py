"""A new evidence source needs no change in the report builder: it only has to produce EvidenceItem values."""

from datetime import timedelta

from coding.core.analytics.decision.evidence import PRICE_LEVEL, EvidenceItem
from coding.core.analytics.decision.evidence_report import build_decision_report
from tests.unit.decision.evidence_builders import NOW, SPOT, directional, level


def baseline():
    """Support (g1), resistance (g2), balanced flow (g3), quality 0.5 each."""
    return [level("support", "g1", 95_000), level("resistance", "g2", 105_000), directional("balanced", "g3", "neutral")]


def report_of(items):
    return build_decision_report("BTC", NOW, SPOT, items)


def test_a_brand_new_level_source_moves_the_map():
    new_level = EvidenceItem(
        id="volume_profile.node", source="Hypothetical volume profile", input_group="exchange_volume_profile",
        kind=PRICE_LEVEL, statement="A high-volume node; it does not say the price will stop there.", as_of=NOW,
        strength=1.0, quality=0.6, price=102_000)
    report = report_of(baseline() + [new_level])
    # 102,000 weighs 0.6 >= 0.3 and is nearer than 105,000, so it becomes the resistance
    assert report.price_map.resistance.price == 102_000
    assert report.price_map.next_above.price == 105_000
    assert any(item.text.startswith("Reference range: 95,000 to 102,000") for item in report.decisions)
    assert "volume_profile.node" in [item.id for item in report.evidence]


def test_a_brand_new_directional_source_appears_in_the_evidence_with_its_caveats_and_changes_no_level():
    new_source = EvidenceItem(
        id="exchange_outflow.net", source="Hypothetical exchange outflow (on-chain)", input_group="onchain_exchange_flows",
        kind="directional", statement="Coins left exchanges on net; it does not say who bought them.", as_of=NOW - timedelta(hours=8),
        strength=1.0, quality=0.8, direction="bullish", horizon_days=7.0)
    before, after = report_of(baseline()), report_of(baseline() + [new_source])
    assert after.price_map.to_dict() == before.price_map.to_dict()            # directional items do not shape the map
    assert "exchange_outflow.net" in [item.id for item in after.evidence]
    assert any("Hypothetical exchange outflow (on-chain): stale" in item.text for item in after.decisions)


def test_removing_a_source_removes_only_its_levels():
    full = report_of(baseline())
    without_resistance = report_of([baseline()[0], baseline()[2]])
    assert full.price_map.resistance.price == 105_000 and without_resistance.price_map.resistance is None
    assert any(item.kind == "unmapped" and "upside is unmapped" in item.text for item in without_resistance.decisions)
    assert report_of([]).price_map.support is None
