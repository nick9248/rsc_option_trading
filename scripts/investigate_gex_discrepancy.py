"""
GEX/DEX Calculation Investigation Script

This script investigates discrepancies between our GEX/DEX calculations and MentorQ reference values.
It tests the hypothesis that we're missing the Spot² term in the industry standard formula.

Current Formula: Net GEX = net_gamma * spot_price
Proposed Formula: Net GEX = net_gamma * spot_price² * 0.01

Reference values from MentorQ (screenshots):
- Feb 27, 2026 (27MAR26): Call Resistance: 72k, Put Support: 70k, HVL: 70k
- Feb 17, 2026 (Next expiry): Call Resistance: 72k, Put Support: 67k, HVL: 69k
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from coding.core.logging.logging_setup import init_logging
from coding.service.deribit.deribit_api_service import DeribitApiService

init_logging(level="INFO")
logger = logging.getLogger(__name__)


class GexDexInvestigator:
    """Investigate GEX/DEX calculation discrepancies."""

    def __init__(self):
        """Initialize investigator with API service."""
        self.api = DeribitApiService()
        self.output_dir = Path("output/gex_investigation")
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def fetch_complete_data(self, currency: str) -> Dict[str, Any]:
        """
        Fetch complete options data with Greeks for a currency.

        Args:
            currency: Currency symbol (BTC or ETH)

        Returns:
            Dictionary with all data needed for GEX calculation
        """
        logger.info(f"Fetching complete data for {currency}...")

        # Step 1: Get perpetual ticker for spot price
        perpetual_ticker = self.api.get_ticker(f"{currency}-PERPETUAL")
        spot_price = perpetual_ticker.get("index_price")
        logger.info(f"Spot Price: ${spot_price:,.2f}")

        # Step 2: Get book summary for all options
        book_summary = self.api.get_book_summary(currency=currency, kind="option")
        logger.info(f"Fetched {len(book_summary)} instruments from book_summary")

        # Step 3: Organize by expiration
        expirations: Dict[str, List[Dict[str, Any]]] = {}
        for item in book_summary:
            instrument_name = item.get("instrument_name", "")
            if not instrument_name or "-" not in instrument_name:
                continue

            # Parse expiration from instrument name (e.g., BTC-27MAR26-72000-C)
            parts = instrument_name.split("-")
            if len(parts) != 4:
                continue

            expiration = parts[1]
            if expiration not in expirations:
                expirations[expiration] = []

            expirations[expiration].append(item)

        logger.info(f"Found {len(expirations)} expirations: {list(expirations.keys())}")

        # Step 4: Fetch Greeks for each instrument
        instruments_with_greeks: Dict[str, List[Dict[str, Any]]] = {}

        for expiration, instruments in expirations.items():
            logger.info(f"Fetching Greeks for {expiration} ({len(instruments)} instruments)...")
            instruments_with_greeks[expiration] = []

            for i, item in enumerate(instruments):
                try:
                    instrument_name = item["instrument_name"]
                    ticker = self.api.get_ticker(instrument_name)
                    greeks = ticker.get("greeks", {})

                    # Combine book_summary data with Greeks
                    complete_item = {
                        **item,
                        "delta": greeks.get("delta"),
                        "gamma": greeks.get("gamma"),
                        "vega": greeks.get("vega"),
                        "theta": greeks.get("theta"),
                        "rho": greeks.get("rho"),
                    }
                    instruments_with_greeks[expiration].append(complete_item)

                    if (i + 1) % 20 == 0:
                        logger.info(f"  Fetched {i + 1}/{len(instruments)}")

                except Exception as e:
                    logger.warning(f"Failed to fetch Greeks for {item['instrument_name']}: {e}")

            logger.info(f"  Completed {expiration}: {len(instruments_with_greeks[expiration])} instruments with Greeks")

        return {
            "currency": currency,
            "spot_price": spot_price,
            "timestamp": datetime.now().isoformat(),
            "expirations": instruments_with_greeks,
        }

    def calculate_gex_both_formulas(
        self, instruments: List[Dict[str, Any]], spot_price: float
    ) -> Dict[str, Any]:
        """
        Calculate GEX using both OLD and NEW formulas for comparison.

        Args:
            instruments: List of instruments with Greeks and OI
            spot_price: Current spot price

        Returns:
            Dictionary with results from both formulas
        """
        # Aggregate by strike
        strike_data: Dict[float, Dict[str, Any]] = {}

        for item in instruments:
            instrument_name = item.get("instrument_name", "")
            if not instrument_name:
                continue

            # Parse strike and option type from instrument_name
            # Format: BTC-27MAR26-280000-P or BTC-27MAR26-64000-C
            parts = instrument_name.split("-")
            if len(parts) != 4:
                continue

            try:
                strike = float(parts[2])
            except (ValueError, IndexError):
                continue

            option_type = parts[3]  # "C" or "P"
            gamma = item.get("gamma") or 0
            delta = item.get("delta") or 0
            oi = item.get("open_interest") or 0

            if strike not in strike_data:
                strike_data[strike] = {
                    "call_gamma": 0.0,
                    "put_gamma": 0.0,
                    "call_delta": 0.0,
                    "put_delta": 0.0,
                    "call_oi": 0.0,
                    "put_oi": 0.0,
                }

            if option_type == "C":
                strike_data[strike]["call_gamma"] += gamma * oi
                strike_data[strike]["call_delta"] += delta * oi
                strike_data[strike]["call_oi"] += oi
            elif option_type == "P":
                strike_data[strike]["put_gamma"] += gamma * oi
                strike_data[strike]["put_delta"] += delta * oi
                strike_data[strike]["put_oi"] += oi

        # Calculate GEX with both formulas
        old_formula_data = {}
        new_formula_data = {}

        for strike, data in strike_data.items():
            net_gamma = data["call_gamma"] - data["put_gamma"]

            # OLD FORMULA: Net GEX = net_gamma * spot_price
            old_gex = net_gamma * spot_price

            # NEW FORMULA: Net GEX = net_gamma * spot_price² * 0.01
            new_gex = net_gamma * (spot_price ** 2) * 0.01

            old_formula_data[strike] = {
                **data,
                "net_gamma": net_gamma,
                "net_gex": old_gex,
                "net_dex": data["call_delta"] + data["put_delta"],
            }

            new_formula_data[strike] = {
                **data,
                "net_gamma": net_gamma,
                "net_gex": new_gex,
                "net_dex": data["call_delta"] + data["put_delta"],
            }

        # Calculate cumulative GEX for both
        old_cumulative = self._calculate_cumulative(old_formula_data)
        new_cumulative = self._calculate_cumulative(new_formula_data)

        # Detect key levels for both
        old_key_levels = self._detect_key_levels(old_formula_data, old_cumulative, spot_price)
        new_key_levels = self._detect_key_levels(new_formula_data, new_cumulative, spot_price)

        return {
            "old_formula": {
                "strike_data": old_formula_data,
                "cumulative_gex": old_cumulative,
                "key_levels": old_key_levels,
            },
            "new_formula": {
                "strike_data": new_formula_data,
                "cumulative_gex": new_cumulative,
                "key_levels": new_key_levels,
            },
        }

    def _calculate_cumulative(self, strike_data: Dict[float, Dict[str, Any]]) -> Dict[float, float]:
        """Calculate cumulative GEX profile."""
        sorted_strikes = sorted(strike_data.keys())
        cumulative = {}
        running_sum = 0.0

        for strike in sorted_strikes:
            running_sum += strike_data[strike]["net_gex"]
            cumulative[strike] = running_sum

        return cumulative

    def _detect_key_levels(
        self,
        strike_data: Dict[float, Dict[str, Any]],
        cumulative_gex: Dict[float, float],
        spot_price: float,
    ) -> Dict[str, Any]:
        """Detect Call Resistance, Put Support, and HVL."""
        sorted_strikes = sorted(strike_data.keys())

        # Call Resistance: Max positive Net GEX
        max_positive_gex = 0.0
        call_resistance = None
        for strike in sorted_strikes:
            gex = strike_data[strike]["net_gex"]
            if gex > max_positive_gex:
                max_positive_gex = gex
                call_resistance = strike

        # Put Support: Max negative Net GEX (by absolute value)
        max_negative_gex = 0.0
        put_support = None
        for strike in sorted_strikes:
            gex = strike_data[strike]["net_gex"]
            if gex < 0 and abs(gex) > max_negative_gex:
                max_negative_gex = abs(gex)
                put_support = strike

        # HVL: Zero crossing in cumulative GEX
        hvl = None
        prev_cumulative = None
        for strike in sorted_strikes:
            curr_cumulative = cumulative_gex[strike]
            if prev_cumulative is not None:
                if prev_cumulative * curr_cumulative < 0:
                    hvl = strike
                    break
            prev_cumulative = curr_cumulative

        # If no zero crossing, find strike closest to zero cumulative
        if hvl is None and sorted_strikes:
            min_abs_cumulative = float("inf")
            for strike in sorted_strikes:
                abs_cumulative = abs(cumulative_gex[strike])
                if abs_cumulative < min_abs_cumulative:
                    min_abs_cumulative = abs_cumulative
                    hvl = strike

        return {
            "call_resistance": call_resistance,
            "put_support": put_support,
            "hvl": hvl,
        }

    def generate_comparison_report(
        self,
        currency: str,
        expiration: str,
        spot_price: float,
        results: Dict[str, Any],
        mentorq_reference: Dict[str, Any],
    ) -> str:
        """
        Generate detailed comparison report.

        Args:
            currency: Currency symbol
            expiration: Expiration date
            spot_price: Current spot price
            results: Results from calculate_gex_both_formulas
            mentorq_reference: MentorQ reference values

        Returns:
            Formatted report string
        """
        lines = []
        separator = "=" * 100

        lines.append(separator)
        lines.append(f"GEX/DEX CALCULATION INVESTIGATION - {currency} {expiration}")
        lines.append(separator)
        lines.append(f"Spot Price: ${spot_price:,.2f}")
        lines.append(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")

        # MentorQ Reference Values
        lines.append("MENTORQ REFERENCE VALUES:")
        lines.append("-" * 100)
        if mentorq_reference:
            lines.append(f"  Call Resistance: ${mentorq_reference.get('call_resistance', 'N/A'):,}")
            lines.append(f"  Put Support: ${mentorq_reference.get('put_support', 'N/A'):,}")
            lines.append(f"  HVL: ${mentorq_reference.get('hvl', 'N/A'):,}")
            if mentorq_reference.get("gex_expiring"):
                lines.append(f"  GEX Expiring: {mentorq_reference['gex_expiring']}")
        else:
            lines.append("  No reference values provided")
        lines.append("")

        # OLD Formula Results
        old_levels = results["old_formula"]["key_levels"]
        lines.append("OUR CURRENT FORMULA: Net GEX = net_gamma * spot_price")
        lines.append("-" * 100)
        lines.append(f"  Call Resistance: ${old_levels['call_resistance']:,.0f}" if old_levels["call_resistance"] else "  Call Resistance: None")
        lines.append(f"  Put Support: ${old_levels['put_support']:,.0f}" if old_levels["put_support"] else "  Put Support: None")
        lines.append(f"  HVL: ${old_levels['hvl']:,.0f}" if old_levels["hvl"] else "  HVL: None")

        if mentorq_reference:
            lines.append("\n  DISCREPANCY vs MENTORQ:")
            if old_levels["call_resistance"] and mentorq_reference.get("call_resistance"):
                diff = old_levels["call_resistance"] - mentorq_reference["call_resistance"]
                lines.append(f"    Call Resistance: {diff:+,.0f} (${abs(diff):,.0f} difference)")
            if old_levels["put_support"] and mentorq_reference.get("put_support"):
                diff = old_levels["put_support"] - mentorq_reference["put_support"]
                lines.append(f"    Put Support: {diff:+,.0f} (${abs(diff):,.0f} difference)")
            if old_levels["hvl"] and mentorq_reference.get("hvl"):
                diff = old_levels["hvl"] - mentorq_reference["hvl"]
                lines.append(f"    HVL: {diff:+,.0f} (${abs(diff):,.0f} difference)")
        lines.append("")

        # NEW Formula Results
        new_levels = results["new_formula"]["key_levels"]
        lines.append("PROPOSED NEW FORMULA: Net GEX = net_gamma * spot_price² * 0.01")
        lines.append("-" * 100)
        lines.append(f"  Call Resistance: ${new_levels['call_resistance']:,.0f}" if new_levels["call_resistance"] else "  Call Resistance: None")
        lines.append(f"  Put Support: ${new_levels['put_support']:,.0f}" if new_levels["put_support"] else "  Put Support: None")
        lines.append(f"  HVL: ${new_levels['hvl']:,.0f}" if new_levels["hvl"] else "  HVL: None")

        if mentorq_reference:
            lines.append("\n  DISCREPANCY vs MENTORQ:")
            if new_levels["call_resistance"] and mentorq_reference.get("call_resistance"):
                diff = new_levels["call_resistance"] - mentorq_reference["call_resistance"]
                lines.append(f"    Call Resistance: {diff:+,.0f} (${abs(diff):,.0f} difference)")
            if new_levels["put_support"] and mentorq_reference.get("put_support"):
                diff = new_levels["put_support"] - mentorq_reference["put_support"]
                lines.append(f"    Put Support: {diff:+,.0f} (${abs(diff):,.0f} difference)")
            if new_levels["hvl"] and mentorq_reference.get("hvl"):
                diff = new_levels["hvl"] - mentorq_reference["hvl"]
                lines.append(f"    HVL: {diff:+,.0f} (${abs(diff):,.0f} difference)")
        lines.append("")

        # Summary
        lines.append("FORMULA COMPARISON:")
        lines.append("-" * 100)

        # Total GEX comparison
        old_total = sum(d["net_gex"] for d in results["old_formula"]["strike_data"].values())
        new_total = sum(d["net_gex"] for d in results["new_formula"]["strike_data"].values())

        lines.append(f"  Total Net GEX (Old): {old_total:+,.2f}")
        lines.append(f"  Total Net GEX (New): {new_total:+,.2f}")
        ratio_str = f"{new_total/old_total:.4f}" if old_total != 0 else "N/A"
        lines.append(f"  Ratio (New/Old): {ratio_str}")
        lines.append("")

        # Key strikes sample
        lines.append("SAMPLE STRIKES - GEX COMPARISON:")
        lines.append("-" * 100)
        lines.append(f"{'Strike':>10}  {'Old GEX':>15}  {'New GEX':>15}  {'Ratio':>10}")
        lines.append(f"{'------':>10}  {'-------':>15}  {'-------':>15}  {'-----':>10}")

        # Show top 5 strikes by absolute GEX (old formula)
        sorted_strikes = sorted(
            results["old_formula"]["strike_data"].keys(),
            key=lambda s: abs(results["old_formula"]["strike_data"][s]["net_gex"]),
            reverse=True
        )[:10]

        for strike in sorted_strikes:
            old_gex = results["old_formula"]["strike_data"][strike]["net_gex"]
            new_gex = results["new_formula"]["strike_data"][strike]["net_gex"]
            ratio = new_gex / old_gex if old_gex != 0 else 0
            lines.append(f"{strike:>10,.0f}  {old_gex:>+15,.2f}  {new_gex:>+15,.2f}  {ratio:>10.4f}")

        lines.append("")
        lines.append(separator)

        return "\n".join(lines)

    def run_investigation(self, currency: str = "BTC") -> None:
        """
        Run complete investigation for a currency.

        Args:
            currency: Currency to investigate (BTC or ETH)
        """
        logger.info(f"\n{'='*100}")
        logger.info(f"STARTING GEX/DEX INVESTIGATION FOR {currency}")
        logger.info(f"{'='*100}\n")

        # Step 1: Fetch complete data
        data = self.fetch_complete_data(currency)

        # Save raw data
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        raw_data_file = self.output_dir / f"{currency}_raw_data_{timestamp_str}.json"
        with open(raw_data_file, "w") as f:
            json.dump(data, f, indent=2)
        logger.info(f"Saved raw data to: {raw_data_file}")

        # Step 2: Analyze each expiration
        spot_price = data["spot_price"]

        # MentorQ reference values (from screenshots)
        mentorq_references = {
            "27MAR26": {
                "call_resistance": 72000,
                "put_support": 70000,
                "hvl": 70000,
                "gex_expiring": "-19.24%",
            },
            # Feb 17 corresponds to the next expiration after 27MAR26
            # The exact expiration code depends on what's available in the data
        }

        all_reports = []

        for expiration, instruments in data["expirations"].items():
            logger.info(f"\n{'='*100}")
            logger.info(f"ANALYZING {expiration}")
            logger.info(f"{'='*100}\n")

            # Calculate with both formulas
            results = self.calculate_gex_both_formulas(instruments, spot_price)

            # Get MentorQ reference if available
            mentorq_ref = mentorq_references.get(expiration, {})

            # Generate report
            report = self.generate_comparison_report(
                currency=currency,
                expiration=expiration,
                spot_price=spot_price,
                results=results,
                mentorq_reference=mentorq_ref,
            )

            print(report)
            all_reports.append(report)

            # Save individual expiration results
            expiration_file = self.output_dir / f"{currency}_{expiration}_results_{timestamp_str}.json"
            with open(expiration_file, "w") as f:
                json.dump(
                    {
                        "currency": currency,
                        "expiration": expiration,
                        "spot_price": spot_price,
                        "mentorq_reference": mentorq_ref,
                        "results": results,
                    },
                    f,
                    indent=2,
                    default=str,
                )
            logger.info(f"Saved results to: {expiration_file}")

        # Save combined report
        report_file = self.output_dir / f"{currency}_investigation_report_{timestamp_str}.txt"
        with open(report_file, "w") as f:
            f.write("\n\n".join(all_reports))
        logger.info(f"\nSaved complete report to: {report_file}")

        logger.info(f"\n{'='*100}")
        logger.info(f"INVESTIGATION COMPLETE FOR {currency}")
        logger.info(f"{'='*100}\n")


def main():
    """Main entry point."""
    investigator = GexDexInvestigator()

    # Investigate BTC (primary focus based on user screenshots)
    # investigator.run_investigation(currency="BTC")

    # Investigate ETH
    investigator.run_investigation(currency="ETH")


if __name__ == "__main__":
    main()
