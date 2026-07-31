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
    """
    25-delta risk reversal / put-call skew.

    bugfix_spec.md Item 9: ``skew`` (put IV - call IV, a non-standard,
    unqualified sign) is replaced by ``risk_reversal_25d`` (call IV -
    put IV -- the market "25-delta risk reversal" convention: SpotGamma,
    MenthorQ, Glassnode's ``options.25DeltaSkewCallPutAll``), the new
    PRIMARY field, and ``put_over_call_skew_25d`` (the legacy sign, now
    explicitly named instead of unqualified). Positive
    ``risk_reversal_25d`` -> calls richer (bullish/upside speculation);
    negative -> puts richer (bearish/downside hedging demand) -- the
    opposite sign convention from the old ``skew``. ``skew`` is kept for
    one release as a read-only alias of ``put_over_call_skew_25d`` (same
    value, unchanged meaning) for any un-migrated reader.
    """

    put_25d_iv: Optional[float]
    call_25d_iv: Optional[float]
    put_25d_strike: Optional[float]
    call_25d_strike: Optional[float]
    interpretation: str

    # --- Additive/renamed fields (bugfix_spec.md Item 9) ---
    risk_reversal_25d: Optional[float] = None
    """call_25d_iv - put_25d_iv -- PRIMARY, market convention."""

    put_over_call_skew_25d: Optional[float] = None
    """put_25d_iv - call_25d_iv -- legacy sign, explicitly named (was the
    unqualified ``skew`` field)."""

    put_25d_delta: Optional[float] = None
    """The ACTUAL delta of the selected 25d put (bugfix_spec.md 9.4 edge
    case: a thin book's "closest to -0.25" pick may not be close at all --
    this makes that visible)."""

    call_25d_delta: Optional[float] = None
    """The ACTUAL delta of the selected 25d call (see put_25d_delta)."""

    def __post_init__(self) -> None:
        # Both None (insufficient data, bugfix_spec.md 9.4) stays as-is --
        # no derivation possible or needed.
        if self.risk_reversal_25d is None and self.put_over_call_skew_25d is not None:
            object.__setattr__(self, "risk_reversal_25d", -self.put_over_call_skew_25d)
        if self.put_over_call_skew_25d is None and self.risk_reversal_25d is not None:
            object.__setattr__(self, "put_over_call_skew_25d", -self.risk_reversal_25d)

    @property
    def skew(self) -> Optional[float]:
        """DEPRECATED (bugfix_spec.md Item 9): alias for
        ``put_over_call_skew_25d`` -- kept for one release for any
        un-migrated reader. Use ``risk_reversal_25d`` (market convention)
        or ``put_over_call_skew_25d`` (explicit legacy sign) instead."""
        return self.put_over_call_skew_25d


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
    ``dealer_vanna_exposure``/``dealer_charm_exposure`` are new --
    ``vanna_signal``/``charm_signal`` are derived from these, not the
    holder sum (the pre-Item-8 defect: the printed dealer narrative was
    keyed off the holder-side number, backwards).

    Task C5 review fix round 1 (Important #1) + round 2 (Important, the
    round-1 fix's own regression): ``dealer_vanna_exposure``/
    ``dealer_charm_exposure`` are the call/put-SPLIT assumed-dealer
    convention (+1 call, -1 put -- SqueezeMetrics, matching
    ``GexDexCalculator`` and ``ExposureProfileCalculator``'s per-strike
    VEX/CEX), NOT a blanket negation of the holder-side sum -- negation was
    the pre-round-1-fix convention and only coincides with the split on a
    100%-call or 100%-put book. These two fields are REQUIRED (no default)
    precisely so nothing can silently fall back to the retired negation
    convention the way ``__post_init__``'s old default did -- round 1's fix
    corrected the one real production call site
    (``VolatilitySurfaceCalculator._calculate_second_order_greeks``) but
    left a negation-based default on this shared model, which several test
    fixtures were (silently, incorrectly) relying on. Every construction
    site must now compute and pass its own explicit dealer values.
    """

    vanna_exposure_holder: float
    charm_exposure_holder: float
    vanna_signal: str
    charm_signal: str
    skipped_instruments: int  # M5: replaces the silent `except: continue`

    # --- Additive fields (bugfix_spec.md Item 8) ---
    dealer_vanna_exposure: float
    """Call/put-split assumed-dealer vanna (+1 call, -1 put) -- NOT
    -vanna_exposure_holder (that was the retired convention; see class
    docstring). Required: no default, so a caller must explicitly decide
    what "dealer" means here rather than inherit a hidden fallback."""

    dealer_charm_exposure: float
    """Call/put-split assumed-dealer charm (+1 call, -1 put) -- NOT
    -charm_exposure_holder. See ``dealer_vanna_exposure``'s docstring."""


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
                # bugfix_spec.md Item 9: "skew" (put - call, non-standard
                # sign) replaced by "risk_reversal_25d" (call - put, market
                # convention) and "put_over_call_skew_25d" (the legacy
                # sign, explicitly named).
                "risk_reversal_25d": self.skew_25d.risk_reversal_25d,
                "put_over_call_skew_25d": self.skew_25d.put_over_call_skew_25d,
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
