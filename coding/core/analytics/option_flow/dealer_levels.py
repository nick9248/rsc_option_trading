"""Result types of the flow-based dealer levels of one expiry (see ``DealerFlowLevelsService`` for the method)."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class StrikeLevelRow:
    """One strike of one expiry. GEX is USD per 1 % move of the spot; positions are in contracts."""

    strike: float
    dealer_net_calls: float          # minus the cumulative taker flow of the calls (negative = dealers are short)
    dealer_net_puts: float
    flow_gex: float                  # flow-based (inferred) GEX
    open_interest_gex: Optional[float]          # standard OI-based GEX (dealers long calls, short puts); None when not computed
    call_open_interest: Optional[float]
    put_open_interest: Optional[float]


@dataclass
class ViolatingOption:
    """One option whose dealer position is larger than its open interest (a failure of the plausibility check)."""

    strike: float
    option_type: str                 # "C" or "P"
    dealer_position: float           # contracts; minus the cumulative taker flow
    open_interest: float
    excess: float                    # |dealer position| - open interest, above zero

    @property
    def position_to_open_interest(self) -> Optional[float]:
        """|dealer position| / open interest; None when the open interest is 0 (no ratio exists)."""
        return abs(self.dealer_position) / self.open_interest if self.open_interest > 0 else None


@dataclass
class DealerFlowLevels:
    """The result of one run."""

    currency: str
    expiry: str
    index_price: float
    chain_captured_at: datetime
    flow_start: datetime             # T0: the first stored trade of the expiry (not before the stable-coverage date)
    trades_used: int
    rows: List[StrikeLevelRow]
    flow_call_wall: Optional[float]
    flow_put_wall: Optional[float]
    flow_gamma_flip: Optional[float]
    open_interest_call_wall: Optional[float]
    open_interest_put_wall: Optional[float]
    open_interest_gamma_flip: Optional[float]
    total_flow_gex: float
    total_open_interest_gex: Optional[float]
    coverage: float                  # share of hours since T0 with at least one stored trade (currency-wide)
    violation_rate: float            # share of options whose |dealer position| exceeds their open interest
    violations: int
    options_checked: int
    options_without_open_interest: int
    gate_passed: bool
    gate_reason: Optional[str]
    notes: List[str] = field(default_factory=list)
    violating_options: List[ViolatingOption] = field(default_factory=list)   # all failures, largest excess first


def violating_gamma_share(levels: DealerFlowLevels) -> Optional[float]:
    """
    Share of the flow-based gamma that sits on strikes with at least one violating option:
    sum of |flow GEX| over the violating strikes divided by the sum of |flow GEX| over all strikes of the expiry.
    A strike counts once even when both its call and its put violate. None when the total is 0 (no share exists).
    """
    total = sum(abs(row.flow_gex) for row in levels.rows)
    if total <= 0:
        return None
    violating_strikes = {option.strike for option in levels.violating_options}
    return sum(abs(row.flow_gex) for row in levels.rows if row.strike in violating_strikes) / total
