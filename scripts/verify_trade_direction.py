"""
Verify the interpretation of Deribit's 'direction' field in trade data.

This script tests whether direction represents:
A) Taker's action (taker bought/sold) - typical for flow analysis
B) Maker's action (maker bought/sold) - as documented for block trades
"""

import logging
from coding.core.database.repository import DatabaseRepository
from dotenv import load_dotenv
from collections import defaultdict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()


def analyze_trade_direction():
    """Analyze trade direction patterns to determine correct interpretation."""

    repo = DatabaseRepository()

    with repo._db_cursor() as cursor:
        # Get trades for BTC 6MAR26
        cursor.execute('''
            SELECT
                strike,
                option_type,
                direction,
                SUM(amount) as total_volume,
                COUNT(*) as trade_count,
                AVG(price) as avg_price
            FROM historical_trades
            WHERE currency = 'BTC'
                AND expiration = '6MAR26'
                AND strike IS NOT NULL
                AND direction IS NOT NULL
            GROUP BY strike, option_type, direction
            ORDER BY strike, option_type, direction
        ''')

        results = cursor.fetchall()

        # Get current spot price
        cursor.execute('''
            SELECT AVG(index_price)
            FROM historical_trades
            WHERE currency = 'BTC'
                AND expiration = '6MAR26'
                AND trade_timestamp > EXTRACT(EPOCH FROM NOW() - INTERVAL '1 hour') * 1000
                AND index_price IS NOT NULL
        ''')

        spot_price = cursor.fetchone()[0]
        if spot_price:
            spot_price = float(spot_price)
        else:
            spot_price = 68000  # approximate from chart

    print("\n" + "="*100)
    print(f"TRADE DIRECTION ANALYSIS - BTC 6MAR26 (Spot: ~${spot_price:,.0f})")
    print("="*100)

    # Organize data by strike
    strike_data = defaultdict(lambda: {
        'C_buy': 0, 'C_sell': 0,
        'P_buy': 0, 'P_sell': 0,
        'C_buy_count': 0, 'C_sell_count': 0,
        'P_buy_count': 0, 'P_sell_count': 0
    })

    for strike, opt_type, direction, volume, count, avg_price in results:
        strike = float(strike)
        volume = float(volume)

        key = f"{opt_type}_{direction}"
        strike_data[strike][key] += volume
        strike_data[strike][f"{key}_count"] += count

    # Analyze patterns
    print("\nKEY OBSERVATIONS:")
    print("-" * 100)

    # Check OTM calls (should typically be sold, not bought)
    otm_call_strikes = [s for s in strike_data.keys() if s > spot_price * 1.05]
    if otm_call_strikes:
        sample_strike = sorted(otm_call_strikes)[0]  # First OTM call
        data = strike_data[sample_strike]

        print(f"\n1. FAR OTM CALL (${sample_strike:,.0f}, ~{((sample_strike/spot_price - 1) * 100):.1f}% OTM):")
        print(f"   Current interpretation:")
        print(f"     - Call Buy:  {data['C_buy']:.2f} BTC ({data['C_buy_count']} trades)")
        print(f"     - Call Sell: {data['C_sell']:.2f} BTC ({data['C_sell_count']} trades)")

        if data['C_sell'] > data['C_buy']:
            print(f"   -> More SELLING than buying (typical for OTM calls - premium collection)")
        else:
            print(f"   -> More BUYING than selling (unusual for far OTM calls)")

        print(f"\n   If direction is FLIPPED (maker's perspective):")
        print(f"     - Call Buy would become: {data['C_sell']:.2f} BTC (taker buying)")
        print(f"     - Call Sell would become: {data['C_buy']:.2f} BTC (taker selling)")

    # Check ATM strikes
    atm_strikes = [s for s in strike_data.keys() if abs(s - spot_price) / spot_price < 0.05]
    if atm_strikes:
        sample_strike = sorted(atm_strikes, key=lambda x: abs(x - spot_price))[0]
        data = strike_data[sample_strike]

        print(f"\n2. ATM STRIKE (${sample_strike:,.0f}, ~{((sample_strike/spot_price - 1) * 100):.1f}% from spot):")
        print(f"   Call Buy:  {data['C_buy']:.2f} BTC ({data['C_buy_count']} trades)")
        print(f"   Call Sell: {data['C_sell']:.2f} BTC ({data['C_sell_count']} trades)")
        print(f"   Put Buy:   {data['P_buy']:.2f} BTC ({data['P_buy_count']} trades)")
        print(f"   Put Sell:  {data['P_sell']:.2f} BTC ({data['P_sell_count']} trades)")

        call_ratio = data['C_buy'] / data['C_sell'] if data['C_sell'] > 0 else float('inf')
        put_ratio = data['P_buy'] / data['P_sell'] if data['P_sell'] > 0 else float('inf')

        print(f"   Call Buy/Sell Ratio: {call_ratio:.2f}")
        print(f"   Put Buy/Sell Ratio: {put_ratio:.2f}")

    # Check ITM puts
    itm_put_strikes = [s for s in strike_data.keys() if s < spot_price * 0.95]
    if itm_put_strikes:
        sample_strike = sorted(itm_put_strikes, reverse=True)[0]  # Closest ITM put
        data = strike_data[sample_strike]

        print(f"\n3. ITM PUT (${sample_strike:,.0f}, ~{((1 - sample_strike/spot_price) * 100):.1f}% ITM):")
        print(f"   Put Buy:  {data['P_buy']:.2f} BTC ({data['P_buy_count']} trades)")
        print(f"   Put Sell: {data['P_sell']:.2f} BTC ({data['P_sell_count']} trades)")

        if data['P_buy'] > data['P_sell']:
            print(f"   -> More BUYING than selling")
        else:
            print(f"   -> More SELLING than buying")

    # Summary
    print("\n" + "="*100)
    print("INTERPRETATION GUIDE:")
    print("="*100)
    print("\nIf current interpretation is CORRECT (direction = taker's action):")
    print("  - 'buy' = taker bought = aggressive buying = bullish signal")
    print("  - 'sell' = taker sold = aggressive selling = bearish signal")
    print("\nIf current interpretation is WRONG (direction = maker's action):")
    print("  - 'buy' = maker bought, taker sold = aggressive selling = bearish signal")
    print("  - 'sell' = maker sold, taker bought = aggressive buying = bullish signal")
    print("  - WE NEED TO FLIP ALL BUY/SELL LABELS!")

    print("\n" + "="*100)
    print("RECOMMENDATION:")
    print("="*100)
    print("\nBased on Deribit's block trade documentation:")
    print("'The direction field is always expressed from the maker's perspective'")
    print("\nIf this applies to ALL trades (not just block trades), then:")
    print(">>> WE SHOULD FLIP THE INTERPRETATION <<<")
    print("\nNext step: Verify with Deribit API documentation for public/get_last_trades_by_currency_and_time")
    print("="*100)


if __name__ == "__main__":
    analyze_trade_direction()
