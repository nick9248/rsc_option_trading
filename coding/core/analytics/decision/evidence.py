"""
The evidence layer: one structured, source-independent form that every signal is converted into.

An extractor (``coding/service/decision/``) turns the output of an existing service into ``EvidenceItem`` values. The
engine only reads these fields, so a new data source is a new extractor and nothing else.

Independence: ``input_group`` names the underlying data an item is derived from (for example "deribit_open_interest").
Items that share an input group are NOT independent (MenthorQ replica levels and open-interest dealer walls are both
computed from the same open interest), so the engine counts each group once.
"""

import json
import math
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

PRICE_LEVEL = "price_level"
DIRECTIONAL = "directional"
RANGE_REGIME = "range_regime"
MAGNET = "magnet"
KINDS: Tuple[str, ...] = (PRICE_LEVEL, DIRECTIONAL, RANGE_REGIME, MAGNET)

BULLISH = "bullish"
BEARISH = "bearish"
NEUTRAL = "neutral"
DIRECTIONS: Tuple[str, ...] = (BULLISH, BEARISH, NEUTRAL)

RANGE_BOUND = "range_bound"     # moves damped: supports "stays inside"
EXPANSION = "expansion"         # moves amplified: opposes "stays inside", no direction
REGIMES: Tuple[str, ...] = (RANGE_BOUND, EXPANSION)


@dataclass(frozen=True)
class EvidenceItem:
    """
    One piece of evidence.

    Attributes:
        id: Unique, stable identifier (the engine sorts by it, so the same inputs give the same output).
        source: Human name of where it comes from, e.g. "MenthorQ replica (monthly expiries)".
        input_group: The underlying data it is derived from; items of one group are not independent.
        kind: One of ``KINDS``. price_level: a price where sources put a level (needs ``price``); directional: a lean up
            or down (needs ``direction``); range_regime: moves damped or amplified (needs ``regime``); magnet: a pool of
            forced orders at a price (needs ``price``; no direction of its own).
        statement: One plain sentence of what it says AND what it does not say.
        as_of: When the underlying data was observed (UTC-aware); the engine judges freshness from it.
        strength: 0..1, how strongly it says what it says.
        quality: 0..1, how much it can be trusted; ``quality_reasons`` lists what lowered it.
        direction: bullish / bearish / neutral where meaningful (directional items), else None.
        regime: range_bound / expansion for range_regime items, else None.
        price: The price of a price_level or magnet item, else None.
        horizon_days: The time the evidence speaks about (e.g. days to its expiry), None when it has none.
        quality_reasons: Why the quality is below 1 (stale data, failed plausibility check, assumption-based, ...).
        details: Numbers behind the item for the audit trail (JSON-serialisable values only).
    """

    id: str
    source: str
    input_group: str
    kind: str
    statement: str
    as_of: datetime
    strength: float
    quality: float
    direction: Optional[str] = None
    regime: Optional[str] = None
    price: Optional[float] = None
    horizon_days: Optional[float] = None
    quality_reasons: Tuple[str, ...] = ()
    details: Dict[str, Any] = field(default_factory=dict, compare=True, hash=False)

    def __post_init__(self) -> None:
        """Refuse an item the engine could not use honestly (bad ranges, naive times, missing price or direction)."""
        if not self.id or not self.input_group:
            raise ValueError("an evidence item needs an id and an input group")
        if self.kind not in KINDS:
            raise ValueError(f"{self.id}: kind {self.kind!r} is not one of {KINDS}")
        if self.as_of.tzinfo is None:
            raise ValueError(f"{self.id}: as_of must be timezone-aware (UTC)")
        for name in ("strength", "quality"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{self.id}: {name} must be a finite number in 0..1, got {value!r}")
        if self.kind in (PRICE_LEVEL, MAGNET) and (self.price is None or not math.isfinite(self.price) or self.price <= 0):
            raise ValueError(f"{self.id}: a {self.kind} item needs a positive price")
        if self.kind == DIRECTIONAL and self.direction not in DIRECTIONS:
            raise ValueError(f"{self.id}: a directional item needs a direction in {DIRECTIONS}")
        if self.kind == RANGE_REGIME and self.regime not in REGIMES:
            raise ValueError(f"{self.id}: a range_regime item needs a regime in {REGIMES}")
        try:
            json.dumps(self.details, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError(f"{self.id}: details must be JSON-serialisable without NaN or infinity ({error})") from error
        if self.horizon_days is not None and (not math.isfinite(self.horizon_days) or self.horizon_days < 0):
            raise ValueError(f"{self.id}: horizon_days must be a non-negative number or None")

    def with_quality(self, quality: float, reason: str) -> "EvidenceItem":
        """A copy with a new quality and one more reason (the original is unchanged)."""
        return replace(self, quality=quality, quality_reasons=self.quality_reasons + (reason,))

    def to_dict(self) -> Dict[str, Any]:
        """JSON-serialisable form (times as ISO 8601 strings)."""
        return {
            "id": self.id, "source": self.source, "input_group": self.input_group, "kind": self.kind,
            "statement": self.statement, "as_of": self.as_of.isoformat(), "strength": self.strength,
            "quality": self.quality, "direction": self.direction, "regime": self.regime, "price": self.price,
            "horizon_days": self.horizon_days, "quality_reasons": list(self.quality_reasons), "details": self.details,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EvidenceItem":
        """The inverse of ``to_dict``."""
        return cls(
            id=data["id"], source=data["source"], input_group=data["input_group"], kind=data["kind"],
            statement=data["statement"], as_of=datetime.fromisoformat(data["as_of"]), strength=data["strength"],
            quality=data["quality"], direction=data.get("direction"), regime=data.get("regime"), price=data.get("price"),
            horizon_days=data.get("horizon_days"), quality_reasons=tuple(data.get("quality_reasons", ())),
            details=dict(data.get("details", {})))


def sorted_evidence(items: List[EvidenceItem]) -> List[EvidenceItem]:
    """
    The items in id order.

    Raises:
        ValueError: when two items share an id (the audit trail would be ambiguous).
    """
    ordered = sorted(items, key=lambda item: item.id)
    for first, second in zip(ordered, ordered[1:]):
        if first.id == second.id:
            raise ValueError(f"duplicate evidence id {first.id!r}")
    return ordered
