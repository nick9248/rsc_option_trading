"""
Test how far back the Deribit API allows us to fetch trades.

CRITICAL QUESTION: Can we fill 12-hour gaps when system restarts?

Strategy:
1. Test fetching trades from 1 hour ago
2. Test fetching trades from 6 hours ago
3. Test fetching trades from 12 hours ago
4. Test fetching trades from 24 hours ago
5. Determine maximum lookback window

This tells us if we can fill gaps when system is down (11 PM - 11 AM).
"""

import logging
import requests
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def test_lookback(hours_ago: int) -> dict:
    """
    Test if API returns trades from N hours ago.

    Args:
        hours_ago: How many hours back to test

    Returns:
        Result with trade count and oldest trade found
    """
    base_url = "https://www.deribit.com/api/v2"
    endpoint = "/public/get_last_trades_by_currency"

    logger.info(f"\nTesting lookback: {hours_ago} hours ago...")

    try:
        # Fetch recent trades
        response = requests.get(
            url=f"{base_url}{endpoint}",
            params={
                "currency": "BTC",
                "kind": "option",
                "count": 1000  # Fetch many trades to find old ones
            },
            timeout=30
        )

        response.raise_for_status()
        data = response.json()

        result = data.get("result", {})
        if isinstance(result, dict):
            trades = result.get("trades", [])
        elif isinstance(result, list):
            trades = result
        else:
            trades = []

        if not trades:
            return {"hours_ago": hours_ago, "trade_count": 0, "success": False}

        # Find oldest trade
        oldest_trade = min(trades, key=lambda t: t.get("timestamp", float('inf')))
        oldest_time = datetime.fromtimestamp(oldest_trade.get("timestamp", 0) / 1000)

        # Calculate age
        now = datetime.now()
        age = now - oldest_time
        age_hours = age.total_seconds() / 3600

        # Check if we have trades from the target time
        target_time = now - timedelta(hours=hours_ago)
        trades_from_target = [
            t for t in trades
            if datetime.fromtimestamp(t.get("timestamp", 0) / 1000) <= target_time
        ]

        logger.info(f"  Total trades fetched: {len(trades)}")
        logger.info(f"  Oldest trade: {oldest_time} ({age_hours:.1f} hours ago)")
        logger.info(f"  Trades from {hours_ago}h ago: {len(trades_from_target)}")

        success = len(trades_from_target) > 0

        return {
            "hours_ago": hours_ago,
            "trade_count": len(trades),
            "oldest_age_hours": age_hours,
            "trades_from_target": len(trades_from_target),
            "success": success
        }

    except Exception as e:
        logger.error(f"  Error: {e}")
        return {"hours_ago": hours_ago, "success": False, "error": str(e)}


def test_all_lookbacks():
    """Test multiple lookback windows to find API limit."""

    logger.info(f"{'='*60}")
    logger.info(f"API LOOKBACK WINDOW TEST")
    logger.info(f"{'='*60}")
    logger.info(f"Purpose: Determine if we can fill 12-hour gaps")
    logger.info(f"System downtime: 11 PM - 11 AM (12 hours)\n")

    # Test different lookback periods
    test_periods = [1, 3, 6, 12, 24]

    results = []
    for hours in test_periods:
        result = test_lookback(hours)
        results.append(result)

    # Analyze results
    logger.info(f"\n{'='*60}")
    logger.info(f"LOOKBACK WINDOW ANALYSIS")
    logger.info(f"{'='*60}")

    max_lookback = 0
    for r in results:
        if r.get("success"):
            max_lookback = max(max_lookback, r["hours_ago"])
            status = "✅ CAN FETCH"
        else:
            status = "❌ CANNOT FETCH"

        logger.info(f"{r['hours_ago']}h ago: {status}")

    logger.info(f"\n{'='*60}")
    logger.info(f"CONCLUSION")
    logger.info(f"{'='*60}")

    if max_lookback >= 12:
        logger.info(f"✅ SUCCESS: API provides {max_lookback}h lookback")
        logger.info(f"✅ GAP FILLING POSSIBLE: Can fetch data from 12-hour downtime")
        logger.info(f"\nRecommendation:")
        logger.info(f"  - On system startup (11 AM), fetch trades since 11 PM")
        logger.info(f"  - Use catch-up mode to backfill missed hours")
        logger.info(f"  - No data loss expected")
    elif max_lookback >= 6:
        logger.info(f"⚠️  PARTIAL: API provides {max_lookback}h lookback")
        logger.info(f"⚠️  GAP FILLING LIMITED: Can only fill {max_lookback}-hour gaps")
        logger.info(f"\nRecommendation:")
        logger.info(f"  - On startup, fetch what's available ({max_lookback}h)")
        logger.info(f"  - Accept {12 - max_lookback}h daily gap")
        logger.info(f"  - Still get {24 - (12 - max_lookback)}h of data per day")
    else:
        logger.info(f"❌ LIMITED: API provides only {max_lookback}h lookback")
        logger.info(f"❌ GAP FILLING NOT POSSIBLE: Cannot fill 12-hour gaps")
        logger.info(f"\nRecommendation:")
        logger.info(f"  - Accept 12-hour daily gaps")
        logger.info(f"  - Still collect 12h/day when system is up")
        logger.info(f"  - 12h/day × 90 days = 1080 hours of data (acceptable)")

    logger.info(f"\n{'='*60}")
    logger.info(f"NEXT STEPS")
    logger.info(f"{'='*60}")
    logger.info(f"1. Review lookback capability above")
    logger.info(f"2. Design catch-up logic based on max_lookback")
    logger.info(f"3. Implement gap-filling in ProspectiveCollector")
    logger.info(f"4. Test with simulated 12-hour gap")

    return max_lookback


if __name__ == "__main__":
    max_lookback = test_all_lookbacks()

    logger.info(f"\nMax lookback window: {max_lookback} hours")
