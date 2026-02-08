"""
RECONNAISSANCE TEST: Deribit Historical Trades API

Goal: Fetch 1 HOUR of BTC options trades to understand data format before scaling up.

Nobel-level approach:
1. Start small (1 hour, not 6 months)
2. Validate response structure
3. Check for IV, prices, volume, timestamps
4. Understand pagination and rate limits
5. Only then scale to full backfill

Expected endpoint: /public/get_last_trades_by_currency_and_time
"""

import logging
import requests
from datetime import datetime, timedelta
from typing import Dict, List, Any
import json

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def test_historical_trades_endpoint():
    """
    Test the historical trades endpoint with 1 hour of BTC options data.
    """

    base_url = "https://www.deribit.com/api/v2"
    endpoint = "/public/get_last_trades_by_currency_and_time"

    # Test with 1 hour of data from 1 month ago (definitely has historical data)
    # Using milliseconds timestamp
    # Let's try January 2, 2026, 14:00-15:00 UTC (1 month ago, high volume time)
    start_time = int(datetime(2026, 1, 2, 14, 0, 0).timestamp() * 1000)
    end_time = int(datetime(2026, 1, 2, 15, 0, 0).timestamp() * 1000)

    params = {
        "currency": "BTC",
        "kind": "option",  # Only options, not futures
        "start_timestamp": start_time,
        "end_timestamp": end_time,
        "count": 100,  # Small batch to test
        "include_old": True  # Include historical data
    }

    logger.info(f"Testing endpoint: {base_url}{endpoint}")
    logger.info(f"Parameters: {params}")
    logger.info(f"Time range: {datetime.fromtimestamp(start_time/1000)} to {datetime.fromtimestamp(end_time/1000)}")

    try:
        response = requests.get(
            url=f"{base_url}{endpoint}",
            params=params,
            timeout=30
        )

        response.raise_for_status()
        data = response.json()

        logger.info(f"Response status: {response.status_code}")
        logger.info(f"Response keys: {data.keys()}")

        if "result" in data:
            result = data["result"]

            # Check if result is dict (with trades and has_more) or list
            if isinstance(result, dict):
                trades = result.get("trades", [])
                has_more = result.get("has_more", False)
                logger.info(f"Trades returned: {len(trades)}")
                logger.info(f"Has more data: {has_more}")
            elif isinstance(result, list):
                trades = result
                logger.info(f"Trades returned: {len(trades)}")
            else:
                trades = []
                logger.warning(f"Unexpected result type: {type(result)}")

            if trades:
                # Analyze first trade structure
                first_trade = trades[0]
                logger.info(f"\n{'='*60}")
                logger.info(f"FIRST TRADE STRUCTURE:")
                logger.info(f"{'='*60}")
                logger.info(json.dumps(first_trade, indent=2))

                # Check for critical fields
                critical_fields = {
                    "instrument_name": first_trade.get("instrument_name"),
                    "price": first_trade.get("price"),
                    "amount": first_trade.get("amount"),
                    "timestamp": first_trade.get("timestamp"),
                    "iv": first_trade.get("iv"),  # Implied Volatility - CRITICAL
                    "mark_price": first_trade.get("mark_price"),
                    "index_price": first_trade.get("index_price"),
                    "direction": first_trade.get("direction")
                }

                logger.info(f"\n{'='*60}")
                logger.info(f"CRITICAL FIELDS CHECK:")
                logger.info(f"{'='*60}")
                for field, value in critical_fields.items():
                    status = "✅" if value is not None else "❌"
                    logger.info(f"{status} {field}: {value}")

                # Validate IV presence (most critical for our use case)
                if first_trade.get("iv") is not None:
                    logger.info(f"\n✅ SUCCESS: IV field present (value: {first_trade.get('iv')})")
                    logger.info(f"   This means we can calculate Greeks from IV!")
                else:
                    logger.error(f"\n❌ CRITICAL: IV field is MISSING!")
                    logger.error(f"   Cannot calculate Greeks without IV!")

                # Check instrument name format (should be BTC-DATE-STRIKE-C/P)
                instrument = first_trade.get("instrument_name", "")
                if instrument and "-" in instrument:
                    parts = instrument.split("-")
                    logger.info(f"\nInstrument format: {parts}")
                    logger.info(f"  Currency: {parts[0] if len(parts) > 0 else 'N/A'}")
                    logger.info(f"  Expiration: {parts[1] if len(parts) > 1 else 'N/A'}")
                    logger.info(f"  Strike: {parts[2] if len(parts) > 2 else 'N/A'}")
                    logger.info(f"  Type: {parts[3] if len(parts) > 3 else 'N/A'} (C=Call, P=Put)")

            else:
                logger.warning(f"No trades returned. Possible reasons:")
                logger.warning(f"  1. No trades in this time window")
                logger.warning(f"  2. Time range too old / data not available")
                logger.warning(f"  3. API endpoint parameters incorrect")

            return {
                "success": True,
                "trade_count": len(trades),
                "has_more": has_more if isinstance(result, dict) else False,
                "sample_trade": trades[0] if trades else None
            }
        else:
            logger.error(f"No 'result' key in response")
            logger.error(f"Response: {data}")
            return {"success": False, "error": "No result in response"}

    except requests.exceptions.RequestException as e:
        logger.error(f"Request failed: {e}")
        return {"success": False, "error": str(e)}
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return {"success": False, "error": str(e)}


def test_book_summary_for_oi():
    """
    Test get_book_summary_by_instrument to verify OI field is available.

    CRITICAL FIX: We need OI directly from API, not inferred from volume.
    """

    base_url = "https://www.deribit.com/api/v2"
    endpoint = "/public/get_book_summary_by_instrument"

    # Test with a specific BTC option (need to use a current one, not expired)
    # Let's try a perpetual first to test the endpoint
    params = {
        "instrument_name": "BTC-PERPETUAL"
    }

    logger.info(f"\n{'='*60}")
    logger.info(f"TESTING OI AVAILABILITY (Book Summary)")
    logger.info(f"{'='*60}")
    logger.info(f"Endpoint: {base_url}{endpoint}")
    logger.info(f"Instrument: {params['instrument_name']}")

    try:
        response = requests.get(
            url=f"{base_url}{endpoint}",
            params=params,
            timeout=30
        )

        response.raise_for_status()
        data = response.json()

        if "result" in data and isinstance(data["result"], list) and len(data["result"]) > 0:
            result = data["result"][0]

            # Check for open_interest field
            oi = result.get("open_interest")

            logger.info(f"Response structure:")
            logger.info(f"  Keys available: {result.keys()}")

            if oi is not None:
                logger.info(f"\n✅ SUCCESS: open_interest field found (value: {oi})")
                logger.info(f"   OI can be pulled directly from API (no inference needed!)")

                # Show other useful fields
                logger.info(f"\nOther useful fields:")
                logger.info(f"  Volume 24h: {result.get('volume')}")
                logger.info(f"  Bid Price: {result.get('bid_price')}")
                logger.info(f"  Ask Price: {result.get('ask_price')}")
                logger.info(f"  Mark Price: {result.get('mark_price')}")
                logger.info(f"  Mark IV: {result.get('mark_iv')}")

                return {"success": True, "oi_available": True}
            else:
                logger.error(f"\n❌ CRITICAL: open_interest field is MISSING!")
                return {"success": True, "oi_available": False}
        else:
            logger.error(f"Unexpected response format")
            return {"success": False, "error": "Unexpected format"}

    except Exception as e:
        logger.error(f"Error: {e}")
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    logger.info(f"{'='*60}")
    logger.info(f"DERIBIT API RECONNAISSANCE TEST")
    logger.info(f"{'='*60}")
    logger.info(f"Purpose: Understand data format before full backfill")
    logger.info(f"Strategy: Test with 1 hour of data, validate structure")
    logger.info(f"\n")

    # Test 1: Historical trades endpoint
    logger.info(f"TEST 1: Historical Trades Endpoint")
    logger.info(f"-" * 60)
    trades_result = test_historical_trades_endpoint()

    # Test 2: Book summary for OI
    logger.info(f"\n\nTEST 2: Book Summary for OI")
    logger.info(f"-" * 60)
    oi_result = test_book_summary_for_oi()

    # Summary
    logger.info(f"\n{'='*60}")
    logger.info(f"RECONNAISSANCE SUMMARY")
    logger.info(f"{'='*60}")
    logger.info(f"Historical Trades API: {'✅ Working' if trades_result.get('success') else '❌ Failed'}")
    logger.info(f"  Trades found: {trades_result.get('trade_count', 0)}")
    sample_trade = trades_result.get('sample_trade')
    logger.info(f"  IV available: {'✅ Yes' if sample_trade and sample_trade.get('iv') else '❌ No'}")
    logger.info(f"\nOpen Interest API: {'✅ Working' if oi_result.get('success') else '❌ Failed'}")
    logger.info(f"  OI field available: {'✅ Yes' if oi_result.get('oi_available') else '❌ No'}")

    logger.info(f"\n{'='*60}")
    logger.info(f"NEXT STEPS:")
    logger.info(f"{'='*60}")
    if trades_result.get('success') and oi_result.get('success'):
        logger.info(f"✅ Both APIs working - Ready to proceed with full backfill")
        logger.info(f"   1. Add historical trades endpoint to DeribitEndpoints")
        logger.info(f"   2. Create backfill service")
        logger.info(f"   3. Start with 1 week of data, then scale to 6 months")
    else:
        logger.info(f"❌ API issues detected - Need to debug before proceeding")
