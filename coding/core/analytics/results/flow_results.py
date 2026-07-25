"""
Result models for buy/sell trade-flow analysis.

Frozen dataclasses per refactor_design_spec.md section 2.3. Mirror the dict
shape historically produced by ``BuySellFlowAnalyzer.calculate()``.

M7 note: ``buy_sell_ratio`` uses a single ``None`` sentinel for the
"undefined" case (replacing the legacy ``float("inf")``). This is an
intentional, gated behavior change scheduled for T5 — the model carries the
new semantics from the start, but nothing in T2/T3 wires a calculator to
produce it yet.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class StrikeFlowEntry:
    """Buy/sell flow stats for one (strike, option_type) bucket."""

    buy_count: float
    sell_count: float
    buy_volume: float
    sell_volume: float
    buy_notional: float
    sell_notional: float
    net_flow: float
    buy_sell_ratio: Optional[float]  # None when sell_volume == 0 (M7)


@dataclass(frozen=True)
class FlowTotals:
    """Expiration-level buy/sell volume totals, split by option type."""

    call_buy_volume: float
    call_sell_volume: float
    put_buy_volume: float
    put_sell_volume: float


@dataclass(frozen=True)
class TopStrikeEntry:
    """One row in the top-buying or top-selling strikes list."""

    strike: float
    option_type: str  # "C" | "P"
    net_flow: float
    volume: float  # buy_volume for the buy list, sell_volume for the sell list
    notional: float  # buy_notional for the buy list, sell_notional for the sell list


@dataclass(frozen=True)
class FlowResult:
    """Full buy/sell flow result for one expiration."""

    flow_data: Dict[float, Dict[str, StrikeFlowEntry]]  # strike -> {"C"|"P" -> entry}
    expiration_totals: FlowTotals
    bias_interpretation: str
    flow_trend: str
    top_buy_strikes: Tuple[TopStrikeEntry, ...]
    top_sell_strikes: Tuple[TopStrikeEntry, ...]
    trade_count: int
    spot_price: float
    window_start_ms: int
    window_end_ms: int
    lookback_hours: float  # derived from window, not assumed

    def to_dict(self) -> Dict[str, Any]:
        """
        Reproduce the legacy ``BuySellFlowAnalyzer.calculate()`` dict shape.
        ``flow_data`` is consumed by
        ``chart_generator.generate_flow_distribution_chart`` /
        ``generate_net_flow_chart`` and ``repository.save_flow_metrics``.
        """
        flow_data: Dict[float, Dict[str, Dict[str, Any]]] = {
            strike: {
                option_type: {
                    "buy_count": entry.buy_count,
                    "sell_count": entry.sell_count,
                    "buy_volume": entry.buy_volume,
                    "sell_volume": entry.sell_volume,
                    "buy_notional": entry.buy_notional,
                    "sell_notional": entry.sell_notional,
                    "net_flow": entry.net_flow,
                    "buy_sell_ratio": entry.buy_sell_ratio,
                }
                for option_type, entry in by_type.items()
            }
            for strike, by_type in self.flow_data.items()
        }

        return {
            "flow_data": flow_data,
            "expiration_totals": {
                "call_buy_volume": self.expiration_totals.call_buy_volume,
                "call_sell_volume": self.expiration_totals.call_sell_volume,
                "put_buy_volume": self.expiration_totals.put_buy_volume,
                "put_sell_volume": self.expiration_totals.put_sell_volume,
            },
            "bias_interpretation": self.bias_interpretation,
            "flow_trend": self.flow_trend,
            "top_buy_strikes": [
                {
                    "strike": e.strike,
                    "option_type": e.option_type,
                    "net_flow": e.net_flow,
                    "buy_volume": e.volume,
                    "buy_notional": e.notional,
                }
                for e in self.top_buy_strikes
            ],
            "top_sell_strikes": [
                {
                    "strike": e.strike,
                    "option_type": e.option_type,
                    "net_flow": e.net_flow,
                    "sell_volume": e.volume,
                    "sell_notional": e.notional,
                }
                for e in self.top_sell_strikes
            ],
            "trade_count": self.trade_count,
            "spot_price": self.spot_price,
        }
