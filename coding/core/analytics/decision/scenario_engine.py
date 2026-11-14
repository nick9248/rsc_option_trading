"""
The scenario engine: evidence in, a ``DecisionReport`` out. Pure and deterministic (same inputs, same JSON).

Steps:

1. Freshness: an item older than its input group's freshness limit loses quality (``STALE_QUALITY_FACTOR``, very stale
   ``VERY_STALE_QUALITY_FACTOR``) with the reason stated. An item newer than ``as_of`` is refused (no lookahead).
2. Price map (``price_map.build_price_map``): the nearest meaningful support below and resistance above the spot.
3. Scenarios, generated from the map, not from a fixed list: "stays inside" (needs at least one bound), "breaks above the
   resistance" (needs a resistance), "breaks below the support" (needs a support). An unmapped side is said so.
4. Each item gets a signed stance per scenario (rules below, factors in ``decision_config``), times its strength; the
   values are aggregated per input group (``independence``), and the scenario's
   net = support - CONFLICT_PENALTY x opposition, score = 100 x net / (net + SCORE_HALF_POINT): uncalibrated, never
   normalised across scenarios, not a probability.
5. Confidence label per scenario from: agreeing distinct input groups, conflict share, mean quality of the supporting items,
   staleness of the supporting items, and the margin to the next scenario.
6. "What would change this": every item is flipped (directional, regime) or removed (levels, magnets) one at a time and
   the whole report is recomputed; the items that change the top scenario are listed.
7. Decision-support lines (reference range, reference level for a hedge, what to watch, caveats). Never orders or targets.

Stance rules (before strength):
* price_level in the support band: inside +LEVEL_SUPPORTS_INSIDE, break down -LEVEL_OPPOSES_BREAK; in the resistance band
  the same with break up. Other levels only shape the map.
* directional bullish: break up +, break down -, inside -DIRECTIONAL_OPPOSES_INSIDE (bearish mirrored);
  neutral: inside +BALANCED_SUPPORTS_INSIDE.
* range_regime range_bound: inside +, each break -RANGE_BOUND_OPPOSES_BREAK; expansion: inside -, each break +half.
* magnet beyond the resistance: break up +; beyond the support: break down +; between: inside +half. Times proximity
  (1 - distance / reach, not below 0), so the nearer and (through strength) larger pool weighs more.
"""

from dataclasses import replace
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from coding.core.analytics.decision import decision_config as config
from coding.core.analytics.decision.decision_report import (
    BREAK_DOWN, BREAK_UP, HIGH, INSIDE, LOW, MEDIUM, SCENARIO_ORDER, ChangeTrigger, DecisionItem, DecisionReport,
    ScenarioAssessment)
from coding.core.analytics.decision.evidence import (
    BEARISH, BULLISH, DIRECTIONAL, EXPANSION, MAGNET, NEUTRAL, PRICE_LEVEL, RANGE_BOUND, RANGE_REGIME, EvidenceItem,
    sorted_evidence)
from coding.core.analytics.decision.independence import contributions_by_group
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


def _scenario_keys(price_map: PriceMap) -> List[str]:
    keys = []
    if price_map.support is not None or price_map.resistance is not None:
        keys.append(INSIDE)
    if price_map.resistance is not None:
        keys.append(BREAK_UP)
    if price_map.support is not None:
        keys.append(BREAK_DOWN)
    return keys


def stances(item: EvidenceItem, keys: Sequence[str], price_map: PriceMap) -> Dict[str, float]:
    """The signed value (stance x strength, x proximity for magnets) of one item for each scenario of ``keys`` (0 when silent)."""
    values = {key: 0.0 for key in keys}

    def add(key: str, amount: float) -> None:
        if key in values:
            values[key] += amount

    strength = item.strength
    if item.kind == PRICE_LEVEL:
        if price_map.support is not None and item.id in price_map.support.evidence_ids:
            add(INSIDE, config.LEVEL_SUPPORTS_INSIDE * strength)
            add(BREAK_DOWN, -config.LEVEL_OPPOSES_BREAK * strength)
        if price_map.resistance is not None and item.id in price_map.resistance.evidence_ids:
            add(INSIDE, config.LEVEL_SUPPORTS_INSIDE * strength)
            add(BREAK_UP, -config.LEVEL_OPPOSES_BREAK * strength)
    elif item.kind == DIRECTIONAL:
        if item.direction == NEUTRAL:
            add(INSIDE, config.BALANCED_SUPPORTS_INSIDE * strength)
        else:
            with_it, against_it = (BREAK_UP, BREAK_DOWN) if item.direction == BULLISH else (BREAK_DOWN, BREAK_UP)
            add(with_it, config.DIRECTIONAL_SUPPORTS_BREAK * strength)
            add(against_it, -config.DIRECTIONAL_OPPOSES_OTHER_BREAK * strength)
            # On a one-sided map "inside" is open toward the unmapped side, so a lean toward that side does not oppose it.
            toward_unmapped = ((item.direction == BULLISH and price_map.resistance is None)
                               or (item.direction == BEARISH and price_map.support is None))
            if not toward_unmapped:
                add(INSIDE, -config.DIRECTIONAL_OPPOSES_INSIDE * strength)
    elif item.kind == RANGE_REGIME:
        if item.regime == RANGE_BOUND:
            add(INSIDE, config.RANGE_BOUND_SUPPORTS_INSIDE * strength)
            add(BREAK_UP, -config.RANGE_BOUND_OPPOSES_BREAK * strength)
            add(BREAK_DOWN, -config.RANGE_BOUND_OPPOSES_BREAK * strength)
        elif item.regime == EXPANSION:
            both_bounds = price_map.support is not None and price_map.resistance is not None
            add(INSIDE, -(config.EXPANSION_OPPOSES_INSIDE if both_bounds
                          else config.ONE_SIDED_EXPANSION_OPPOSES_INSIDE) * strength)
            add(BREAK_UP, config.EXPANSION_SUPPORTS_BREAK * strength)
            add(BREAK_DOWN, config.EXPANSION_SUPPORTS_BREAK * strength)
    elif item.kind == MAGNET and item.price is not None:
        reach = config.MAGNET_REACH_SHARE * price_map.spot
        proximity = max(0.0, 1.0 - abs(item.price - price_map.spot) / reach)
        amount = strength * proximity
        if price_map.resistance is not None and item.price > price_map.resistance.high:
            add(BREAK_UP, config.MAGNET_SUPPORTS_BREAK * amount)
        elif price_map.support is not None and item.price < price_map.support.low:
            add(BREAK_DOWN, config.MAGNET_SUPPORTS_BREAK * amount)
        else:
            add(INSIDE, config.MAGNET_SUPPORTS_INSIDE * amount)
    return values


def _title(key: str, price_map: PriceMap) -> Tuple[str, Optional[float], Optional[float], Optional[float], Optional[float]]:
    """Title, lower, upper, level, next level of a scenario."""
    support, resistance = price_map.support, price_map.resistance
    distance = f"{config.MAXIMUM_LEVEL_DISTANCE_SHARE:.0%}"
    if key == INSIDE:
        if support is not None and resistance is not None:
            return (f"stays between {_price(support.price)} and {_price(resistance.price)}", support.price,
                    resistance.price, None, None)
        if support is not None:
            return (f"stays above {_price(support.price)} (no mapped level above within {distance}: the upside is "
                    "unmapped)", support.price, None, None, None)
        if resistance is None:
            raise ValueError("an inside scenario needs a bound")
        return (f"stays below {_price(resistance.price)} (no mapped level below within {distance}: the downside is "
                "unmapped)", None, resistance.price, None, None)
    if key == BREAK_UP:
        if resistance is None:
            raise ValueError("a break up needs a resistance")
        beyond = price_map.next_above
        tail = (f", next mapped level above {_price(beyond.price)}" if beyond is not None
                else f", no further mapped level above within {distance}")
        return f"breaks above {_price(resistance.price)}{tail}", None, None, resistance.price, beyond.price if beyond else None
    if support is None:
        raise ValueError("a break down needs a support")
    beyond = price_map.next_below
    tail = (f", next mapped level below {_price(beyond.price)}" if beyond is not None
            else f", no further mapped level below within {distance}")
    return f"breaks below {_price(support.price)}{tail}", None, None, support.price, beyond.price if beyond else None


def _is_stale(item: EvidenceItem) -> bool:
    return any(reason.startswith(STALE_REASON_PREFIX) for reason in item.quality_reasons)


def _score(items: List[EvidenceItem], price_map: PriceMap) -> List[Dict[str, Any]]:
    """The raw numbers of every scenario of the map, best first (ties in ``SCENARIO_ORDER``)."""
    keys = _scenario_keys(price_map)
    values_by_item = {item.id: stances(item, keys, price_map) for item in items}
    raw = []
    for key in keys:
        values = [(item, values_by_item[item.id][key]) for item in items if values_by_item[item.id][key] != 0.0]
        contributions = contributions_by_group(values)
        support = sum(value for value in contributions.values() if value > 0)
        opposition = sum(-value for value in contributions.values() if value < 0)
        net = max(0.0, support - config.CONFLICT_PENALTY * opposition)
        raw.append({
            "key": key, "contributions": contributions, "support": support, "opposition": opposition,
            # Conflict is judged per item (quality x |value|), because the group means above net it away within a group.
            "item_support": sum(item.quality * value for item, value in values if value > 0),
            "item_opposition": sum(item.quality * -value for item, value in values if value < 0),
            "score": 100.0 * net / (net + config.SCORE_HALF_POINT),
            "supporting": sorted(item.id for item, value in values if value > 0),
            "opposing": sorted(item.id for item, value in values if value < 0),
        })
    raw.sort(key=lambda entry: (-entry["score"], SCENARIO_ORDER.index(entry["key"])))
    return raw


def _top_key(ranked: List[Dict[str, Any]]) -> Optional[str]:
    return ranked[0]["key"] if ranked and ranked[0]["score"] > 0 else None


def _confidence(entry: Dict[str, Any], ranked: List[Dict[str, Any]], items_by_id: Dict[str, EvidenceItem],
                titles: Dict[str, str]) -> Tuple[str, List[str]]:
    """The label and its reasons (one per criterion; each adds +1, 0 or -1 point)."""
    points = 0
    reasons: List[str] = []
    agreeing = [group for group, value in entry["contributions"].items() if value > config.MINIMUM_AGREEING_CONTRIBUTION]
    count = len(agreeing)
    names = f" ({', '.join(agreeing)})" if agreeing else ""
    if count >= config.HIGH_CONFIDENCE_MINIMUM_GROUPS:
        points += 1
        reasons.append(f"+1: {count} distinct input groups agree{names}")
    elif count <= config.LOW_CONFIDENCE_MAXIMUM_GROUPS:
        points -= 1
        reasons.append(f"-1: only {count} distinct input group supports it{names}" if count == 1
                       else "-1: no distinct input group supports it")
    else:
        reasons.append(f"0: {count} distinct input groups agree{names}; "
                       f"{config.HIGH_CONFIDENCE_MINIMUM_GROUPS} are needed for high")

    weighing = entry["item_support"] + entry["item_opposition"]
    if weighing <= 0:
        points -= 1
        reasons.append("-1: no evidence speaks to it")
    else:
        share = entry["item_opposition"] / weighing
        what = "of the evidence speaking to it (quality x strength, per item) opposes it"
        if share > config.HIGH_CONFLICT_SHARE:
            points -= 1
            reasons.append(f"-1: {share:.0%} {what} (conflict)")
        elif share < config.LOW_CONFLICT_SHARE:
            points += 1
            reasons.append(f"+1: only {share:.0%} {what}")
        else:
            reasons.append(f"0: {share:.0%} {what}")

    supporting = [items_by_id[item_id] for item_id in entry["supporting"]]
    stale: List[str] = []
    if supporting:
        mean_quality = sum(item.quality for item in supporting) / len(supporting)
        if mean_quality >= config.GOOD_MEAN_QUALITY:
            points += 1
            reasons.append(f"+1: mean quality of the supporting evidence is {mean_quality:.2f}")
        elif mean_quality < config.POOR_MEAN_QUALITY:
            points -= 1
            reasons.append(f"-1: mean quality of the supporting evidence is only {mean_quality:.2f}")
        else:
            reasons.append(f"0: mean quality of the supporting evidence is {mean_quality:.2f}")
        stale = [item.id for item in supporting if _is_stale(item)]
        if stale:
            points -= 1
            reasons.append(f"-1: {len(stale)} supporting item(s) are stale ({', '.join(stale)})")
        else:
            reasons.append("0: all supporting evidence is within its freshness limit")

    position = ranked.index(entry)
    if position == 0:
        if len(ranked) > 1:
            margin = entry["score"] - ranked[1]["score"]
            if margin >= config.WIDE_MARGIN_POINTS:
                points += 1
                reasons.append(f"+1: leads the next scenario by {margin:.1f} points")
            elif margin < config.NARROW_MARGIN_POINTS:
                points -= 1
                reasons.append(f"-1: leads the next scenario by only {margin:.1f} points")
            else:
                reasons.append(f"0: leads the next scenario by {margin:.1f} points")
        else:
            reasons.append("0: the only scenario the map allows")
    else:
        points -= 1
        reasons.append(f"-1: {ranked[0]['score'] - entry['score']:.1f} points below the top scenario "
                       f"({titles[ranked[0]['key']]})")

    if count <= config.LOW_CONFIDENCE_MAXIMUM_GROUPS or points <= config.LOW_CONFIDENCE_MAXIMUM_POINTS or entry["score"] <= 0:
        label = LOW
    elif (count >= config.HIGH_CONFIDENCE_MINIMUM_GROUPS and points >= config.HIGH_CONFIDENCE_MINIMUM_POINTS
          and not (stale and config.STALE_SUPPORT_BLOCKS_HIGH)):
        label = HIGH
    else:
        label = MEDIUM
        if stale and config.STALE_SUPPORT_BLOCKS_HIGH and count >= config.HIGH_CONFIDENCE_MINIMUM_GROUPS \
                and points >= config.HIGH_CONFIDENCE_MINIMUM_POINTS:
            reasons.append("capped at medium: high needs fresh supporting evidence")
    reasons.append(f"{points:+d} points in total -> {label}")
    return label, reasons


def _assess(items: List[EvidenceItem], spot: float) -> Tuple[PriceMap, List[ScenarioAssessment]]:
    """Map and scored scenarios of already freshness-checked items."""
    price_map = build_price_map(items, spot)
    ranked = _score(items, price_map)
    items_by_id = {item.id: item for item in items}
    titles = {entry["key"]: _title(entry["key"], price_map)[0] for entry in ranked}
    scenarios = []
    for entry in ranked:
        title, lower, upper, level, next_level = _title(entry["key"], price_map)
        label, reasons = _confidence(entry, ranked, items_by_id, titles)
        horizons = [items_by_id[item_id].horizon_days for item_id in entry["supporting"]
                    if items_by_id[item_id].horizon_days is not None]
        scenarios.append(ScenarioAssessment(
            key=entry["key"], title=title, lower=lower, upper=upper, level=level, next_level=next_level,
            evidence_score=entry["score"], support=entry["support"], opposition=entry["opposition"],
            group_contributions=entry["contributions"], supporting_evidence_ids=entry["supporting"],
            opposing_evidence_ids=entry["opposing"],
            horizon_days=[min(horizons), max(horizons)] if horizons else None,  # type: ignore[type-var]
            confidence=label, confidence_reasons=reasons))
    return price_map, scenarios


def _variant(item: EvidenceItem) -> Tuple[Optional[EvidenceItem], str]:
    """The flipped item (None = removed) and the plain description of the change."""
    if item.kind == DIRECTIONAL and item.direction in (BULLISH, BEARISH):
        flipped = BEARISH if item.direction == BULLISH else BULLISH
        return replace(item, direction=flipped), f"if {item.id} turned {flipped}"
    if item.kind == DIRECTIONAL:
        return None, f"if {item.id} (balanced flow) were absent"
    if item.kind == RANGE_REGIME:
        flipped = EXPANSION if item.regime == RANGE_BOUND else RANGE_BOUND
        return replace(item, regime=flipped), f"if {item.id} indicated {flipped.replace('_', '-')}"
    return None, f"if {item.id} ({item.kind.replace('_', ' ')} at {_price(item.price or 0.0)}) were absent"


def _what_would_change(items: List[EvidenceItem], spot: float, top: Optional[str]) -> List[ChangeTrigger]:
    """Every item whose flip or removal alone changes the top scenario (id order)."""
    triggers = []
    for index, item in enumerate(items):
        changed, description = _variant(item)
        others = items[:index] + items[index + 1:]
        variant_items = others if changed is None else items[:index] + [changed] + items[index + 1:]
        _, scenarios = _assess(variant_items, spot)
        new_top = scenarios[0].key if scenarios and scenarios[0].evidence_score > 0 else None
        if new_top != top:
            triggers.append(ChangeTrigger(item.id, description, new_top))
    return triggers


def _sources(ids: Sequence[str], items_by_id: Dict[str, EvidenceItem]) -> str:
    if not ids:
        return "nothing"
    return "; ".join(f"{items_by_id[item_id].source} [{item_id}]" for item_id in ids)


def _decisions(price_map: PriceMap, scenarios: List[ScenarioAssessment], top: Optional[str], confidence: str,
               items: List[EvidenceItem], triggers: List[ChangeTrigger], not_computed: Sequence[str],
               caveats: Sequence[str] = ()) -> List[DecisionItem]:
    """The plain-language decision-support lines."""
    spot = price_map.spot
    distance = f"{config.MAXIMUM_LEVEL_DISTANCE_SHARE:.0%}"
    items_by_id = {item.id: item for item in items}
    support, resistance = price_map.support, price_map.resistance
    lines = [DecisionItem("notice", "These are decision-support items (reference levels and conditions), not orders "
                                    "and not price targets.")]
    top_scenario = next((item for item in scenarios if item.key == top), None)
    if top_scenario is None:
        lines.append(DecisionItem("summary", "No scenario has net support: no reference decision follows from this evidence."))
    else:
        lines.append(DecisionItem("summary", f"Best-supported scenario: {top_scenario.title} (evidence score "
                                             f"{top_scenario.evidence_score:.0f} on a 0-100 scale, uncalibrated, not a probability; confidence {confidence})."))
    if support is not None and resistance is not None:
        lines.append(DecisionItem("reference_range", (
            f"Reference range for a strategy: {_price(support.price)} to {_price(resistance.price)} "
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
    for scenario in scenarios:
        if scenario.key == INSIDE:
            continue
        side = "above" if scenario.key == BREAK_UP else "below"
        beyond = (f"the next mapped level {side} is {_price(scenario.next_level)}" if scenario.next_level is not None
                  else f"nothing further is mapped {side} within {distance}")
        lines.append(DecisionItem("watch", (
            f"Watch: a break {side} {_price(scenario.level or 0.0)}. Supported now by: "
            f"{_sources(scenario.supporting_evidence_ids, items_by_id)}. Opposed by: "
            f"{_sources(scenario.opposing_evidence_ids, items_by_id)}. Beyond it {beyond}.")))
    for band in price_map.at_spot:
        lines.append(DecisionItem("watch", (
            f"The spot is inside a mapped band ({_price(band.low)} to {_price(band.high)}, from "
            f"{', '.join(band.input_groups)}): the price is at a mapped level now, so it bounds neither side.")))
    if resistance is None:
        lines.append(DecisionItem("unmapped", f"No meaningful level above within {distance} of the spot: the upside is unmapped."))
    if support is None:
        lines.append(DecisionItem("unmapped", f"No meaningful level below within {distance} of the spot: the downside is unmapped."))
    if triggers:
        lines.append(DecisionItem("watch", "What would change the top scenario: " + "; ".join(
            f"{trigger.change} -> {trigger.new_top_scenario or 'no scenario with net support'}" for trigger in triggers) + "."))
    elif top is not None:
        lines.append(DecisionItem("watch", "No single item's flip or removal changes the top scenario."))
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
        not_computed: Sources that could not be computed, with why (shown as caveats, not used in scores).
        context: Extra numbers for the reader (not used in scores; must be JSON-serialisable).
        caveats: Warnings about the inputs that are not tied to one item (e.g. an old spot); shown as data caveats and
            kept in ``context["caveats"]``.

    Raises:
        ValueError: naive ``as_of``, lookahead, duplicate ids or a non-positive reference price.
    """
    items = apply_freshness(evidence, as_of)
    price_map, scenarios = _assess(items, reference_price)
    top = scenarios[0].key if scenarios and scenarios[0].evidence_score > 0 else None
    top_scenario = scenarios[0] if top is not None else None
    confidence = top_scenario.confidence if top_scenario is not None else LOW
    reasons = (list(top_scenario.confidence_reasons) if top_scenario is not None
               else ["no scenario has net support" if scenarios else "no mapped level on either side of the spot"])
    triggers = _what_would_change(items, reference_price, top)
    return DecisionReport(
        currency=currency, as_of=as_of, reference_price=reference_price, score_scale=config.EVIDENCE_SCORE_SCALE,
        evidence=items, price_map=price_map, scenarios=scenarios, top_scenario=top, confidence=confidence,
        confidence_reasons=reasons, what_would_change=triggers,
        decisions=_decisions(price_map, scenarios, top, confidence, items, triggers, not_computed, caveats),
        not_computed=list(not_computed), context={**(context or {}), "caveats": list(caveats)})
