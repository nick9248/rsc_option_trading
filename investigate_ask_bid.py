"""
Investigate why ask/bid prices are 0 for liquid options.
"""

import logging
import json
from coding.core.logging.logging_setup import init_logging
from coding.service.deribit.deribit_api_service import DeribitApiService

init_logging(level="INFO")
logger = logging.getLogger(__name__)


def main():
    """Investigate ask/bid issue."""
    api = DeribitApiService()

    # Check the specific instruments the user mentioned
    instruments = [
        "ETH-27MAR26-3400-C",
        "ETH-27MAR26-3700-C"
    ]

    logger.info("="*80)
    logger.info("INVESTIGATING ASK/BID ISSUE FOR LIQUID OPTIONS")
    logger.info("="*80)
    logger.info("User reported these have >10k OI (highly liquid)")
    logger.info("")

    for inst_name in instruments:
        logger.info(f"\n{'='*80}")
        logger.info(f"Instrument: {inst_name}")
        logger.info(f"{'='*80}")

        # Fetch ticker data
        logger.info("\n--- /public/ticker response ---")
        ticker = api.get_ticker(inst_name)

        if ticker:
            logger.info(json.dumps(ticker, indent=2, default=str))

            ask_price = ticker.get("ask_price", "NOT IN RESPONSE")
            bid_price = ticker.get("bid_price", "NOT IN RESPONSE")
            mark_price = ticker.get("mark_price", "NOT IN RESPONSE")
            best_ask_price = ticker.get("best_ask_price", "NOT IN RESPONSE")
            best_bid_price = ticker.get("best_bid_price", "NOT IN RESPONSE")
            open_interest = ticker.get("open_interest", "NOT IN RESPONSE")
            volume = ticker.get("volume", "NOT IN RESPONSE")

            logger.info("\n--- Key Fields ---")
            logger.info(f"ask_price: {ask_price}")
            logger.info(f"bid_price: {bid_price}")
            logger.info(f"best_ask_price: {best_ask_price}")
            logger.info(f"best_bid_price: {best_bid_price}")
            logger.info(f"mark_price: {mark_price}")
            logger.info(f"open_interest: {open_interest}")
            logger.info(f"volume: {volume}")

            logger.info("\n--- Analysis ---")
            if ask_price == 0 or bid_price == 0:
                logger.error(f"ask_price or bid_price is 0!")
                if best_ask_price != "NOT IN RESPONSE" or best_bid_price != "NOT IN RESPONSE":
                    logger.warning(f"BUT best_ask_price={best_ask_price} and best_bid_price={best_bid_price} exist!")
                    logger.warning("ROOT CAUSE: We're using wrong field names!")
            else:
                logger.info("ask_price and bid_price are non-zero")
        else:
            logger.error(f"Failed to fetch ticker for {inst_name}")

    logger.info("\n" + "="*80)
    logger.info("CONCLUSION")
    logger.info("="*80)
    logger.info("Check if the API returns 'best_ask_price' instead of 'ask_price'")
    logger.info("Check Deribit API documentation for correct field names")


if __name__ == "__main__":
    main()
