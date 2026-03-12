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
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


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
        self._mark_iv_avg: Optional[float] = None

    def calculate(self) -> Dict[str, Any]:
        """
        Run all volatility surface calculations.

        Returns:
            Dict with iv_by_strike, skew_25d, pc_by_moneyness,
            second_order_greeks, and atm_iv.
        """
        iv_by_strike = self._calculate_iv_by_strike()
        skew_25d = self._calculate_25_delta_skew()
        pc_by_moneyness = self._calculate_pc_by_moneyness()
        second_order = self._calculate_second_order_greeks()
        atm_iv = self._calculate_atm_iv()

        return {
            "iv_by_strike": iv_by_strike,
            "skew_25d": skew_25d,
            "pc_by_moneyness": pc_by_moneyness,
            "second_order_greeks": second_order,
            "atm_iv": atm_iv,
        }

    def _calculate_iv_by_strike(self) -> List[Dict[str, Any]]:
        """
        Build IV smile table: call IV and put IV per strike.

        Returns:
            Sorted list of dicts with strike, call_iv, put_iv.
        """
        strike_iv: Dict[float, Dict[str, Optional[float]]] = {}

        for inst in self.instruments:
            strike = inst["strike"]
            mark_iv = inst.get("mark_iv")
            option_type = inst["option_type"]

            if mark_iv is None:
                continue

            if strike not in strike_iv:
                strike_iv[strike] = {"call_iv": None, "put_iv": None}

            if option_type == "C":
                strike_iv[strike]["call_iv"] = mark_iv
            else:
                strike_iv[strike]["put_iv"] = mark_iv

        result = []
        for strike in sorted(strike_iv.keys()):
            entry = {"strike": strike}
            entry.update(strike_iv[strike])
            result.append(entry)

        return result

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
            bucket_data["bias"] = self._interpret_pc_ratio(ratio)

        return buckets

    @staticmethod
    def _interpret_pc_ratio(ratio: float) -> str:
        """Interpret P/C ratio into directional bias."""
        if ratio == float("inf"):
            return "N/A"
        if ratio < 0.7:
            return "Bullish"
        elif ratio < 1.0:
            return "Slightly Bullish"
        elif ratio < 1.3:
            return "Slightly Bearish"
        else:
            return "Bearish"

    def _calculate_second_order_greeks(self) -> Dict[str, Any]:
        """
        Calculate aggregated second-order Greeks (Vanna, Charm).

        Approximations:
        - Vanna ≈ vega / spot (sensitivity of delta to vol)
        - Charm ≈ -theta / delta (time decay of delta)

        These are aggregated across all instruments, weighted by OI.

        Returns:
            Dict with net_vanna, net_charm, vanna_signal, charm_signal.
        """
        net_vanna = 0.0
        net_charm = 0.0

        for inst in self.instruments:
            gamma = inst.get("gamma")
            vega = inst.get("vega")
            theta = inst.get("theta")
            oi = inst.get("open_interest", 0)

            if oi <= 0:
                continue

            # Vanna approximation: gamma × vega / spot
            if gamma is not None and vega is not None and self.spot_price > 0:
                vanna = gamma * vega / self.spot_price
                net_vanna += vanna * oi

            # Charm approximation: -gamma × theta
            if gamma is not None and theta is not None:
                charm = -gamma * theta
                net_charm += charm * oi

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
        mark_iv_avg: Optional[float]
    ) -> None:
        """
        Store VWAP IV data for inclusion in report.

        Args:
            vwap_iv: Volume-weighted average IV from actual trades.
            mark_iv_avg: Average mark IV for comparison.
        """
        self._vwap_iv = vwap_iv
        self._mark_iv_avg = mark_iv_avg

    def generate_report_section(self, result: Optional[Dict[str, Any]] = None) -> str:
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
        skew = result["skew_25d"]
        if skew["skew"] is not None:
            lines.append(
                f"25-Delta Skew: {skew['skew']:+.1f}% ({skew['interpretation']})"
            )
            lines.append(
                f"  25d Put: {skew['put_25d_iv']:.1f}% (K={skew['put_25d_strike']:,.0f})  |  "
                f"25d Call: {skew['call_25d_iv']:.1f}% (K={skew['call_25d_strike']:,.0f})"
            )
        else:
            lines.append(f"25-Delta Skew: {skew['interpretation']}")
        lines.append("")

        # ATM IV
        atm_iv = result["atm_iv"]
        if atm_iv is not None:
            lines.append(f"ATM IV: {atm_iv:.1f}%")
            lines.append("")

        # VWAP IV (if available)
        vwap_iv = self._vwap_iv
        mark_iv_avg = self._mark_iv_avg
        if vwap_iv is not None and mark_iv_avg is not None:
            diff = vwap_iv - mark_iv_avg
            if diff > 1:
                aggression = "Buyers aggressive (VWAP > Mark)"
            elif diff < -1:
                aggression = "Sellers aggressive (VWAP < Mark)"
            else:
                aggression = "Balanced"
            lines.append(f"VWAP IV: {vwap_iv:.1f}%  |  Mark IV: {mark_iv_avg:.1f}%  |  Diff: {diff:+.1f}%")
            lines.append(f"  {aggression}")
            lines.append("")

        # IV by Strike (show most relevant strikes around spot)
        iv_data = result["iv_by_strike"]
        if iv_data:
            lines.append("IV BY STRIKE:")
            lines.append(f"  {'Strike':>10}  {'Call IV':>10}  {'Put IV':>10}")
            lines.append(f"  {'------':>10}  {'-------':>10}  {'------':>10}")

            # Filter to ±30% of spot for readability
            for entry in iv_data:
                strike = entry["strike"]
                if self.spot_price > 0:
                    distance = abs(strike - self.spot_price) / self.spot_price
                    if distance > 0.30:
                        continue

                call_iv = f"{entry['call_iv']:.1f}%" if entry["call_iv"] is not None else "   -"
                put_iv = f"{entry['put_iv']:.1f}%" if entry["put_iv"] is not None else "   -"
                lines.append(f"  {strike:>10,.0f}  {call_iv:>10}  {put_iv:>10}")
            lines.append("")

        # P/C by Moneyness
        pc = result["pc_by_moneyness"]
        lines.append("P/C RATIO BY MONEYNESS:")
        for bucket_name, label in [("atm", "ATM"), ("near_otm", "Near-OTM"), ("far_otm", "Far-OTM")]:
            bucket = pc[bucket_name]
            rng = bucket["range"]
            ratio = bucket["ratio"]
            bias = bucket["bias"]

            if ratio == float("inf"):
                ratio_str = "N/A (No Call OI)"
            else:
                ratio_str = f"P/C = {ratio:.2f} ({bias})"

            lines.append(f"  {label} ({rng}):{'':>5}{ratio_str}")
        lines.append("")

        # Second-Order Greeks
        second = result["second_order_greeks"]
        lines.append("SECOND-ORDER GREEKS:")
        lines.append(f"  Net Vanna Exposure: {second['net_vanna']:+.6f}")
        lines.append(f"  Net Charm Exposure: {second['net_charm']:+.6f}")
        lines.append(f"  Vanna Signal: {second['vanna_signal']}")
        lines.append(f"  Charm Signal: {second['charm_signal']}")
        lines.append("")

        return "\n".join(lines)
