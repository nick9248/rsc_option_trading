"""
Test script for flow chart generation.

Verifies that the chart generation functions work correctly with real data.
"""

import logging
from coding.core.logging.logging_setup import init_logging
from coding.core.database.repository import DatabaseRepository
from coding.core.analytics.buy_sell_flow_analyzer import BuySellFlowAnalyzer
from coding.core.analytics.chart_generator import (
    generate_flow_distribution_chart,
    generate_net_flow_chart,
    generate_flow_trend_chart,
    save_chart
)

init_logging(level="INFO")
logger = logging.getLogger(__name__)


def test_flow_charts():
    """Test flow chart generation with real data."""
    logger.info("Testing flow chart generation...")

    # Initialize repository
    repository = DatabaseRepository()

    # Test parameters
    currency = "BTC"
    expiration = "27MAR26"
    spot_price = 100000.0  # Approximate

    # Create flow analyzer
    logger.info(f"Creating flow analyzer for {currency} {expiration}...")
    flow_analyzer = BuySellFlowAnalyzer(
        repository=repository,
        currency=currency,
        expiration=expiration,
        spot_price=spot_price,
        lookback_hours=24
    )

    # Calculate flow data
    logger.info("Calculating flow data...")
    flow_data = flow_analyzer.calculate()

    logger.info(f"Trade count: {flow_data.get('trade_count', 0)}")
    logger.info(f"Strikes analyzed: {len(flow_data.get('flow_data', {}))}")

    # Test Chart A: Flow distribution
    logger.info("Generating flow distribution chart...")
    fig_distribution = generate_flow_distribution_chart(
        flow_data=flow_data,
        spot_price=spot_price,
        currency=currency,
        expiration=expiration
    )
    chart_path_dist = save_chart(
        fig_distribution,
        f"test_flow_distribution_{currency}_{expiration}",
        subfolder=f"test_flow/{expiration}",
        save_png=False
    )
    logger.info(f"✓ Distribution chart saved: {chart_path_dist}")

    # Test Chart B: Net flow
    logger.info("Generating net flow chart...")
    fig_net_flow = generate_net_flow_chart(
        flow_data=flow_data,
        spot_price=spot_price,
        currency=currency,
        expiration=expiration
    )
    chart_path_net = save_chart(
        fig_net_flow,
        f"test_net_flow_{currency}_{expiration}",
        subfolder=f"test_flow/{expiration}",
        save_png=False
    )
    logger.info(f"✓ Net flow chart saved: {chart_path_net}")

    # Test Chart C: Trend over time
    logger.info("Generating flow trend chart...")
    fig_trend = generate_flow_trend_chart(
        repository=repository,
        currency=currency,
        expiration=expiration,
        lookback_days=7
    )
    chart_path_trend = save_chart(
        fig_trend,
        f"test_flow_trend_{currency}_{expiration}",
        subfolder=f"test_flow/{expiration}",
        save_png=False
    )
    logger.info(f"✓ Trend chart saved: {chart_path_trend}")

    logger.info("All charts generated successfully!")
    logger.info(f"\nChart locations:")
    logger.info(f"  Distribution: {chart_path_dist}")
    logger.info(f"  Net Flow: {chart_path_net}")
    logger.info(f"  Trend: {chart_path_trend}")


if __name__ == "__main__":
    test_flow_charts()
