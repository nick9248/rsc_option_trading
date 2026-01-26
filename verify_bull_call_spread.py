"""
Bull Call Spread Implementation Verification Script

This script performs comprehensive audit by:
1. Fetching real Deribit API data
2. Manually calculating expected values
3. Comparing to implementation
4. Identifying potential issues
"""

import logging
from collections import Counter
from typing import Dict, Tuple

from coding.core.logging.logging_setup import init_logging
from coding.service.deribit.deribit_api_service import DeribitApiService
from coding.core.strategy import create_strategy
from coding.core.strategy.models.spread_config import SpreadStrikeConfig

init_logging(level="DEBUG")
logger = logging.getLogger(__name__)


def extract_underlying_price(ticker_data_list: list) -> float:
    """Extract current underlying price from ticker data."""
    # Use mode of underlying prices from active instruments
    prices = [
        item.get("underlying_price")
        for item in ticker_data_list
        if item.get("underlying_price")
    ]
    if prices:
        return Counter(prices).most_common(1)[0][0]
    return 0.0


def extract_strike_from_name(instrument_name: str) -> float:
    """Extract strike price from Deribit instrument name."""
    try:
        parts = instrument_name.split("-")
        return float(parts[2])
    except (IndexError, ValueError):
        return 0.0


def manual_spread_calculation(
    ticker_data: Dict,
    currency: str,
    expiration: str,
    underlying_price: float
) -> Dict:
    """
    Manually calculate optimal Bull Call Spread using same algorithm.

    This replicates the skew-aware algorithm to verify correctness.
    """
    logger.info("\n" + "="*80)
    logger.info("MANUAL CALCULATION - Bull Call Spread Skew-Aware Selection")
    logger.info("="*80)
    logger.info(f"Currency: {currency}")
    logger.info(f"Expiration: {expiration}")
    logger.info(f"Underlying Price: ${underlying_price:.2f}")

    # Filter call instruments for this expiration (already a dict)
    call_instruments = {
        name: data
        for name, data in ticker_data.items()
        if currency in name and expiration in name and name.endswith("-C")
    }

    logger.info(f"Found {len(call_instruments)} call options")

    # Define strike ranges (same as implementation)
    min_long_strike = underlying_price * 0.90
    max_long_strike = underlying_price * 1.20
    min_short_strike = underlying_price * 0.95
    max_short_strike = underlying_price * 1.30

    logger.info(f"Long strike range: ${min_long_strike:.0f} - ${max_long_strike:.0f}")
    logger.info(f"Short strike range: ${min_short_strike:.0f} - ${max_short_strike:.0f}")

    # Debug: Check delta availability
    deltas_available = sum(
        1 for data in call_instruments.values()
        if data.get("greeks", {}).get("delta") is not None
    )
    logger.info(f"Instruments with delta data: {deltas_available}/{len(call_instruments)}")

    # Sample first 3 instruments to show data structure
    if call_instruments:
        sample_names = list(call_instruments.keys())[:3]
        for name in sample_names:
            data = call_instruments[name]
            greeks = data.get("greeks", {})
            logger.debug(
                f"  {name}: delta={greeks.get('delta')}, "
                f"ask={data.get('best_ask_price')}, mark={data.get('mark_price')}"
            )

    # Filter long candidates
    long_candidates = {}
    for name, data in call_instruments.items():
        strike = extract_strike_from_name(name)
        delta = abs(data.get("greeks", {}).get("delta", 0))

        if min_long_strike <= strike <= max_long_strike and delta >= 0.20:
            long_candidates[name] = data

    logger.info(f"Long candidates (delta >= 0.20): {len(long_candidates)}")

    # Filter short candidates
    short_candidates = {}
    for name, data in call_instruments.items():
        strike = extract_strike_from_name(name)
        delta = abs(data.get("greeks", {}).get("delta", 0))

        if min_short_strike <= strike <= max_short_strike and delta >= 0.10:
            short_candidates[name] = data

    logger.info(f"Short candidates (delta >= 0.10): {len(short_candidates)}")

    # Generate all valid spreads
    spreads = []
    quantity = 1

    for long_name, long_data in long_candidates.items():
        long_strike = extract_strike_from_name(long_name)
        long_delta = abs(long_data.get("greeks", {}).get("delta", 0))
        long_iv = long_data.get("greeks", {}).get("iv", 0)

        # Cost calculation (same as implementation)
        long_ask = long_data.get("best_ask_price", 0)
        long_mark = long_data.get("mark_price", 0)
        long_price = long_ask if long_ask > 0 else long_mark
        long_cost = long_price * underlying_price * quantity

        for short_name, short_data in short_candidates.items():
            short_strike = extract_strike_from_name(short_name)

            # Skip if not valid spread structure
            if short_strike <= long_strike:
                continue

            short_delta = abs(short_data.get("greeks", {}).get("delta", 0))
            short_iv = short_data.get("greeks", {}).get("iv", 0)

            # Credit calculation (same as implementation)
            short_bid = short_data.get("best_bid_price", 0)
            short_mark = short_data.get("mark_price", 0)
            short_price = short_bid if short_bid > 0 else short_mark
            short_credit = short_price * underlying_price * abs(quantity)

            # Calculate metrics
            net_debit = long_cost - short_credit
            strike_width = short_strike - long_strike

            # Skip if negative debit (should never happen for bull call spread)
            if net_debit <= 0:
                continue

            # Skip if spread too wide
            if strike_width > underlying_price * 0.25:
                continue

            max_profit = strike_width - net_debit
            profit_debit_ratio = max_profit / net_debit if net_debit > 0 else 0

            # Skip if ratio below minimum (0.3 default in service)
            if profit_debit_ratio < 0.3:
                continue

            iv_skew_slope = (
                (short_iv - long_iv) / strike_width if strike_width > 0 else 0
            )

            spreads.append({
                "long_name": long_name,
                "short_name": short_name,
                "long_strike": long_strike,
                "short_strike": short_strike,
                "long_delta": long_delta,
                "short_delta": short_delta,
                "long_cost": long_cost,
                "short_credit": short_credit,
                "net_debit": net_debit,
                "strike_width": strike_width,
                "max_profit": max_profit,
                "profit_debit_ratio": profit_debit_ratio,
                "iv_skew_slope": iv_skew_slope,
                "long_iv": long_iv,
                "short_iv": short_iv,
            })

    logger.info(f"Valid spreads found: {len(spreads)}")

    if not spreads:
        logger.error("No valid spreads found!")
        return {}

    # Sort by profit/debit ratio (default optimization)
    spreads.sort(key=lambda s: s["profit_debit_ratio"], reverse=True)

    # Show top 5 spreads
    logger.info("\nTop 5 Spreads by Profit/Debit Ratio:")
    for i, spread in enumerate(spreads[:5], 1):
        logger.info(
            f"{i}. {spread['long_strike']:.0f}/{spread['short_strike']:.0f} - "
            f"Ratio: {spread['profit_debit_ratio']:.3f}, "
            f"Width: ${spread['strike_width']:.0f}, "
            f"Debit: ${spread['net_debit']:.2f}, "
            f"Long Delta: {spread['long_delta']:.3f}, "
            f"Short Delta: {spread['short_delta']:.3f}"
        )

    optimal = spreads[0]
    logger.info("\n" + "-"*80)
    logger.info("OPTIMAL SPREAD SELECTED:")
    logger.info(f"  Long:  {optimal['long_name']}")
    logger.info(f"    Strike: ${optimal['long_strike']:.0f}")
    logger.info(f"    Delta: {optimal['long_delta']:.3f}")
    logger.info(f"    IV: {optimal['long_iv']:.4f}")
    logger.info(f"    Cost: ${optimal['long_cost']:.2f}")
    logger.info(f"  Short: {optimal['short_name']}")
    logger.info(f"    Strike: ${optimal['short_strike']:.0f}")
    logger.info(f"    Delta: {optimal['short_delta']:.3f}")
    logger.info(f"    IV: {optimal['short_iv']:.4f}")
    logger.info(f"    Credit: ${optimal['short_credit']:.2f}")
    logger.info(f"  Net Debit: ${optimal['net_debit']:.2f}")
    logger.info(f"  Strike Width: ${optimal['strike_width']:.0f}")
    logger.info(f"  Max Profit: ${optimal['max_profit']:.2f}")
    logger.info(f"  Profit/Debit Ratio: {optimal['profit_debit_ratio']:.3f}")
    logger.info(f"  IV Skew Slope: {optimal['iv_skew_slope']:.6f}")
    logger.info("-"*80)

    return optimal


def verify_implementation(
    ticker_data: Dict,
    currency: str,
    expiration: str,
    underlying_price: float,
    manual_result: Dict
) -> None:
    """Verify implementation matches manual calculation."""
    logger.info("\n" + "="*80)
    logger.info("IMPLEMENTATION VERIFICATION")
    logger.info("="*80)

    # Create strategy with skew-aware config (matching service defaults)
    config = SpreadStrikeConfig(
        method="skew_aware",
        optimize_for="profit_debit_ratio",
        min_profit_debit_ratio=0.3,  # Service default
        quantity=1
    )

    strategy = create_strategy(
        name="Bull Call Spread",
        currency=currency,
        expiration=expiration,
        underlying_price=underlying_price
    )

    strategy.build_legs(ticker_data=ticker_data, spread_config=config)

    # Extract implementation results
    if len(strategy.legs) != 2:
        logger.error(f"Expected 2 legs, got {len(strategy.legs)}")
        return

    long_leg = strategy.legs[0]
    short_leg = strategy.legs[1]

    impl_total_cost = abs(strategy.get_total_cost())
    impl_max_profit = strategy.get_max_profit()
    impl_max_risk = strategy.get_max_risk()
    impl_ratio = impl_max_profit / impl_total_cost if impl_total_cost > 0 else 0

    logger.info("Implementation Results:")
    logger.info(f"  Long:  {long_leg.instrument_name}")
    logger.info(f"    Strike: ${long_leg.strike:.0f}")
    logger.info(f"    Delta: {abs(long_leg.greeks.get('delta', 0)):.3f}")
    logger.info(f"    Cost: ${long_leg.cost:.2f}")
    logger.info(f"  Short: {short_leg.instrument_name}")
    logger.info(f"    Strike: ${short_leg.strike:.0f}")
    logger.info(f"    Delta: {abs(short_leg.greeks.get('delta', 0)):.3f}")
    logger.info(f"    Cost: ${short_leg.cost:.2f} (credit)")
    logger.info(f"  Total Cost (Net Debit): ${impl_total_cost:.2f}")
    logger.info(f"  Max Profit: ${impl_max_profit:.2f}")
    logger.info(f"  Max Risk: ${impl_max_risk:.2f}")
    logger.info(f"  Profit/Debit Ratio: {impl_ratio:.3f}")

    # Compare with manual calculation
    logger.info("\n" + "-"*80)
    logger.info("COMPARISON - Manual vs Implementation:")
    logger.info("-"*80)

    # Strike selection
    strikes_match = (
        long_leg.strike == manual_result["long_strike"] and
        short_leg.strike == manual_result["short_strike"]
    )
    logger.info(f"Strike Selection: {'✓ MATCH' if strikes_match else '✗ MISMATCH'}")
    if not strikes_match:
        logger.warning(
            f"  Manual: {manual_result['long_strike']:.0f}/{manual_result['short_strike']:.0f}"
        )
        logger.warning(f"  Implementation: {long_leg.strike:.0f}/{short_leg.strike:.0f}")

    # Net debit
    debit_diff = abs(impl_total_cost - manual_result["net_debit"])
    debit_match = debit_diff < 0.01
    logger.info(f"Net Debit: {'✓ MATCH' if debit_match else '✗ MISMATCH'}")
    logger.info(f"  Manual: ${manual_result['net_debit']:.2f}")
    logger.info(f"  Implementation: ${impl_total_cost:.2f}")
    if not debit_match:
        logger.warning(f"  Difference: ${debit_diff:.2f}")

    # Profit/Debit Ratio
    ratio_diff = abs(impl_ratio - manual_result["profit_debit_ratio"])
    ratio_match = ratio_diff < 0.01
    logger.info(f"Profit/Debit Ratio: {'✓ MATCH' if ratio_match else '✗ MISMATCH'}")
    logger.info(f"  Manual: {manual_result['profit_debit_ratio']:.3f}")
    logger.info(f"  Implementation: {impl_ratio:.3f}")
    if not ratio_match:
        logger.warning(f"  Difference: {ratio_diff:.3f}")

    # Overall verdict
    logger.info("\n" + "="*80)
    if strikes_match and debit_match and ratio_match:
        logger.info("✓ VERIFICATION PASSED - Implementation matches manual calculation")
    else:
        logger.error("✗ VERIFICATION FAILED - Discrepancies found")
    logger.info("="*80)


def audit_findings() -> None:
    """Provide comprehensive audit findings and recommendations."""
    logger.info("\n" + "="*80)
    logger.info("AUDIT FINDINGS AND RECOMMENDATIONS")
    logger.info("="*80)

    findings = [
        {
            "topic": "Ask/Bid Price Field Names",
            "status": "✓ FIXED",
            "explanation": (
                "ROOT CAUSE: Deribit API does NOT return 'ask_price' and 'bid_price' fields. "
                "It returns 'best_ask_price' and 'best_bid_price'. The implementation was using "
                "wrong field names, causing all options to fall back to mark_price. This has been "
                "fixed across all strategies (Long Call, Long Put, Bull Call Spread)."
            ),
            "action": "Fixed - now using correct field names"
        },
        {
            "topic": "60-Day Expiration Appropriateness",
            "status": "⚠ REQUIRES EVALUATION",
            "explanation": (
                "Bull Call Spreads are typically used for short to medium-term trades "
                "(30-60 days). 60-day expirations are within normal range, but theta "
                "decay is slower compared to 30-day options. For more aggressive directional "
                "plays, consider 30-45 day expirations to capture faster theta decay of "
                "the short leg."
            ),
            "action": "Consider adding expiration filter/preference in GUI"
        },
        {
            "topic": "Strategy-Specific Defaults",
            "status": "⚠ IMPROVEMENT NEEDED",
            "explanation": (
                "Current defaults (delta=0.30, max_loss=5%) are generic. Bull Call Spreads "
                "typically benefit from different defaults:\n"
                "  - Delta: 0.40-0.50 for long leg (more ATM for better directionality)\n"
                "  - Delta: 0.20-0.30 for short leg (further OTM to maximize width)\n"
                "  - Min profit/debit ratio: 0.5 (50% return on capital is professional standard)\n"
                "Single-leg strategies (Long Call/Put) work better with 0.30 delta for pure "
                "speculation, but spreads optimize for risk/reward efficiency."
            ),
            "action": "Implement strategy-specific default configurations"
        },
        {
            "topic": "Budget Parameter in GUI",
            "status": "⚠ MISSING FEATURE",
            "explanation": (
                "SpreadStrikeConfig has max_budget parameter for 'max_width_for_budget' "
                "optimization mode, but it's not exposed in the GUI. This is a useful feature "
                "for capital-constrained scenarios where users want to maximize spread width "
                "within a specific budget."
            ),
            "action": "Add budget input field to Strategy tab GUI"
        },
        {
            "topic": "Strike Selection Algorithm",
            "status": "✓ CORRECT AFTER FIXES",
            "explanation": (
                "The skew-aware algorithm now properly filters strikes:\n"
                "  - Long: 0.90x-1.20x (ATM to moderately OTM), delta >= 0.20\n"
                "  - Short: 0.95x-1.30x (slightly to moderately OTM), delta >= 0.10\n"
                "  - Max spread width: 25% of underlying\n"
                "This prevents selection of unrealistic 'lottery ticket' options. "
                "The delta filters ensure reasonable probability of profit."
            ),
            "action": "No action needed"
        },
        {
            "topic": "Chart Generator Architecture",
            "status": "⚠ REFACTOR NEEDED",
            "explanation": (
                "Current ChartGenerator is monolithic. For scalability when adding more "
                "strategies (Bear Put Spread, Iron Condor, etc.), recommend base class pattern:\n"
                "  - BaseStrategyChartGenerator: Common P&L calculations\n"
                "  - SingleLegChartGenerator: For Long Call/Put\n"
                "  - SpreadChartGenerator: For Bull/Bear spreads\n"
                "  - ComplexStrategyChartGenerator: For Iron Condors, etc.\n"
                "This prevents one strategy from breaking others."
            ),
            "action": "Refactor chart generator with inheritance hierarchy"
        },
        {
            "topic": "Testing Methodology",
            "status": "✓ DOCUMENTED",
            "explanation": (
                "Testing methodology has been added to CLAUDE.md. All future strategy "
                "implementations must follow this verification process: fetch real data, "
                "manual calculation, comparison, edge case testing."
            ),
            "action": "Follow methodology for all future implementations"
        },
    ]

    for i, finding in enumerate(findings, 1):
        logger.info(f"\n{i}. {finding['topic']}")
        logger.info(f"   Status: {finding['status']}")
        logger.info(f"   Explanation: {finding['explanation']}")
        logger.info(f"   Action: {finding['action']}")

    logger.info("\n" + "="*80)


def main():
    """Main verification workflow."""
    try:
        # Initialize API service
        logger.info("Initializing Deribit API service...")
        api = DeribitApiService()

        # Test connectivity
        connection_info = api.check_connectivity()
        logger.info(f"Connected to Deribit API v{connection_info['version']}")

        # Fetch real data for ETH
        currency = "ETH"
        logger.info(f"\nFetching instruments for {currency}...")
        instruments = api.get_instruments(currency=currency, kind="option")
        logger.info(f"Found {len(instruments)} instruments")

        # Extract expirations from instruments
        expirations_from_instruments = set()
        for inst in instruments:
            name = inst.get("instrument_name", "")
            parts = name.split("-")
            if len(parts) >= 4:
                expirations_from_instruments.add(parts[1])

        option_expirations = sorted(list(expirations_from_instruments))

        if not option_expirations:
            logger.error("No expirations found")
            return

        # Use first ~60 day expiration
        expiration = option_expirations[2] if len(option_expirations) > 2 else option_expirations[0]
        logger.info(f"Selected expiration: {expiration} (from {len(option_expirations)} available)")

        # Filter instruments for this expiration
        expiration_instruments = [
            inst for inst in instruments
            if expiration in inst.get("instrument_name", "")
        ]
        logger.info(f"Found {len(expiration_instruments)} instruments for {expiration}")

        # Fetch ticker data with greeks (same as service layer)
        logger.info("Fetching ticker data with greeks...")
        ticker_data = {}

        for inst in expiration_instruments:
            inst_name = inst["instrument_name"]
            try:
                ticker = api.get_ticker(inst_name)
                if ticker:
                    ticker_data[inst_name] = ticker
            except Exception as e:
                logger.warning(f"Failed to fetch ticker for {inst_name}: {e}")
                continue

        logger.info(f"Fetched ticker data for {len(ticker_data)} instruments")

        # Extract underlying price from ticker data
        if not ticker_data:
            logger.error("No ticker data fetched")
            return

        underlying_price = extract_underlying_price(list(ticker_data.values()))

        if underlying_price == 0:
            logger.error("Could not extract underlying price")
            return

        logger.info(f"Current {currency} price: ${underlying_price:.2f}")

        # Manual calculation
        manual_result = manual_spread_calculation(
            ticker_data=ticker_data,
            currency=currency,
            expiration=expiration,
            underlying_price=underlying_price
        )

        if not manual_result:
            logger.error("Manual calculation failed - no valid spreads")
            return

        # Verify implementation
        verify_implementation(
            ticker_data=ticker_data,
            currency=currency,
            expiration=expiration,
            underlying_price=underlying_price,
            manual_result=manual_result
        )

        # Provide audit findings
        audit_findings()

    except Exception as e:
        logger.error(f"Verification failed: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
