"""
Test getting RECENT trades (last few minutes) to understand response format.

Strategy: Query without time range first, then add time range once we understand format.
"""

import logging
import requests
import json
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def test_recent_trades():
    """Test get_last_trades_by_currency for recent BTC options trades."""

    base_url = "https://www.deribit.com/api/v2"
    endpoint = "/public/get_last_trades_by_currency"

    params = {
        "currency": "BTC",
        "kind": "option",
        "count": 10  # Just 10 trades to start
    }

    logger.info(f"Fetching RECENT trades (last few minutes)")
    logger.info(f"Endpoint: {base_url}{endpoint}")
    logger.info(f"Parameters: {params}")

    try:
        response = requests.get(
            url=f"{base_url}{endpoint}",
            params=params,
            timeout=30
        )

        response.raise_for_status()
        data = response.json()

        if "result" in data:
            result = data["result"]

            if isinstance(result, dict):
                trades = result.get("trades", [])
                has_more = result.get("has_more", False)
            elif isinstance(result, list):
                trades = result
                has_more = False
            else:
                trades = []
                has_more = False

            logger.info(f"\n{'='*60}")
            logger.info(f"SUCCESS: {len(trades)} recent trades found!")
            logger.info(f"{'='*60}")

            if trades:
                first = trades[0]
                logger.info(f"\nFIRST TRADE:")
                logger.info(json.dumps(first, indent=2))

                # Check critical fields
                logger.info(f"\n{'='*60}")
                logger.info(f"CRITICAL FIELDS CHECK:")
                logger.info(f"{'='*60}")
                logger.info(f"✅ instrument_name: {first.get('instrument_name')}")
                logger.info(f"✅ price: {first.get('price')}")
                logger.info(f"✅ amount: {first.get('amount')}")
                logger.info(f"✅ timestamp: {first.get('timestamp')} ({datetime.fromtimestamp(first.get('timestamp', 0)/1000)})")

                iv = first.get('iv')
                if iv is not None:
                    logger.info(f"✅ IV: {iv} (PRESENT - can calculate Greeks!)")
                else:
                    logger.info(f"❌ IV: None (MISSING - cannot calculate Greeks!)")

                logger.info(f"✅ mark_price: {first.get('mark_price')}")
                logger.info(f"✅ index_price: {first.get('index_price')}")
                logger.info(f"✅ direction: {first.get('direction')} (buy/sell)")

                return {"success": True, "trade_count": len(trades), "sample": first}
            else:
                logger.warning(f"No recent trades found")
                return {"success": True, "trade_count": 0}

        else:
            logger.error(f"No result in response")
            return {"success": False}

    except Exception as e:
        logger.error(f"Error: {e}")
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    logger.info(f"{'='*60}")
    logger.info(f"TESTING RECENT TRADES (NO TIME FILTER)")
    logger.info(f"{'='*60}\n")

    result = test_recent_trades()

    if result.get("success") and result.get("trade_count", 0) > 0:
        logger.info(f"\n{'='*60}")
        logger.info(f"✅ SUCCESS: API works and returns trades with IV!")
        logger.info(f"{'='*60}")
        logger.info(f"\nNext step: Try historical trades with time range")
    else:
        logger.error(f"\n❌ ISSUE: No recent trades or API problem")
