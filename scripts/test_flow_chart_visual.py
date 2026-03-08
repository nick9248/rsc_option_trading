"""
Test the buy/sell flow chart generation to verify visual correctness.
"""

import logging
from datetime import datetime
from coding.core.database.repository import DatabaseRepository
from coding.core.analytics.buy_sell_flow_analyzer import BuySellFlowAnalyzer
from coding.core.analytics.chart_generator import generate_flow_distribution_chart
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()


def test_flow_chart():
    """Generate flow chart and verify data mapping."""

    repo = DatabaseRepository()

    # Get spot price
    with repo._db_cursor() as cursor:
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
            spot_price = 68000

    # Initialize analyzer
    analyzer = BuySellFlowAnalyzer(
        repository=repo,
        currency="BTC",
        expiration="6MAR26",
        spot_price=spot_price,
        lookback_hours=24
    )

    # Calculate flow data
    flow_data = analyzer.calculate()

    # Check specific strike (78k)
    strike_78k_data = flow_data["flow_data"].get(78000.0, {})
    call_data = strike_78k_data.get("C", {})

    print("\n" + "="*80)
    print("FLOW DATA AT $78,000 STRIKE (CALLS)")
    print("="*80)
    print(f"Buy Volume:    {call_data.get('buy_volume', 0):.4f} BTC")
    print(f"Sell Volume:   {call_data.get('sell_volume', 0):.4f} BTC")
    print(f"Buy Notional:  ${call_data.get('buy_notional', 0):,.0f}")
    print(f"Sell Notional: ${call_data.get('sell_notional', 0):,.0f}")
    print(f"\nRatio (Sell/Buy): {call_data.get('sell_notional', 0) / call_data.get('buy_notional', 1) if call_data.get('buy_notional', 0) > 0 else 'N/A':.2f}x")
    print("="*80)

    # Generate chart
    fig = generate_flow_distribution_chart(
        flow_data=flow_data,
        spot_price=spot_price,
        currency="BTC",
        expiration="6MAR26"
    )

    # Save chart
    output_path = f"output/charts/flow_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    fig.write_html(output_path)
    print(f"\nChart saved to: {output_path}")

    # Print trace data for $78k to verify
    print("\n" + "="*80)
    print("CHART DATA VERIFICATION (what goes into the chart)")
    print("="*80)

    # Extract the data that would be plotted for $78k
    strikes = sorted(flow_data["flow_data"].keys())
    idx_78k = strikes.index(78000.0) if 78000.0 in strikes else None

    if idx_78k is not None:
        # The chart shows the first 4 traces (notional by default)
        call_buy_notional = []
        call_sell_notional = []

        for strike in strikes:
            strike_data = flow_data["flow_data"][strike]
            call_d = strike_data.get("C", {})
            call_buy_notional.append(call_d.get("buy_notional", 0))
            call_sell_notional.append(call_d.get("sell_notional", 0))

        print(f"\nAt $78,000 strike (index {idx_78k}):")
        print(f"  Trace 0 (Green 'Call Buy'):  x = ${call_buy_notional[idx_78k]:,.0f}")
        print(f"  Trace 1 (Red 'Call Sell'):   x = ${call_sell_notional[idx_78k]:,.0f}")
        print(f"\nIn the chart:")
        print(f"  - Green bar should be SHORTER (${call_buy_notional[idx_78k]:,.0f})")
        print(f"  - Red bar should be LONGER (${call_sell_notional[idx_78k]:,.0f})")
        print(f"  - Red should be {call_sell_notional[idx_78k] / call_buy_notional[idx_78k]:.1f}x longer than green")

    print("="*80)


if __name__ == "__main__":
    test_flow_chart()
