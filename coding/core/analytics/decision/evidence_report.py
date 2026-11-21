"""
The evidence report: evidence in, a ``DecisionReport`` out. Pure and deterministic (same inputs, same JSON).

Steps:

1. Freshness: an item older than its input group's freshness limit loses quality (``STALE_QUALITY_FACTOR``, very stale
   ``VERY_STALE_QUALITY_FACTOR``) with the reason stated. An item newer than ``as_of`` is refused (no lookahead).
2. Price map (``price_map.build_price_map``): the nearest meaningful support below and resistance above the spot.
3. Reference lines: the range between support and resistance, a level for a hedge, bands the spot is inside, what is unmapped and
   every data caveat. Never orders or targets.

There is no scoring of scenarios and no confidence label (removed on 2026-11-21: never calibrated against outcomes).
"""

from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence

from coding.core.analytics.decision import decision_config as config
from coding.core.analytics.decision.decision_report import DecisionItem, DecisionReport
from coding.core.analytics.decision.evidence import EvidenceItem, sorted_evidence
from coding.core.analytics.decision.price_map import PriceMap, build_price_map

STALE_REASON_PREFIX = "stale"       # every freshness reason starts with it ("stale: ..." or "stale (very): ...")


def _price(value: float) -> str:
    return f"{value:,.0f}"


def _percent_from(value: float, spot: float) -> str:
    return f"{abs(value - spot) / spot:.1%}"


def apply_freshness(items: Iterable[EvidenceItem], as_of: datetime) -> List[EvidenceItem]:
    """
    The items with their quality lowered when stale (reason added), id order.

    Raises:
        ValueError: for a naive ``as_of``, an item newer than ``as_of`` (lookahead) or a duplicate id.
    """
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware (UTC)")
    result = []
    for item in items:
        if item.as_of > as_of:
            raise ValueError(f"{item.id} is from {item.as_of.isoformat()}, after the report time {as_of.isoformat()} "
                             "(lookahead)")
        age_hours = (as_of - item.as_of).total_seconds() / 3600
        limit = config.FRESHNESS_LIMIT_HOURS.get(item.input_group, config.DEFAULT_FRESHNESS_LIMIT_HOURS)
        if age_hours > limit * config.VERY_STALE_MULTIPLE:
            item = item.with_quality(item.quality * config.VERY_STALE_QUALITY_FACTOR,
                                     f"{STALE_REASON_PREFIX} (very): {age_hours:.1f} h old (limit {limit:g} h), "
                                     f"quality x{config.VERY_STALE_QUALITY_FACTOR:g}")
        elif age_hours > limit:
            item = item.with_quality(item.quality * config.STALE_QUALITY_FACTOR,
                                     f"{STALE_REASON_PREFIX}: {age_hours:.1f} h old (limit {limit:g} h), "
                                     f"quality x{config.STALE_QUALITY_FACTOR:g}")
        result.append(item)
    return sorted_evidence(result)


def _decisions(price_map: PriceMap, items: List[EvidenceItem], not_computed: Sequence[str],
               caveats: Sequence[str] = ()) -> List[DecisionItem]:
    """The plain-language reference lines."""
    spot = price_map.spot
    distance = f"{config.MAXIMUM_LEVEL_DISTANCE_SHARE:.0%}"
    support, resistance = price_map.support, price_map.resistance
    lines = [DecisionItem("notice", "These are reference levels and conditions, not orders, not price targets and not a "
                                    "ranking of scenarios.")]
    if support is not None and resistance is not None:
        lines.append(DecisionItem("reference_range", (
            f"Reference range: {_price(support.price)} to {_price(resistance.price)} "
            f"({_percent_from(support.price, spot)} below and {_percent_from(resistance.price, spot)} above the spot "
            f"{_price(spot)}; support from {', '.join(support.input_groups)}, resistance from "
            f"{', '.join(resistance.input_groups)}).")))
    if support is not None:
        beyond = (f"; next mapped level below: {_price(price_map.next_below.price)} "
                  f"({_percent_from(price_map.next_below.price, spot)} under the spot)" if price_map.next_below is not None
                  else f"; no further mapped level below within {distance}")
        lines.append(DecisionItem("reference_hedge", (
            f"Reference level for a hedge: {_price(support.price)} (nearest mapped level below, "
            f"{_percent_from(support.price, spot)} under the spot){beyond}.")))
    for band in price_map.at_spot:
        lines.append(DecisionItem("watch", (
            f"The spot is inside a mapped band ({_price(band.low)} to {_price(band.high)}, from "
            f"{', '.join(band.input_groups)}): the price is at a mapped level now, so it bounds neither side.")))
    if resistance is None:
        lines.append(DecisionItem("unmapped", f"No meaningful level above within {distance} of the spot: the upside is unmapped."))
    if support is None:
        lines.append(DecisionItem("unmapped", f"No meaningful level below within {distance} of the spot: the downside is unmapped."))
    item_caveats = []
    for item in items:
        for reason in item.quality_reasons:
            item_caveats.append(f"{item.source}: {reason}")
    for text in dict.fromkeys(item_caveats):
        lines.append(DecisionItem("data_caveat", f"Data caveat: {text}."))
    for text in caveats:
        lines.append(DecisionItem("data_caveat", f"Data caveat: {text}."))
    for text in not_computed:
        lines.append(DecisionItem("data_caveat", f"Not computed: {text}."))
    return lines


def build_decision_report(currency: str, as_of: datetime, reference_price: float, evidence: Iterable[EvidenceItem],
                          not_computed: Sequence[str] = (), context: Optional[Dict[str, Any]] = None,
                          caveats: Sequence[str] = ()) -> DecisionReport:
    """
    The report of one set of evidence.

    Args:
        currency: Label of the underlying.
        as_of: The instant described (UTC-aware); every item must be at or before it.
        reference_price: Spot the map is built around (positive).
        evidence: The items, any order (ids must be unique).
        not_computed: Sources that could not be computed, with why (shown as caveats).
        context: Extra numbers for the reader (must be JSON-serialisable).
        caveats: Warnings about the inputs that are not tied to one item (e.g. an old spot); shown as data caveats and
            kept in ``context["caveats"]``.

    Raises:
        ValueError: naive ``as_of``, lookahead, duplicate ids or a non-positive reference price.
    """
    items = apply_freshness(evidence, as_of)
    price_map = build_price_map(items, reference_price)
    return DecisionReport(
        currency=currency, as_of=as_of, reference_price=reference_price, evidence=items, price_map=price_map,
        decisions=_decisions(price_map, items, not_computed, caveats), not_computed=list(not_computed),
        context={**(context or {}), "caveats": list(caveats)})
