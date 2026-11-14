"""
Flow-based dealer levels of ONE expiry, next to the standard open-interest (OI) based levels.

Idea (Glassnode's taker-flow method, already in ``DealerInventoryCalculator``): every trade has a taker (the side that
crossed the book) and a maker. We assume the maker is a dealer. So if takers bought 100 contracts of an option since it was
listed, dealers sold 100: the dealer's position in that option is minus the cumulative taker flow. Gamma exposure (GEX) of
the dealers is then ``dealer position x gamma x spot^2 x 1%`` per strike, and the levels are read from it: the call wall (largest
positive GEX), the put wall (largest negative GEX) and the gamma flip (where the running sum over strikes changes sign).

The standard way instead assumes dealers are long calls and short puts of the whole open interest. Both views are computed here
for the same expiry so they can be compared. Greeks come from the newest stored option-chain snapshot (Black-Scholes from the
mark IV, the way the MenthorQ replication does it); the signed flow comes from ``historical_trades``.

There is no ground truth for either view. The flow-based view has a built-in plausibility check: a dealer cannot hold more than
the open interest of an option. The share of options that break it (``violation_rate``) and the share of hours with stored trades
(``coverage``) are returned, and ``gate_passed`` applies the project's binding thresholds (coverage >= 95 %, violations <= 5 %).
"""

import logging
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple, cast

import plotly.graph_objects as go

from coding.core.analytics.dealer_inventory_calculator import DealerInventoryCalculator
from coding.core.analytics.option_flow.dealer_levels import DealerFlowLevels, StrikeLevelRow
from coding.core.analytics.option_flow.dealer_levels_figure import build_dealer_levels_figure
from coding.core.analytics.menthorq_replication_calculator import (
    GAMMA_UNDERLYING_INDEX, build_priced_instruments, compute_exposure, parse_expiry_settlement)
from coding.core.database.repository import DatabaseRepository
from coding.service.on_chain.menthorq_input_loader import MenthorqInputLoader
from coding.service.on_chain.on_chain_analysis_service import (
    _DEALER_INVENTORY_COVERAGE_GATE as COVERAGE_GATE,
    _DEALER_INVENTORY_COVERAGE_STABLE_DATE as COVERAGE_STABLE_DATE,
    _DEALER_INVENTORY_MAX_EXCLUSION_RATE as MAX_EXCLUSION_RATE,
    _DEALER_INVENTORY_VIOLATION_GATE as VIOLATION_GATE)

logger = logging.getLogger(__name__)


class DealerFlowLevelsService:
    """Computes both views of one expiry."""

    def __init__(self, repository: Optional[DatabaseRepository] = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        """
        Args:
            repository: Database access (the configured database by default); only read methods are used.
            clock: Wall clock (UTC-aware).
        """
        self.repository = repository or DatabaseRepository()
        self.loader = MenthorqInputLoader(self.repository)
        self.clock = clock

    @staticmethod
    def build_figure(levels: DealerFlowLevels) -> go.Figure:
        """The interactive Plotly figure of a result."""
        return build_dealer_levels_figure(levels)

    def expirations(self, currency: str = "BTC") -> List[str]:
        """The not-yet-expired expirations of the newest stored chain snapshot, soonest first."""
        now = self.clock()
        snapshot = self.repository.get_option_chain_snapshot_at_or_before(currency, now)
        if snapshot is None:
            return []
        labels = {row["expiration"] for row in snapshot[1] if row.get("expiration")}
        settlements = {label: parse_expiry_settlement(label) for label in labels}
        upcoming = [label for label, settlement in settlements.items() if settlement is not None and settlement > now]
        return sorted(upcoming, key=lambda label: cast(datetime, settlements[label]))

    def levels(self, currency: str, expiry: str) -> DealerFlowLevels:
        """
        Both views for ``expiry`` as of now.

        Raises:
            LookupError: no chain snapshot or index price is stored, or the expiry is not in the newest snapshot.
        """
        now = self.clock()
        expiry = expiry.upper()
        inputs = self.loader.load_inputs(currency, now)
        chain_captured_at, index_price = inputs["snapshot_captured_at_utc"], inputs["index_price"]
        instruments = build_priced_instruments(inputs["chain_rows"], [expiry], index_price, chain_captured_at,
                                               GAMMA_UNDERLYING_INDEX)
        if not instruments:
            raise LookupError(f"{expiry} is not in the {currency} chain snapshot of {chain_captured_at:%Y-%m-%d %H:%M} UTC")

        greeks: Dict[Tuple[float, str], Dict[str, Optional[float]]] = {}
        open_interest: Dict[Tuple[float, str], float] = {}
        for item in instruments:
            key = (item["strike"], item["option_type"])
            greeks[key] = {"gamma": item["gamma"], "delta": item["delta"]}
            if item["open_interest"] is not None:
                open_interest[key] = item["open_interest"]

        first_trade_ms = self.repository.get_first_trade_timestamp(currency, expiry)
        stable_ms = int(COVERAGE_STABLE_DATE.timestamp() * 1000)
        start_ms = stable_ms if first_trade_ms is None else max(first_trade_ms, stable_ms)
        flow_rows = self.repository.get_signed_taker_flow_by_strike(currency, expiry, start_ms)
        present_hours, expected_hours = self.repository.get_trade_hour_coverage(currency, start_ms)
        coverage = present_hours / expected_hours if expected_hours > 0 else 0.0

        calculator = DealerInventoryCalculator(flow_rows, cast(Dict[Tuple[float, str], Dict[str, float]], greeks), index_price, currency, oi_by_instrument=open_interest)
        flow = calculator.calculate()
        check = calculator.coverage_report(open_interest)
        exposure = compute_exposure(instruments, index_price, chain_captured_at, currency)
        open_interest_by_strike = {row["strike"]: row for row in exposure["strikes"]}

        rows = [StrikeLevelRow(
            strike=strike, dealer_net_calls=data["dealer_net_c"], dealer_net_puts=data["dealer_net_p"],
            flow_gex=data["inferred_gex"], open_interest_gex=open_interest_by_strike.get(strike, {}).get("net_gex"),
            call_open_interest=open_interest_by_strike.get(strike, {}).get("call_open_interest"),
            put_open_interest=open_interest_by_strike.get(strike, {}).get("put_open_interest"))
            for strike, data in sorted(flow["strike_data"].items())]
        key_levels = flow["key_levels"]
        open_interest_levels = exposure["levels"]
        excluded = check["legs_excluded_no_oi"]
        seen = check["n_strikes"] + excluded
        exclusion_rate = excluded / seen if seen else 0.0

        reasons = []
        if not flow["spot_price_valid"]:
            reasons.append("no valid index price")
        if not flow_rows:
            reasons.append("no stored trades for this expiry")
        if coverage < COVERAGE_GATE:
            reasons.append(f"trade coverage {coverage:.1%} is below {COVERAGE_GATE:.0%}")
        if check["violation_rate"] > VIOLATION_GATE:
            reasons.append(f"{check['violation_rate']:.1%} of options exceed their open interest (limit {VIOLATION_GATE:.0%})")
        if (check["n_strikes"] == 0 and excluded > 0) or exclusion_rate > MAX_EXCLUSION_RATE:
            reasons.append(f"{excluded} of {seen} traded options have no open interest to check against")
        notes = []
        if first_trade_ms is not None and first_trade_ms < stable_ms:
            notes.append(f"trade history is only complete from {COVERAGE_STABLE_DATE:%Y-%m-%d}, so the flow starts there, "
                         "not at the listing of the expiry; earlier trades are missing from the dealer position")
        if flow["stale_strikes"]:
            notes.append(f"{len(flow['stale_strikes'])} traded options are no longer in the chain and were left out")
        if flow["instruments_missing_gamma"]:
            notes.append(f"{flow['instruments_missing_gamma']} options have no gamma and count as zero")

        return DealerFlowLevels(
            currency=currency, expiry=expiry, index_price=index_price, chain_captured_at=chain_captured_at,
            flow_start=datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc),
            trades_used=sum(row.get("trade_count", 0) for row in flow_rows), rows=rows,
            flow_call_wall=key_levels["call_resistance"]["strike"] if key_levels["call_resistance"] else None,
            flow_put_wall=key_levels["put_support"]["strike"] if key_levels["put_support"] else None,
            flow_gamma_flip=key_levels["hvl"],
            open_interest_call_wall=open_interest_levels["call_resistance"]["net_gex_argmax_positive"],
            open_interest_put_wall=open_interest_levels["put_support"]["net_gex_argmin_negative"],
            open_interest_gamma_flip=open_interest_levels["hvl"]["cumulative_sign_change_existing"],
            total_flow_gex=flow["total_inferred_gex"], total_open_interest_gex=exposure["total_net_gex"],
            coverage=coverage, violation_rate=check["violation_rate"], violations=check["n_violations"],
            options_checked=check["n_strikes"], options_without_open_interest=excluded,
            gate_passed=not reasons, gate_reason="; ".join(reasons) or None, notes=notes)
