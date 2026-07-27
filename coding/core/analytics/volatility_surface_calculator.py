"""
Volatility surface analysis calculator.

Computes per-expiry volatility metrics:
- IV by strike (smile/skew visualization)
- 25-delta skew
- P/C ratio by moneyness bucket
- Vanna/Charm (second-order Greeks)
- VWAP IV vs mark IV
"""

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

from coding.core.analytics.black_scholes_calculator import BlackScholesCalculator
from coding.core.analytics.results.vol_surface_results import (
    IvByStrikeRow,
    MoneynessBucket,
    PutCallByMoneyness,
    SecondOrderGreeks,
    SkewResult,
    VolSurfaceResult,
)
from coding.core.analytics.thresholds import interpret_put_call_ratio

logger = logging.getLogger(__name__)
_bs = BlackScholesCalculator()

# bugfix_spec.md Item 3 — the aggression residual (VWAP - matched mark IV
# baseline) is now correctly ~±1 vol point (previously it conflated smile
# shape with aggression and swung ±10-15 points), so this label threshold is
# doing real work. Named per F3.3.
VWAP_AGGRESSION_THRESHOLD_POINTS = 1.0

# Below this many distinct traded instruments, the matched baseline is
# computed from too thin a sample to support a directional aggression label
# (a single traded instrument sets VWAP == baseline mix trivially). The raw
# numbers are still shown; only the label is suppressed.
# bugfix_spec.md Item 3 F3.3's illustrative code snippet shows this constant
# as 3, but its own acceptance tests (T3.2 vs T3.4, section 3.5) require 2:
# n=2 must render the normal label ("Balanced"), n=1 must suppress it. The
# acceptance tests' hand-computed expectations govern (task brief: used
# verbatim) over the snippet, so this is 2, not 3 — noted as a spec
# inconsistency in the Task A4 report.
MINIMUM_TRADED_INSTRUMENTS_FOR_AGGRESSION = 2


class VolatilitySurfaceCalculator:
    """
    Calculate volatility surface metrics from enriched instrument data.

    Uses instrument-level mark_iv, delta, gamma, theta, vega
    (already fetched during GEX/DEX phase).
    """

    def __init__(
        self,
        instruments: List[Dict[str, Any]],
        spot_price: float,
        expiration: str,
    ):
        """
        Initialize with enriched instruments for a single expiration.

        Args:
            instruments: List of instrument dicts with mark_iv, delta, gamma,
                        theta, vega, strike, option_type, open_interest.
            spot_price: Current underlying spot price.
            expiration: Expiration date string.
        """
        self.instruments = instruments
        self.spot_price = spot_price
        self.expiration = expiration
        self._vwap_iv: Optional[float] = None
        self._mark_iv_baseline: Optional[float] = None
        self._traded_instrument_count: int = 0

    def calculate(self) -> VolSurfaceResult:
        """
        Run all volatility surface calculations.

        Returns:
            VolSurfaceResult (coding/core/analytics/results/vol_surface_results.py).
            Call ``.to_dict()`` for the legacy dict shape.
        """
        iv_by_strike_rows = self._calculate_iv_by_strike_rows()
        skew_dict = self._calculate_25_delta_skew()
        pc_dict = self._calculate_pc_by_moneyness()
        second_order_dict = self._calculate_second_order_greeks()
        atm_iv = self._calculate_atm_iv()

        def _bucket(name: str) -> MoneynessBucket:
            b = pc_dict[name]
            return MoneynessBucket(
                call_oi=b["call_oi"], put_oi=b["put_oi"], range_label=b["range"],
                ratio=b["ratio"], bias=b["bias"],
            )

        return VolSurfaceResult(
            expiration=self.expiration,
            spot_price=self.spot_price,
            iv_by_strike=tuple(iv_by_strike_rows),
            skew_25d=SkewResult(
                put_25d_iv=skew_dict["put_25d_iv"],
                call_25d_iv=skew_dict["call_25d_iv"],
                put_25d_strike=skew_dict.get("put_25d_strike"),
                call_25d_strike=skew_dict.get("call_25d_strike"),
                skew=skew_dict["skew"],
                interpretation=skew_dict["interpretation"],
            ),
            pc_by_moneyness=PutCallByMoneyness(
                atm=_bucket("atm"), near_otm=_bucket("near_otm"), far_otm=_bucket("far_otm"),
            ),
            second_order_greeks=SecondOrderGreeks(
                net_vanna=second_order_dict["net_vanna"],
                net_charm=second_order_dict["net_charm"],
                vanna_signal=second_order_dict["vanna_signal"],
                charm_signal=second_order_dict["charm_signal"],
                skipped_instruments=second_order_dict["skipped_instruments"],
            ),
            atm_iv=atm_iv,
            vwap_iv=self._vwap_iv,
            mark_iv_average=self._mark_iv_baseline,
            traded_instrument_count=self._traded_instrument_count,
        )

    def _calculate_iv_by_strike_rows(self) -> List[IvByStrikeRow]:
        """
        Build one IvByStrikeRow per instrument with a usable mark_iv.

        The legacy calculate() dict merged call_iv/put_iv per strike; that
        merge is now owned by ``VolSurfaceResult.merged_iv_by_strike()``
        (moved out of the formatter — A3-review carried finding) and by
        ``VolSurfaceResult.to_dict()`` for the legacy shim.

        Returns:
            List sorted by strike ascending, matching the legacy table order.
        """
        rows: List[IvByStrikeRow] = []
        for inst in self.instruments:
            mark_iv = inst.get("mark_iv")
            if mark_iv is None:
                continue

            strike = inst["strike"]
            moneyness_pct = (
                abs(strike - self.spot_price) / self.spot_price * 100.0
                if self.spot_price > 0 else 0.0
            )
            rows.append(IvByStrikeRow(
                strike=strike,
                option_type=inst["option_type"],
                mark_iv=mark_iv,
                delta=inst.get("delta"),
                moneyness_pct=moneyness_pct,
            ))

        rows.sort(key=lambda r: (r.strike, r.option_type))
        return rows

    def _calculate_25_delta_skew(self) -> Dict[str, Any]:
        """
        Calculate 25-delta skew: 25d Put IV - 25d Call IV.

        Positive skew = puts more expensive (hedging demand).
        Negative skew = calls more expensive (upside speculation).

        Returns:
            Dict with put_25d_iv, call_25d_iv, skew, interpretation.
        """
        # Find instruments closest to ±0.25 delta
        puts = [i for i in self.instruments if i["option_type"] == "P" and i.get("delta") is not None]
        calls = [i for i in self.instruments if i["option_type"] == "C" and i.get("delta") is not None]

        put_25d = self._find_closest_delta(puts, -0.25)
        call_25d = self._find_closest_delta(calls, 0.25)

        if put_25d is None or call_25d is None:
            return {
                "put_25d_iv": None,
                "call_25d_iv": None,
                "skew": None,
                "interpretation": "Insufficient data",
            }

        put_iv = put_25d.get("mark_iv", 0)
        call_iv = call_25d.get("mark_iv", 0)
        skew = put_iv - call_iv

        if skew > 5:
            interpretation = "Puts More Expensive - Strong Hedging Demand"
        elif skew > 1:
            interpretation = "Puts More Expensive - Hedging Demand"
        elif skew > -1:
            interpretation = "Balanced"
        elif skew > -5:
            interpretation = "Calls More Expensive - Upside Speculation"
        else:
            interpretation = "Calls More Expensive - Strong Upside Speculation"

        return {
            "put_25d_iv": put_iv,
            "call_25d_iv": call_iv,
            "put_25d_strike": put_25d["strike"],
            "call_25d_strike": call_25d["strike"],
            "skew": skew,
            "interpretation": interpretation,
        }

    def _find_closest_delta(
        self,
        instruments: List[Dict],
        target_delta: float
    ) -> Optional[Dict]:
        """Find instrument with delta closest to target."""
        if not instruments:
            return None

        valid = [i for i in instruments if i.get("delta") is not None and i.get("mark_iv") is not None]
        if not valid:
            return None

        return min(valid, key=lambda i: abs(i["delta"] - target_delta))

    def _calculate_pc_by_moneyness(self) -> Dict[str, Any]:
        """
        Calculate P/C ratio split by moneyness buckets.

        Buckets:
        - ATM: within ±5% of spot
        - Near-OTM: 5-15% from spot
        - Far-OTM: >15% from spot

        Returns:
            Dict with per-bucket call_oi, put_oi, ratio, bias.
        """
        buckets = {
            "atm": {"call_oi": 0, "put_oi": 0, "range": "±5%"},
            "near_otm": {"call_oi": 0, "put_oi": 0, "range": "5-15%"},
            "far_otm": {"call_oi": 0, "put_oi": 0, "range": "15%+"},
        }

        if self.spot_price <= 0:
            # Distance-to-spot is undefined with no spot price, so no OI can be
            # bucketed. Still populate ratio/bias on every bucket - the report
            # path (generate_report_section) unconditionally reads both keys
            # and would KeyError otherwise.
            for bucket_data in buckets.values():
                bucket_data["ratio"] = 0.0
                bucket_data["bias"] = "N/A"
            return buckets

        for inst in self.instruments:
            strike = inst["strike"]
            oi = inst.get("open_interest", 0)
            option_type = inst["option_type"]

            distance_pct = abs(strike - self.spot_price) / self.spot_price * 100

            if distance_pct <= 5:
                bucket = "atm"
            elif distance_pct <= 15:
                bucket = "near_otm"
            else:
                bucket = "far_otm"

            if option_type == "C":
                buckets[bucket]["call_oi"] += oi
            else:
                buckets[bucket]["put_oi"] += oi

        # Calculate ratios and bias per bucket
        for bucket_data in buckets.values():
            call_oi = bucket_data["call_oi"]
            put_oi = bucket_data["put_oi"]

            if call_oi > 0:
                ratio = put_oi / call_oi
            else:
                ratio = float("inf") if put_oi > 0 else 0

            bucket_data["ratio"] = ratio
            # M4 (code_quality_review.md): shared interpreter, unifying
            # this bucket-level bias with OnChainMetricsCalculator.
            # calculate_put_call_ratio's whole-expiration bias
            # (refactor_design_spec.md T12 -- planned golden delta).
            bucket_data["bias"] = interpret_put_call_ratio(ratio)

        return buckets

    def _calculate_second_order_greeks(self) -> Dict[str, Any]:
        """
        Calculate aggregated second-order Greeks (Vanna, Charm).

        Closed-form Black-Scholes formulas (r=q=0, standard for crypto):
        - Vanna = −φ(d1) × d2 / σ  (∂Δ/∂σ, sensitivity of delta to vol)
        - Charm = φ(d1) × d2 / (2τ) (∂Δ/∂τ, time decay of delta)

        d1 is recovered from stored delta via inverse normal CDF.
        τ is derived from stored gamma and vega without needing expiry date:
          gamma = φ(d1)/(S·σ·√τ),  vega = S·φ(d1)·√τ/100
          → τ = (vega × 100) / (S² × gamma × sigma)

        Results are aggregated across all instruments, weighted by OI.

        Returns:
            Dict with net_vanna, net_charm, vanna_signal, charm_signal,
            skipped_instruments (count of instruments with oi > 0 that were
            excluded from the sum for any reason — missing greeks, invalid
            derived tau, or a raised exception; VolSurfaceResult.
            second_order_greeks.skipped_instruments, M5).
        """
        net_vanna = 0.0
        net_charm = 0.0
        skipped_instruments = 0

        for inst in self.instruments:
            delta = inst.get("delta")
            gamma = inst.get("gamma")
            vega = inst.get("vega")
            mark_iv = inst.get("mark_iv")
            option_type = inst.get("option_type")
            oi = inst.get("open_interest", 0)

            if oi <= 0:
                continue
            if None in (delta, gamma, vega, mark_iv, option_type):
                skipped_instruments += 1
                continue

            try:
                sigma = float(mark_iv) / 100.0
                gamma_f = float(gamma)
                vega_f = float(vega)
                if sigma <= 0 or gamma_f <= 0 or vega_f <= 0 or self.spot_price <= 0:
                    skipped_instruments += 1
                    continue

                # Derive time-to-expiry from stored greeks (no expiry date needed)
                raw_vega = vega_f * 100.0  # S·φ(d1)·√τ (undo /100 convention)
                tau = raw_vega / (self.spot_price ** 2 * gamma_f * sigma)
                if not (0 < tau <= 2.0):
                    skipped_instruments += 1
                    continue

                d1 = _bs.d1_from_delta(float(delta), option_type)
                d2 = d1 - sigma * math.sqrt(tau)

                net_vanna += _bs.calculate_vanna(d1, d2, sigma) * float(oi)
                net_charm += _bs.calculate_charm(d1, d2, tau) * float(oi)

            except Exception:
                skipped_instruments += 1
                continue

        # Interpret signals
        if net_vanna > 0:
            vanna_signal = "IV drop → dealers buy underlying (bullish)"
        else:
            vanna_signal = "IV drop → dealers sell underlying (bearish)"

        if net_charm > 0:
            charm_signal = "Time decay pushing delta positive (bullish drift)"
        else:
            charm_signal = "Time decay pushing delta negative (bearish drift)"

        return {
            "net_vanna": net_vanna,
            "net_charm": net_charm,
            "vanna_signal": vanna_signal,
            "charm_signal": charm_signal,
            "skipped_instruments": skipped_instruments,
        }

    def _calculate_atm_iv(self) -> Optional[float]:
        """
        Calculate ATM IV as the average of the closest call and put IVs to spot.

        Returns:
            ATM IV as percentage, or None if insufficient data.
        """
        calls = [i for i in self.instruments if i["option_type"] == "C" and i.get("mark_iv") is not None]
        puts = [i for i in self.instruments if i["option_type"] == "P" and i.get("mark_iv") is not None]

        if not calls and not puts:
            return None

        atm_ivs = []
        for group in [calls, puts]:
            if group:
                closest = min(group, key=lambda i: abs(i["strike"] - self.spot_price))
                atm_ivs.append(closest["mark_iv"])

        return sum(atm_ivs) / len(atm_ivs) if atm_ivs else None

    def set_vwap_iv_data(
        self,
        vwap_iv: Optional[float],
        mark_iv_baseline: Optional[float],
        traded_instrument_count: int = 0,
    ) -> None:
        """
        Store VWAP IV data for inclusion in the next calculate().

        Args:
            vwap_iv: Volume-weighted IV of actual trades.
            mark_iv_baseline: Volume-weighted MARK IV of the SAME instruments
                that traded, weighted by the SAME traded volumes (the
                "matched baseline" — bugfix_spec.md Item 3). Historically
                named ``mark_iv_avg``; callers that have not yet been updated
                to compute the matched baseline may still pass a chain-wide
                average here, but the report gates on
                ``traded_instrument_count`` either way.
            traded_instrument_count: Number of distinct instruments that
                contributed to both legs above. 0 (the default) means the
                aggression signal is suppressed in the report.
        """
        self._vwap_iv = vwap_iv
        self._mark_iv_baseline = mark_iv_baseline
        self._traded_instrument_count = traded_instrument_count

    def generate_report_section(self, result: Optional[VolSurfaceResult] = None) -> str:
        """
        Generate formatted volatility surface report section.

        Args:
            result: Pre-computed result from calculate(). If None, calculate() is called.
                    Pass a pre-computed result to avoid calling calculate() twice.

        Returns:
            Formatted string for inclusion in analysis report.
        """
        if result is None:
            result = self.calculate()
        lines = []
        sub_separator = "-" * 80

        lines.append("VOLATILITY SURFACE ANALYSIS")
        lines.append(sub_separator)

        # 25-Delta Skew
        skew = result.skew_25d
        if skew.skew is not None:
            lines.append(
                f"25-Delta Skew: {skew.skew:+.1f}% ({skew.interpretation})"
            )
            lines.append(
                f"  25d Put: {skew.put_25d_iv:.1f}% (K={skew.put_25d_strike:,.0f})  |  "
                f"25d Call: {skew.call_25d_iv:.1f}% (K={skew.call_25d_strike:,.0f})"
            )
        else:
            lines.append(f"25-Delta Skew: {skew.interpretation}")
        lines.append("")

        # ATM IV
        atm_iv = result.atm_iv
        if atm_iv is not None:
            lines.append(f"ATM IV: {atm_iv:.1f}%")
            lines.append("")

        # VWAP IV vs the matched (volume-weighted, same-instruments) mark IV
        # baseline (if available) — bugfix_spec.md Item 3.
        vwap_iv = result.vwap_iv
        mark_iv_baseline = result.mark_iv_average
        if vwap_iv is not None and mark_iv_baseline is not None:
            if result.traded_instrument_count < MINIMUM_TRADED_INSTRUMENTS_FOR_AGGRESSION:
                lines.append(
                    f"VWAP IV: {vwap_iv:.1f}%  |  Matched Mark IV: {mark_iv_baseline:.1f}%  "
                    f"(only {result.traded_instrument_count} instrument(s) traded - "
                    f"aggression signal suppressed)"
                )
            else:
                diff = vwap_iv - mark_iv_baseline
                if diff > VWAP_AGGRESSION_THRESHOLD_POINTS:
                    aggression = "Buyers aggressive (VWAP > Mark)"
                elif diff < -VWAP_AGGRESSION_THRESHOLD_POINTS:
                    aggression = "Sellers aggressive (VWAP < Mark)"
                else:
                    aggression = "Balanced"
                lines.append(
                    f"VWAP IV: {vwap_iv:.1f}%  |  Matched Mark IV: {mark_iv_baseline:.1f}%  "
                    f"|  Diff: {diff:+.1f}%  ({result.traded_instrument_count} instruments)"
                )
                lines.append(f"  {aggression}")
            lines.append("")

        # IV by Strike (show most relevant strikes around spot)
        merged_iv = result.merged_iv_by_strike()
        if merged_iv:
            lines.append("IV BY STRIKE:")
            lines.append(f"  {'Strike':>10}  {'Call IV':>10}  {'Put IV':>10}")
            lines.append(f"  {'------':>10}  {'-------':>10}  {'------':>10}")

            # Filter to ±30% of spot for readability
            for strike, ivs in merged_iv.items():
                if self.spot_price > 0:
                    distance = abs(strike - self.spot_price) / self.spot_price
                    if distance > 0.30:
                        continue

                call_iv = f"{ivs['call_iv']:.1f}%" if ivs["call_iv"] is not None else "   -"
                put_iv = f"{ivs['put_iv']:.1f}%" if ivs["put_iv"] is not None else "   -"
                lines.append(f"  {strike:>10,.0f}  {call_iv:>10}  {put_iv:>10}")
            lines.append("")

        # P/C by Moneyness
        pc = result.pc_by_moneyness
        lines.append("P/C RATIO BY MONEYNESS:")
        for bucket, label in [(pc.atm, "ATM"), (pc.near_otm, "Near-OTM"), (pc.far_otm, "Far-OTM")]:
            rng = bucket.range_label
            ratio = bucket.ratio
            bias = bucket.bias

            if ratio == float("inf"):
                ratio_str = "N/A (No Call OI)"
            else:
                ratio_str = f"P/C = {ratio:.2f} ({bias})"

            lines.append(f"  {label} ({rng}):{'':>5}{ratio_str}")
        lines.append("")

        # Second-Order Greeks
        second = result.second_order_greeks
        lines.append("SECOND-ORDER GREEKS:")
        lines.append(f"  Net Vanna Exposure: {second.net_vanna:+.6f}")
        lines.append(f"  Net Charm Exposure: {second.net_charm:+.6f}")
        lines.append(f"  Vanna Signal: {second.vanna_signal}")
        lines.append(f"  Charm Signal: {second.charm_signal}")
        lines.append("")

        return "\n".join(lines)
