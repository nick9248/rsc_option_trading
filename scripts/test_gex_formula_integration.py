"""
Test GEX/DEX calculation integration with updated formula.

This script verifies that the updated Spot² * 0.01 formula works correctly
in the full system pipeline.
"""

import logging
from coding.core.logging.logging_setup import init_logging
from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService
from coding.service.deribit.deribit_api_service import DeribitApiService

init_logging(level="INFO")
logger = logging.getLogger(__name__)


def main():
    """Test GEX/DEX integration with real data."""
    logger.info("\n" + "="*100)
    logger.info("TESTING GEX/DEX FORMULA INTEGRATION")
    logger.info("="*100 + "\n")

    api_service = DeribitApiService()
    service = OnChainAnalysisService(api_service)

    # Generate report for BTC with a specific expiration
    logger.info("Generating on-chain analysis for BTC - 27MAR26...")

    def progress_callback(message: str):
        logger.info(f"  {message}")

    try:
        report = service.generate_report(
            currency="BTC",
            expiration="27MAR26",
            progress_callback=progress_callback
        )

        logger.info("\n" + "="*100)
        logger.info("GENERATED REPORT EXCERPT:")
        logger.info("="*100)

        # Extract GEX/DEX section from report
        lines = report.split("\n")
        gex_section_started = False
        gex_lines = []

        for line in lines:
            if "GEX/DEX ANALYSIS" in line:
                gex_section_started = True
            if gex_section_started:
                gex_lines.append(line)
                if len(gex_lines) > 50:  # Show first 50 lines of GEX section
                    break

        print("\n".join(gex_lines[:50]))

        logger.info("\n" + "="*100)
        logger.info("INTEGRATION TEST COMPLETE")
        logger.info("="*100)
        logger.info("\nThe GEX values shown above use the updated formula:")
        logger.info("Net GEX = (Call Gamma - Put Gamma) * Spot² * 0.01")
        logger.info("\nVerify that:")
        logger.info("1. Report generated without errors")
        logger.info("2. GEX values are properly scaled (larger magnitudes)")
        logger.info("3. Key levels (Call Resistance, Put Support, HVL) are detected")

    except Exception as e:
        logger.error(f"Integration test failed: {e}", exc_info=True)
        raise


if __name__ == "__main__":
    main()
