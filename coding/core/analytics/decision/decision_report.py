"""
The output of the decision maker, JSON-serialisable both ways (``to_dict`` / ``from_dict``), so a report can be stored in
``decision_snapshots`` exactly as it was produced and read back for calibration later.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from coding.core.analytics.decision.evidence import EvidenceItem
from coding.core.analytics.decision.price_map import PriceMap

INSIDE = "inside"
BREAK_UP = "break_up"
BREAK_DOWN = "break_down"
SCENARIO_ORDER = (INSIDE, BREAK_UP, BREAK_DOWN)      # tie-break of equal scores, so the ranking is deterministic

LOW = "low"
MEDIUM = "medium"
HIGH = "high"

METHOD_VERSION = "decision-maker-1"
"""Stored with every report: a later calibration must know which rules produced the scores."""

DISCLAIMER = ("Decision support only: reference levels and conditions derived from the evidence listed. Not an order, not a "
              "price target and not a forecast. Evidence scores are uncalibrated relative support (0-100), not probabilities; "
              "the confidence label is ordinal and comes with its reasons.")


@dataclass(frozen=True)
class ScenarioAssessment:
    """
    One scenario with its score and the evidence behind it.

    Attributes:
        key: ``INSIDE``, ``BREAK_UP`` or ``BREAK_DOWN``.
        title: Plain-language name with its levels, e.g. "breaks above 68,000".
        lower: Lower bound of the range (inside) or None.
        upper: Upper bound of the range (inside) or None.
        level: The level a break passes (breaks) or None.
        next_level: The next mapped level beyond the broken one, None when nothing is mapped there.
        evidence_score: 0..100, uncalibrated, not a probability.
        support: Sum of the positive group contributions.
        opposition: Sum of the sizes of the negative group contributions.
        group_contributions: Signed contribution of each input group.
        supporting_evidence_ids: Items that speak for it.
        opposing_evidence_ids: Items that speak against it.
        horizon_days: [shortest, longest] horizon of the supporting items that have one, None when none has.
        confidence: low / medium / high.
        confidence_reasons: The reasons that produced the label, one per criterion.
    """

    key: str
    title: str
    lower: Optional[float]
    upper: Optional[float]
    level: Optional[float]
    next_level: Optional[float]
    evidence_score: float
    support: float
    opposition: float
    group_contributions: Dict[str, float]
    supporting_evidence_ids: List[str]
    opposing_evidence_ids: List[str]
    horizon_days: Optional[List[float]]
    confidence: str
    confidence_reasons: List[str]

    def to_dict(self) -> Dict[str, Any]:
        """JSON-serialisable form."""
        return {"key": self.key, "title": self.title, "lower": self.lower, "upper": self.upper, "level": self.level,
                "next_level": self.next_level, "evidence_score": self.evidence_score, "support": self.support,
                "opposition": self.opposition, "group_contributions": dict(self.group_contributions),
                "supporting_evidence_ids": list(self.supporting_evidence_ids),
                "opposing_evidence_ids": list(self.opposing_evidence_ids),
                "horizon_days": list(self.horizon_days) if self.horizon_days is not None else None,
                "confidence": self.confidence, "confidence_reasons": list(self.confidence_reasons)}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ScenarioAssessment":
        """The inverse of ``to_dict``."""
        return cls(data["key"], data["title"], data["lower"], data["upper"], data["level"], data["next_level"],
                   data["evidence_score"], data["support"], data["opposition"], dict(data["group_contributions"]),
                   list(data["supporting_evidence_ids"]), list(data["opposing_evidence_ids"]),
                   list(data["horizon_days"]) if data["horizon_days"] is not None else None, data["confidence"],
                   list(data["confidence_reasons"]))


@dataclass(frozen=True)
class ChangeTrigger:
    """One item whose flip (or removal) would change the top scenario: what changes and what would lead then."""

    evidence_id: str
    change: str
    new_top_scenario: Optional[str]

    def to_dict(self) -> Dict[str, Any]:
        """JSON-serialisable form."""
        return {"evidence_id": self.evidence_id, "change": self.change, "new_top_scenario": self.new_top_scenario}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChangeTrigger":
        """The inverse of ``to_dict``."""
        return cls(data["evidence_id"], data["change"], data["new_top_scenario"])


@dataclass(frozen=True)
class DecisionItem:
    """One decision-support line: a reference level, a condition to watch or a caveat. Never an order or a target."""

    kind: str       # notice, summary, reference_range, reference_hedge, watch, unmapped, data_caveat
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
        score_scale: What the evidence score means (uncalibrated, not a probability).
        evidence: Every item after the engine's freshness check, id order.
        price_map: The bands and reference levels.
        scenarios: Best score first.
        top_scenario: Key of the best-supported scenario, None when no scenario has net support.
        confidence: The top scenario's label ("low" without a top scenario).
        confidence_reasons: Its reasons.
        what_would_change: Items whose flip or removal would change the top scenario.
        decisions: Plain-language decision-support lines.
        not_computed: Sources that could not be computed, with why.
        context: Extra numbers for the reader (e.g. open interest per expiry); not used in the scores.
        disclaimer: What the report is not.
        method_version: Version of the rules (``METHOD_VERSION``).
    """

    currency: str
    as_of: datetime
    reference_price: float
    score_scale: str
    evidence: List[EvidenceItem]
    price_map: PriceMap
    scenarios: List[ScenarioAssessment]
    top_scenario: Optional[str]
    confidence: str
    confidence_reasons: List[str]
    what_would_change: List[ChangeTrigger]
    decisions: List[DecisionItem]
    not_computed: List[str] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    disclaimer: str = DISCLAIMER
    method_version: str = METHOD_VERSION

    def scenario(self, key: str) -> Optional[ScenarioAssessment]:
        """The scenario with ``key``, None when it was not generated."""
        return next((item for item in self.scenarios if item.key == key), None)

    def to_dict(self) -> Dict[str, Any]:
        """JSON-serialisable form (times as ISO 8601 strings)."""
        return {"currency": self.currency, "as_of": self.as_of.isoformat(), "reference_price": self.reference_price,
                "score_scale": self.score_scale, "evidence": [item.to_dict() for item in self.evidence],
                "price_map": self.price_map.to_dict(), "scenarios": [item.to_dict() for item in self.scenarios],
                "top_scenario": self.top_scenario, "confidence": self.confidence,
                "confidence_reasons": list(self.confidence_reasons),
                "what_would_change": [item.to_dict() for item in self.what_would_change],
                "decisions": [item.to_dict() for item in self.decisions], "not_computed": list(self.not_computed),
                "context": self.context, "disclaimer": self.disclaimer, "method_version": self.method_version}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DecisionReport":
        """The inverse of ``to_dict``."""
        return cls(
            currency=data["currency"], as_of=datetime.fromisoformat(data["as_of"]),
            reference_price=data["reference_price"], score_scale=data["score_scale"],
            evidence=[EvidenceItem.from_dict(item) for item in data["evidence"]],
            price_map=PriceMap.from_dict(data["price_map"]),
            scenarios=[ScenarioAssessment.from_dict(item) for item in data["scenarios"]],
            top_scenario=data["top_scenario"], confidence=data["confidence"],
            confidence_reasons=list(data["confidence_reasons"]),
            what_would_change=[ChangeTrigger.from_dict(item) for item in data["what_would_change"]],
            decisions=[DecisionItem.from_dict(item) for item in data["decisions"]],
            not_computed=list(data["not_computed"]), context=dict(data["context"]), disclaimer=data["disclaimer"],
            method_version=data["method_version"])
