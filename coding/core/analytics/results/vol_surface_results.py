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
    """25-delta put/call skew.

    NOTE: bugfix_spec.md Item 9 (sub-task 3, a separate commit) re-signs
    and renames this to the market "risk reversal" convention -- untouched
    here (this commit is Item 8 / sign-convention unification for GEX/DEX
    and vanna/charm, plus Item 12's charm docstring fix; scoped separately
    per the task brief).
    """

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
    """
    Aggregated second-order Greeks (Vanna, Charm).

    bugfix_spec.md Item 8: ``net_vanna``/``net_charm`` (the un-split raw
    sum -- pure arithmetic, no positioning assumption) are renamed to
    ``vanna_exposure_holder``/``charm_exposure_holder`` -- the "holder"
    name they should always have had (see ``GexDexCalculator``'s class
    docstring for the one convention this refers back to).
    ``dealer_vanna_exposure``/``dealer_charm_exposure`` (the negation) are
    new -- ``vanna_signal``/``charm_signal`` are now derived from these,
    not the holder sum (the pre-Item-8 defect: the printed dealer
    narrative was keyed off the holder-side number, backwards).
    """

    vanna_exposure_holder: float
    charm_exposure_holder: float
    vanna_signal: str
    charm_signal: str
    skipped_instruments: int  # M5: replaces the silent `except: continue`

    # --- Additive fields (bugfix_spec.md Item 8) ---
    dealer_vanna_exposure: Optional[float] = None
    """-vanna_exposure_holder -- the assumed-dealer view."""

    dealer_charm_exposure: Optional[float] = None
    """-charm_exposure_holder -- the assumed-dealer view."""

    def __post_init__(self) -> None:
        if self.dealer_vanna_exposure is None:
            object.__setattr__(self, "dealer_vanna_exposure", -self.vanna_exposure_holder)
        if self.dealer_charm_exposure is None:
            object.__setattr__(self, "dealer_charm_exposure", -self.charm_exposure_holder)


@dataclass(frozen=True)
class VolSurfaceResult:
    """
    Full volatility surface result for one expiration.

    ``vwap_iv``, ``mark_iv_average`` (bugfix_spec.md Item 3: the volume-
    weighted MATCHED mark-IV baseline, not a chain-wide average — see the
    calculator's docstring), and ``traded_instrument_count`` are typed-only
    fields: the legacy ``calculate()`` dict never carried VWAP data (it lived
    only as ephemeral instance state, read as attributes by the live render
    path, ``reporting/vol_surface_formatter.py``'s ``format_vol_surface_
    section`` -- task A7 review: this comment previously named the deleted
    ``generate_report_section`` as the consumer), so ``to_dict()``
    deliberately omits them to stay a byte-faithful legacy shim. Read them
    as attributes.
    """

    expiration: str
    spot_price: float
    iv_by_strike: Tuple[IvByStrikeRow, ...]  # one row per INSTRUMENT
    skew_25d: SkewResult
    pc_by_moneyness: PutCallByMoneyness
    second_order_greeks: SecondOrderGreeks
    atm_iv: Optional[float]
    vwap_iv: Optional[float]
    mark_iv_average: Optional[float]
    traded_instrument_count: int

    def merged_iv_by_strike(self) -> Dict[float, Dict[str, Optional[float]]]:
        """
        Group the per-instrument ``iv_by_strike`` rows into the legacy
        per-strike ``{strike: {call_iv, put_iv}}`` shape (one call_iv/put_iv
        pair per strike, not per instrument). Sorted by strike ascending.

        Moved here from ``reporting/vol_surface_formatter.py`` (A3-review
        carried finding): this grouping is calculator/model-layer logic, not
        a formatting concern — the formatter is now a pure consumer of this
        method.
        """
        merged: Dict[float, Dict[str, Optional[float]]] = {}
        for row in self.iv_by_strike:
            entry = merged.setdefault(row.strike, {"call_iv": None, "put_iv": None})
            if row.option_type == "C":
                entry["call_iv"] = row.mark_iv
            else:
                entry["put_iv"] = row.mark_iv
        return dict(sorted(merged.items()))

    def to_dict(self) -> Dict[str, Any]:
        """
        Reproduce the legacy ``VolatilitySurfaceCalculator.calculate()`` dict
        shape exactly: ``iv_by_strike`` (merged per-strike), ``skew_25d``,
        ``pc_by_moneyness``, ``second_order_greeks`` (the original 4 keys —
        no ``skipped_instruments``), ``atm_iv``. Consumed by
        ``volatility_reconstruction_service`` and
        ``on_chain_analysis_service`` (``analyzer.volatility_surface_structured``).
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
            "iv_by_strike": [
                {"strike": strike, "call_iv": ivs["call_iv"], "put_iv": ivs["put_iv"]}
                for strike, ivs in self.merged_iv_by_strike().items()
            ],
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
                # bugfix_spec.md Item 8: net_vanna/net_charm (the un-split
                # holder-side sum) renamed to vanna_exposure_holder/
                # charm_exposure_holder; dealer_* added.
                "vanna_exposure_holder": self.second_order_greeks.vanna_exposure_holder,
                "charm_exposure_holder": self.second_order_greeks.charm_exposure_holder,
                "dealer_vanna_exposure": self.second_order_greeks.dealer_vanna_exposure,
                "dealer_charm_exposure": self.second_order_greeks.dealer_charm_exposure,
                "vanna_signal": self.second_order_greeks.vanna_signal,
                "charm_signal": self.second_order_greeks.charm_signal,
            },
            "atm_iv": self.atm_iv,
        }
