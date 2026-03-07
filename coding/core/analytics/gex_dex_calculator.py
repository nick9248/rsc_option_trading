"""
GEX (Gamma Exposure) and DEX (Delta Exposure) calculator.

Calculates gamma and delta exposure per strike, cumulative profiles,
and identifies key levels (Call Resistance, Put Support, HVL).
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class GexDexCalculator:
    """
    Calculate GEX and DEX from options data with Greeks.

    Formulas (Industry Standard):
    - Net GEX per strike = (Call Gamma - Put Gamma) * Spot Price² * 0.01
      * Gamma is weighted by OI during aggregation
      * Spot² accounts for notional dollar exposure
      * 0.01 scales to 1% underlying move
    - Net DEX per strike = Call Delta + Put Delta (put delta is negative)

    Key Levels:
    - Call Resistance: Strike with maximum positive Net GEX
    - Put Support: Strike with maximum negative Net GEX
    - HVL (High Vol Level): Zero Gamma level where cumulative GEX flips sign
    """

    def __init__(
        self,
        instruments: List[Dict[str, Any]],
        spot_price: float,
        currency: str = "BTC",
    ):
        """
        Initialize calculator with instrument data containing Greeks.

        Args:
            instruments: List of instrument dicts with gamma, delta, OI, strike, option_type.
            spot_price: Current underlying spot price.
            currency: Underlying currency symbol (e.g. "BTC", "ETH"). Used for unit labels.
                      GEX is always in USD; DEX is in this currency.
        """
        self.instruments = instruments
        self.spot_price = spot_price
        self.currency = currency
        self.strike_data: Dict[float, Dict[str, Any]] = {}

    def calculate(self) -> Dict[str, Any]:
        """
        Calculate all GEX/DEX metrics.

        Returns:
            Dict with per-strike data, cumulative profiles, and key levels.
        """
        self._aggregate_by_strike()
        self._calculate_gex_dex()
        cumulative = self._calculate_cumulative_profiles()
        key_levels = self._detect_key_levels()

        return {
            "strike_data": self.strike_data,
            "cumulative_gex": cumulative["cumulative_gex"],
            "cumulative_dex": cumulative["cumulative_dex"],
            "key_levels": key_levels,
            "spot_price": self.spot_price,
            "total_net_gex": sum(d["net_gex"] for d in self.strike_data.values()),
            "total_net_dex": sum(d["net_dex"] for d in self.strike_data.values()),
        }

    def _aggregate_by_strike(self) -> None:
        """Aggregate instrument data by strike price."""
        for item in self.instruments:
            strike = item.get("strike")
            if strike is None:
                continue

            option_type = item.get("option_type", "").upper()
            gamma = item.get("gamma") or 0
            delta = item.get("delta") or 0
            oi = item.get("open_interest") or 0

            if strike not in self.strike_data:
                self.strike_data[strike] = {
                    "call_gamma": 0.0,
                    "put_gamma": 0.0,
                    "call_delta": 0.0,
                    "put_delta": 0.0,
                    "call_oi": 0.0,
                    "put_oi": 0.0,
                    "net_gex": 0.0,
                    "net_dex": 0.0,
                }

            if option_type == "C":
                self.strike_data[strike]["call_gamma"] += gamma * oi
                self.strike_data[strike]["call_delta"] += delta * oi
                self.strike_data[strike]["call_oi"] += oi
            elif option_type == "P":
                self.strike_data[strike]["put_gamma"] += gamma * oi
                self.strike_data[strike]["put_delta"] += delta * oi
                self.strike_data[strike]["put_oi"] += oi

    def _calculate_gex_dex(self) -> None:
        """
        Calculate Net GEX and Net DEX per strike.

        Net GEX = (Call Gamma - Put Gamma) * Spot Price² * 0.01
        - Spot² accounts for notional exposure to underlying moves
        - 0.01 converts to percentage-based move (1%)
        - Gamma values are already weighted by OI from aggregation

        Net DEX = Call Delta + Put Delta (put delta is already negative)
        """
        for strike, data in self.strike_data.items():
            # Net GEX: (Call Gamma - Put Gamma) * Spot² * 0.01 (industry standard)
            # The gamma values are already weighted by OI from aggregation
            net_gamma = data["call_gamma"] - data["put_gamma"]
            data["net_gex"] = net_gamma * (self.spot_price ** 2) * 0.01

            # Net DEX: Call Delta + Put Delta
            # Put delta is negative, so this gives net directional exposure
            data["net_dex"] = data["call_delta"] + data["put_delta"]

            # Store raw net gamma for reference
            data["net_gamma"] = net_gamma

    def _calculate_cumulative_profiles(self) -> Dict[str, Dict[float, float]]:
        """
        Calculate cumulative GEX and DEX profiles across strikes.

        Returns:
            Dict with cumulative_gex and cumulative_dex mappings.
        """
        sorted_strikes = sorted(self.strike_data.keys())

        cumulative_gex: Dict[float, float] = {}
        cumulative_dex: Dict[float, float] = {}

        running_gex = 0.0
        running_dex = 0.0

        for strike in sorted_strikes:
            running_gex += self.strike_data[strike]["net_gex"]
            running_dex += self.strike_data[strike]["net_dex"]
            cumulative_gex[strike] = running_gex
            cumulative_dex[strike] = running_dex
            # Store in strike data as well
            self.strike_data[strike]["cumulative_gex"] = running_gex
            self.strike_data[strike]["cumulative_dex"] = running_dex

        return {
            "cumulative_gex": cumulative_gex,
            "cumulative_dex": cumulative_dex,
        }

    def _detect_key_levels(self) -> Dict[str, Any]:
        """
        Detect key trading levels from GEX/DEX data.

        Returns:
            Dict with call_resistance, put_support, hvl (zero gamma), and gamma_flip.
        """
        if not self.strike_data:
            return {
                "call_resistance": None,
                "put_support": None,
                "hvl": None,
                "gamma_flip": None,
            }

        sorted_strikes = sorted(self.strike_data.keys())

        # Call Resistance: Strike with maximum positive Net GEX
        max_positive_gex = 0.0
        call_resistance = None
        for strike in sorted_strikes:
            gex = self.strike_data[strike]["net_gex"]
            if gex > max_positive_gex:
                max_positive_gex = gex
                call_resistance = strike

        # Put Support: Strike with maximum negative Net GEX (by absolute value)
        max_negative_gex = 0.0
        put_support = None
        for strike in sorted_strikes:
            gex = self.strike_data[strike]["net_gex"]
            if gex < 0 and abs(gex) > max_negative_gex:
                max_negative_gex = abs(gex)
                put_support = strike

        # HVL / Gamma Flip: Where cumulative GEX crosses zero
        # Find the strike where sign changes from positive to negative or vice versa
        gamma_flip = None
        hvl = None
        prev_cumulative = None
        
        # Also track Net GEX flips (local zero gamma) as fallback
        net_gex_flips = []
        prev_net_gex = None
        prev_strike = None

        for strike in sorted_strikes:
            # 1. Check Cumulative Flip
            curr_cumulative = self.strike_data[strike]["cumulative_gex"]

            if prev_cumulative is not None:
                # Check for sign change in cumulative GEX
                if prev_cumulative * curr_cumulative < 0:
                    # Sign changed - this is the global gamma flip point
                    gamma_flip = strike
                    hvl = strike
                    
            prev_cumulative = curr_cumulative
            
            # 2. Check Net GEX Flip (Local Zero Gamma)
            curr_net_gex = self.strike_data[strike]["net_gex"]
            
            if prev_net_gex is not None:
                 if (prev_net_gex > 0 and curr_net_gex < 0) or (prev_net_gex < 0 and curr_net_gex > 0):
                     # Found a local flip
                     # Store strike, and the magnitude of the flip (sum of abs values)
                     magnitude = abs(prev_net_gex) + abs(curr_net_gex)
                     net_gex_flips.append({
                         "strike": strike,
                         "prev_strike": prev_strike,
                         "magnitude": magnitude,
                         "distance_to_spot": abs(strike - self.spot_price) if self.spot_price else float('inf')
                     })
            
            prev_net_gex = curr_net_gex
            prev_strike = strike

        # If no global flip found, or if it's at the very edge (trivial), use major Net GEX flip
        # Trivial check: if hvl is the first or last strike, it's likely just an artifact of starting at 0
        is_trivial_hvl = hvl == sorted_strikes[0] or hvl == sorted_strikes[-1]
        
        if (hvl is None or is_trivial_hvl) and net_gex_flips:
            # Find the "major" flip. 
            # We prioritize flips closest to spot price to find the relevant trading level.
            # Alternatively, we could prioritize magnitude. 
            # Let's sort by distance to spot first, then magnitude.
            
            # Filter for flips within reasonable range if possible, or just take closest
            best_flip = min(net_gex_flips, key=lambda x: x["distance_to_spot"])
            
            hvl = best_flip["strike"]
            # If we didn't have a gamma flip, we can use this as a proxy or leave it None
            # Keeping gamma_flip strictly for cumulative zero crossing is more accurate to definition.

        # If still no HVL (very rare), find strike closest to zero cumulative GEX (absolute minimum)
        if hvl is None and sorted_strikes:
            min_abs_cumulative = float("inf")
            for strike in sorted_strikes:
                abs_cumulative = abs(self.strike_data[strike]["cumulative_gex"])
                if abs_cumulative < min_abs_cumulative:
                    min_abs_cumulative = abs_cumulative
                    hvl = strike
                    
        return {
            "call_resistance": {
                "strike": call_resistance,
                "net_gex": max_positive_gex,
            } if call_resistance else None,
            "put_support": {
                "strike": put_support,
                "net_gex": -max_negative_gex,
            } if put_support else None,
            "hvl": hvl,
            "gamma_flip": gamma_flip,
        }

    def generate_report_section(self) -> str:
        """
        Generate formatted text report section for GEX/DEX.

        Returns:
            Formatted string for inclusion in analysis report.
        """
        result = self.calculate()
        lines = []
        separator = "-" * 80

        lines.append("GEX/DEX ANALYSIS (Gamma & Delta Exposure)")
        lines.append(separator)
        lines.append(f"Spot Price: ${self.spot_price:,.2f}")
        lines.append("")

        # Key Levels
        key_levels = result["key_levels"]
        lines.append("KEY LEVELS:")

        if key_levels["call_resistance"]:
            cr = key_levels["call_resistance"]
            lines.append(
                f"  Call Resistance: ${cr['strike']:,.0f} "
                f"(Net GEX: {cr['net_gex']:+,.2f} USD)"
            )
        else:
            lines.append("  Call Resistance: None found")

        if key_levels["put_support"]:
            ps = key_levels["put_support"]
            lines.append(
                f"  Put Support: ${ps['strike']:,.0f} "
                f"(Net GEX: {ps['net_gex']:+,.2f} USD)"
            )
        else:
            lines.append("  Put Support: None found")

        if key_levels["hvl"]:
            lines.append(f"  HVL (Zero Gamma): ${key_levels['hvl']:,.0f}")
        else:
            lines.append("  HVL (Zero Gamma): Not detected")

        lines.append("")

        # Totals
        lines.append("TOTALS:")
        lines.append(f"  Total Net GEX: {result['total_net_gex']:+,.2f} USD")
        lines.append(f"  Total Net DEX: {result['total_net_dex']:+,.4f} {self.currency}")
        lines.append("")

        # Interpretation
        total_gex = result["total_net_gex"]
        if total_gex > 0:
            gex_interp = "Positive (Dealers long gamma - stabilizing, buy dips/sell rallies)"
        elif total_gex < 0:
            gex_interp = "Negative (Dealers short gamma - amplifying volatility)"
        else:
            gex_interp = "Neutral"
        lines.append(f"  GEX Environment: {gex_interp}")

        total_dex = result["total_net_dex"]
        if total_dex > 0:
            dex_interp = "Positive (Net long delta - bullish pressure)"
        elif total_dex < 0:
            dex_interp = "Negative (Net short delta - bearish pressure)"
        else:
            dex_interp = "Neutral"
        lines.append(f"  DEX Environment: {dex_interp}")
        lines.append("")

        # Per-strike data table
        lines.append("GEX/DEX BY STRIKE:")
        lines.append(separator)
        lines.append(
            f"{'Strike':>10}  {'Net GEX(USD)':>13}  {'Net DEX(' + self.currency + ')':>12}  "
            f"{'Cum GEX(USD)':>13}  {'Cum DEX(' + self.currency + ')':>12}  Notes"
        )
        lines.append(
            f"{'------':>10}  {'-------':>13}  {'-------':>12}  "
            f"{'-------':>13}  {'-------':>12}  -----"
        )

        sorted_strikes = sorted(result["strike_data"].keys())
        for strike in sorted_strikes:
            data = result["strike_data"][strike]

            notes = []
            if key_levels["call_resistance"] and strike == key_levels["call_resistance"]["strike"]:
                notes.append("Call Resistance")
            if key_levels["put_support"] and strike == key_levels["put_support"]["strike"]:
                notes.append("Put Support")
            if key_levels["hvl"] and strike == key_levels["hvl"]:
                notes.append("HVL/Zero Gamma")

            notes_str = " | ".join(notes) if notes else ""

            lines.append(
                f"{strike:>10,.0f}  {data['net_gex']:>+12,.2f}  {data['net_dex']:>+12,.4f}  "
                f"{data['cumulative_gex']:>+12,.2f}  {data['cumulative_dex']:>+12,.4f}  {notes_str}"
            )

        lines.append("")
        return "\n".join(lines)
