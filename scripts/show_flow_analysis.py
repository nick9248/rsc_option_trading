"""
Display on-chain analysis with buy/sell flow for verification.
"""

import logging
from coding.core.logging.logging_setup import init_logging
from coding.core.database.repository import DatabaseRepository
from coding.service.deribit.deribit_api_service import DeribitApiService
from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService

init_logging(level="INFO")
logger = logging.getLogger(__name__)


def show_analysis():
    """Show on-chain analysis with buy/sell flow."""
    # Use BTC for demonstration
    currency = "BTC"

    logger.info(f"Fetching on-chain analysis for {currency} with buy/sell flow...")

    # Initialize services
    repository = DatabaseRepository()

    with DeribitApiService() as api_service:
        service = OnChainAnalysisService(api_service, repository=repository)

        # Fetch analysis with buy/sell flow enabled
        report = service.fetch_and_analyze(
            currency=currency,
            fetch_gex_dex=False,  # Skip GEX/DEX for faster execution
            fetch_buy_sell_flow=True,  # Enable buy/sell flow
            progress_callback=lambda msg: logger.info(f"  {msg}")
        )

    # Print the full report
    print("\n" + "="*100)
    print("ON-CHAIN ANALYSIS REPORT WITH BUY/SELL FLOW")
    print("="*100)
    print(report)
    print("="*100)


if __name__ == "__main__":
    show_analysis()
