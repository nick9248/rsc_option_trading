"""
The output of the evidence report, JSON-serialisable both ways (``to_dict`` / ``from_dict``), so a report can be stored in
``decision_snapshots`` exactly as it was produced.

It holds what the sources say (evidence items with their strength, quality and caveats) and the price map built from the levels
among them. It does NOT score scenarios or label confidence: that was removed on 2026-11-21 because the scores were never
calibrated against outcomes.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List

from coding.core.analytics.decision.evidence import EvidenceItem
from coding.core.analytics.decision.price_map import PriceMap

METHOD_VERSION = "evidence-report-1"
"""Stored with every report: a later reader must know which rules produced it."""

DISCLAIMER = ("Reference levels and the evidence behind them, as listed. Not an order, not a price target, not a forecast and "
              "not a ranking of scenarios: strength and quality are hand-set priors, not probabilities.")


@dataclass(frozen=True)
class DecisionItem:
    """One reference line: a level, a condition to watch or a caveat. Never an order or a target."""

    kind: str       # notice, reference_range, reference_hedge, watch, unmapped, data_caveat
    text: str

    def to_dict(self) -> Dict[str, Any]:
        """JSON-serialisable form."""
        return {"kind": self.kind, "text": self.text}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DecisionItem":
        """The inverse of ``to_dict``."""
        return cls(data["kind"], data["text"])


@dataclass(frozen=True)
class DecisionSnapshotRecord:
    """One stored report as read from ``decision_snapshots``: the key, the spot and the report dict exactly as saved."""

    currency: str
    computed_at: datetime
    reference_price: float
    report: Dict[str, Any]


@dataclass(frozen=True)
class DecisionReport:
    """
    Everything one run produced.

    Attributes:
        currency: "BTC" or "ETH" (any label for other uses).
        as_of: The instant the report describes (UTC-aware); no item may be newer.
        reference_price: The spot the map is built around.
        evidence: Every item after the freshness check, id order.
        price_map: The bands and reference levels.
        decisions: Plain-language reference lines (levels, what is unmapped, data caveats).
        not_computed: Sources that could not be computed, with why.
        context: Extra numbers for the reader (e.g. open interest per expiry).
        disclaimer: What the report is not.
        method_version: Version of the rules (``METHOD_VERSION``).
    """

    currency: str
    as_of: datetime
    reference_price: float
    evidence: List[EvidenceItem]
    price_map: PriceMap
    decisions: List[DecisionItem]
    not_computed: List[str] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    disclaimer: str = DISCLAIMER
    method_version: str = METHOD_VERSION

    def to_dict(self) -> Dict[str, Any]:
        """JSON-serialisable form (times as ISO 8601 strings)."""
        return {"currency": self.currency, "as_of": self.as_of.isoformat(), "reference_price": self.reference_price,
                "evidence": [item.to_dict() for item in self.evidence], "price_map": self.price_map.to_dict(),
                "decisions": [item.to_dict() for item in self.decisions], "not_computed": list(self.not_computed),
                "context": self.context, "disclaimer": self.disclaimer, "method_version": self.method_version}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DecisionReport":
        """The inverse of ``to_dict``."""
        return cls(
            currency=data["currency"], as_of=datetime.fromisoformat(data["as_of"]),
            reference_price=data["reference_price"], evidence=[EvidenceItem.from_dict(item) for item in data["evidence"]],
            price_map=PriceMap.from_dict(data["price_map"]), decisions=[DecisionItem.from_dict(item) for item in data["decisions"]],
            not_computed=list(data["not_computed"]), context=dict(data["context"]), disclaimer=data["disclaimer"],
            method_version=data["method_version"])
