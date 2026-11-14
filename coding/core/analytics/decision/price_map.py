"""
The price map: bands of price_level evidence around the spot, the nearest meaningful support below and resistance above.

Bands: the price_level items within ``MAXIMUM_LEVEL_DISTANCE_SHARE`` of the spot are taken from the lowest price up; a band
closes when the next price is further than ``LEVEL_BAND_SHARE`` x spot from the band's FIRST price (the greedy rule of
``find_confluences`` on the Levels page; that function keeps only multi-source bands, the map needs every band).

A band's weight = sum over its input groups of the group's contribution (``independence.group_contribution`` with value =
strength), so levels of one input group in a band count once. A band is meaningful when its weight reaches
``MINIMUM_LEVEL_WEIGHT``. Whether a level is "support" or "resistance" follows only from its side of the spot: a put wall
above the spot is a level above the spot. A band whose price range contains the spot is "at the spot", neither. A side without a meaningful band within the distance is "unmapped" (None),
never filled with an invented level.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from coding.core.analytics.decision import decision_config
from coding.core.analytics.decision.evidence import PRICE_LEVEL, EvidenceItem
from coding.core.analytics.decision.independence import contributions_by_group


@dataclass(frozen=True)
class MapLevel:
    """
    One band of the map.

    Attributes:
        price: The strength x quality weighted mean price of its items (the plain mean when all of them weigh 0).
        low: Lowest item price.
        high: Highest item price.
        weight: Sum of its input groups' contributions (independence-aware).
        input_groups: The distinct input groups, sorted.
        evidence_ids: Its items, sorted.
        meaningful: weight >= ``MINIMUM_LEVEL_WEIGHT``.
    """

    price: float
    low: float
    high: float
    weight: float
    input_groups: List[str]
    evidence_ids: List[str]
    meaningful: bool

    def to_dict(self) -> Dict[str, Any]:
        """JSON-serialisable form."""
        return {"price": self.price, "low": self.low, "high": self.high, "weight": self.weight,
                "input_groups": list(self.input_groups), "evidence_ids": list(self.evidence_ids),
                "meaningful": self.meaningful}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MapLevel":
        """The inverse of ``to_dict``."""
        return cls(data["price"], data["low"], data["high"], data["weight"], list(data["input_groups"]),
                   list(data["evidence_ids"]), data["meaningful"])


@dataclass(frozen=True)
class PriceMap:
    """
    All bands within the distance, and the four reference levels picked from the meaningful ones.

    Attributes:
        spot: Reference price.
        band_width: Band width in price units (``LEVEL_BAND_SHARE`` x spot).
        maximum_distance: Distance in price units beyond which levels are left out.
        levels: Every band, lowest first.
        support: Nearest meaningful band below the spot, None when the downside is unmapped.
        resistance: Nearest meaningful band above the spot, None when the upside is unmapped.
        next_below: The meaningful band after ``support`` going down, None when there is none within the distance.
        next_above: The meaningful band after ``resistance`` going up, None when there is none within the distance.
        left_out: Ids of price_level items beyond the distance.
        at_spot: Meaningful bands whose price range contains the spot (neither support nor resistance: the price is at
            them now).
    """

    spot: float
    band_width: float
    maximum_distance: float
    levels: List[MapLevel]
    support: Optional[MapLevel]
    resistance: Optional[MapLevel]
    next_below: Optional[MapLevel]
    next_above: Optional[MapLevel]
    left_out: List[str] = field(default_factory=list)
    at_spot: List[MapLevel] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """JSON-serialisable form."""
        def level(item: Optional[MapLevel]) -> Optional[Dict[str, Any]]:
            return item.to_dict() if item is not None else None
        return {"spot": self.spot, "band_width": self.band_width, "maximum_distance": self.maximum_distance,
                "levels": [item.to_dict() for item in self.levels], "support": level(self.support),
                "resistance": level(self.resistance), "next_below": level(self.next_below),
                "next_above": level(self.next_above), "left_out": list(self.left_out),
                "at_spot": [item.to_dict() for item in self.at_spot]}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PriceMap":
        """The inverse of ``to_dict``."""
        def level(item: Optional[Dict[str, Any]]) -> Optional[MapLevel]:
            return MapLevel.from_dict(item) if item is not None else None
        return cls(data["spot"], data["band_width"], data["maximum_distance"],
                   [MapLevel.from_dict(item) for item in data["levels"]], level(data["support"]),
                   level(data["resistance"]), level(data["next_below"]), level(data["next_above"]),
                   list(data["left_out"]), [MapLevel.from_dict(item) for item in data.get("at_spot", [])])


def _band_level(items: List[EvidenceItem]) -> MapLevel:
    """One band from its items."""
    contributions = contributions_by_group((item, item.strength) for item in items)
    weight = sum(contributions.values())
    weights = [item.strength * item.quality for item in items]
    prices = [item.price or 0.0 for item in items]
    total = sum(weights)
    center = (sum(price * value for price, value in zip(prices, weights)) / total if total > 0
              else sum(prices) / len(prices))
    return MapLevel(price=center, low=min(prices), high=max(prices), weight=weight,
                    input_groups=sorted(contributions), evidence_ids=sorted(item.id for item in items),
                    meaningful=weight >= decision_config.MINIMUM_LEVEL_WEIGHT)


def build_price_map(items: List[EvidenceItem], spot: float) -> PriceMap:
    """
    The map of the price_level items (other kinds are ignored).

    Raises:
        ValueError: for a non-positive spot.
    """
    if spot <= 0:
        raise ValueError("the spot must be positive")
    band_width = decision_config.LEVEL_BAND_SHARE * spot
    maximum_distance = decision_config.MAXIMUM_LEVEL_DISTANCE_SHARE * spot
    levels = [item for item in items if item.kind == PRICE_LEVEL]
    inside = sorted((item for item in levels if abs((item.price or 0.0) - spot) <= maximum_distance),
                    key=lambda item: (item.price, item.id))
    left_out = sorted(item.id for item in levels if abs((item.price or 0.0) - spot) > maximum_distance)

    bands: List[List[EvidenceItem]] = []
    for item in inside:
        if bands and (item.price or 0.0) - (bands[-1][0].price or 0.0) <= band_width:
            bands[-1].append(item)
        else:
            bands.append([item])
    map_levels = [_band_level(band) for band in bands]

    at_spot = [level for level in map_levels if level.meaningful and level.low <= spot <= level.high]
    below = [level for level in map_levels if level.meaningful and level.high < spot]
    above = [level for level in map_levels if level.meaningful and level.low > spot]
    below.sort(key=lambda level: -level.price)      # nearest first
    above.sort(key=lambda level: level.price)
    return PriceMap(
        spot=spot, band_width=band_width, maximum_distance=maximum_distance, levels=map_levels,
        support=below[0] if below else None, resistance=above[0] if above else None,
        next_below=below[1] if len(below) > 1 else None, next_above=above[1] if len(above) > 1 else None,
        left_out=left_out, at_spot=at_spot)
