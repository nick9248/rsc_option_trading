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
