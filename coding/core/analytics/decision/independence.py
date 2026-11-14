"""
Independence-aware aggregation: the items of one input group are one opinion.

For the items of one group, each with a quality q and a value x (a strength, or a signed stance times strength):

* the group's signal is the quality-weighted mean of x:  m = sum(q x) / sum(q)
* the group's quality is the quality-weighted mean quality:  Q = sum(q q) / sum(q)
* the group's contribution is  weight x m x Q, clipped to +- ``GROUP_CONTRIBUTION_CAP``.

So two items of one group with the same value count once (the mean of two equal values is that value), while two
groups count twice. A group whose items all have quality 0 contributes 0.
"""

from typing import Dict, Iterable, List, Tuple

from coding.core.analytics.decision import decision_config
from coding.core.analytics.decision.evidence import EvidenceItem


def group_weight(input_group: str) -> float:
    """The configured weight of an input group (the default weight for a group the config does not list)."""
    return decision_config.GROUP_WEIGHTS.get(input_group, decision_config.DEFAULT_GROUP_WEIGHT)


def group_contribution(values: List[Tuple[EvidenceItem, float]]) -> float:
    """
    The contribution of ONE group from its (item, value) pairs: weight x quality-weighted mean value x quality-weighted
    mean quality, clipped to the cap. 0 for no items or zero total quality.

    Raises:
        ValueError: when the items are not all of one input group.
    """
    if not values:
        return 0.0
    groups = {item.input_group for item, _ in values}
    if len(groups) != 1:
        raise ValueError(f"group_contribution needs the items of one input group, got {sorted(groups)}")
    total_quality = sum(item.quality for item, _ in values)
    if total_quality <= 0:
        return 0.0
    mean_value = sum(item.quality * value for item, value in values) / total_quality
    mean_quality = sum(item.quality * item.quality for item, _ in values) / total_quality
    raw = group_weight(values[0][0].input_group) * mean_value * mean_quality
    cap = decision_config.GROUP_CONTRIBUTION_CAP
    return max(-cap, min(cap, raw))


def contributions_by_group(values: Iterable[Tuple[EvidenceItem, float]]) -> Dict[str, float]:
    """The contribution of every input group among (item, value) pairs, by group name (sorted)."""
    by_group: Dict[str, List[Tuple[EvidenceItem, float]]] = {}
    for item, value in values:
        by_group.setdefault(item.input_group, []).append((item, value))
    return {group: group_contribution(by_group[group]) for group in sorted(by_group)}
