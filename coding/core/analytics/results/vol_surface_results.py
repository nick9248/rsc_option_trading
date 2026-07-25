"""
Result models for per-expiry volatility surface analysis.

Frozen dataclasses per refactor_design_spec.md section 2.4. Mirror the dict
shape historically produced by ``VolatilitySurfaceCalculator.calculate()``.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class IvByStrikeRow:
    """Mark IV for one instrument (one strike/option_type pair)."""

    strike: float
    option_type: str
    mark_iv: float
    delta: Optional[float]
    moneyness_pct: float


@dataclass(frozen=True)
class SkewResult:
    """25-delta put/call skew."""

    put_25d_iv: Optional[float]
    call_25d_iv: Optional[float]
    put_25d_strike: Optional[float]
    call_25d_strike: Optional[float]
    skew: Optional[float]
    interpretation: str


@dataclass(frozen=True)
class MoneynessBucket:
    """P/C open interest for one moneyness bucket (ATM, near-OTM, far-OTM)."""

    call_oi: float
    put_oi: float
    range_label: str  # "±5%" | "5-15%" | "15%+"
    ratio: float  # ALWAYS present (H2 fix); 0.0 when undefined
    bias: str  # ALWAYS present; "N/A" when undefined


@dataclass(frozen=True)
class PutCallByMoneyness:
    """P/C ratio broken out by moneyness bucket."""

    atm: MoneynessBucket
    near_otm: MoneynessBucket
    far_otm: MoneynessBucket


@dataclass(frozen=True)
class SecondOrderGreeks:
    """Aggregated second-order Greeks (Vanna, Charm)."""

    net_vanna: float
    net_charm: float
    vanna_signal: str
    charm_signal: str
    skipped_instruments: int  # M5: replaces the silent `except: continue`


@dataclass(frozen=True)
class VolSurfaceResult:
    """Full volatility surface result for one expiration."""

    expiration: str
    spot_price: float
    iv_by_strike: Tuple[IvByStrikeRow, ...]
    skew_25d: SkewResult
    pc_by_moneyness: PutCallByMoneyness
    second_order_greeks: SecondOrderGreeks
    atm_iv: Optional[float]
    vwap_iv: Optional[float]
    mark_iv_average: Optional[float]

    def to_dict(self) -> Dict[str, Any]:
        """
        Reproduce the legacy keys read by
        ``volatility_reconstruction_service``: ``skew_25d``,
        ``second_order_greeks``, ``pc_by_moneyness``, ``atm_iv``.
        """

        def _bucket_dict(bucket: MoneynessBucket) -> Dict[str, Any]:
            return {
                "call_oi": bucket.call_oi,
                "put_oi": bucket.put_oi,
                "range": bucket.range_label,
                "ratio": bucket.ratio,
                "bias": bucket.bias,
            }

        return {
            "expiration": self.expiration,
            "spot_price": self.spot_price,
            "skew_25d": {
                "put_25d_iv": self.skew_25d.put_25d_iv,
                "call_25d_iv": self.skew_25d.call_25d_iv,
                "put_25d_strike": self.skew_25d.put_25d_strike,
                "call_25d_strike": self.skew_25d.call_25d_strike,
                "skew": self.skew_25d.skew,
                "interpretation": self.skew_25d.interpretation,
            },
            "pc_by_moneyness": {
                "atm": _bucket_dict(self.pc_by_moneyness.atm),
                "near_otm": _bucket_dict(self.pc_by_moneyness.near_otm),
                "far_otm": _bucket_dict(self.pc_by_moneyness.far_otm),
            },
            "second_order_greeks": {
                "net_vanna": self.second_order_greeks.net_vanna,
                "net_charm": self.second_order_greeks.net_charm,
                "vanna_signal": self.second_order_greeks.vanna_signal,
                "charm_signal": self.second_order_greeks.charm_signal,
                "skipped_instruments": self.second_order_greeks.skipped_instruments,
            },
            "atm_iv": self.atm_iv,
            "vwap_iv": self.vwap_iv,
            "mark_iv_average": self.mark_iv_average,
        }
