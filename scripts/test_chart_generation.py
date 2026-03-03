"""
Test chart generation to debug white screen issue.
"""

import logging
from pathlib import Path

from coding.core.database.repository import DatabaseRepository
from coding.core.analytics.chart_generator import (
    generate_flow_distribution_chart,
    generate_net_flow_chart,
    generate_flow_trend_chart,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def test_chart_generation():
    """Test generating charts from database."""
    repo = DatabaseRepository()
    currency = "BTC"
    expiration = "27MAR26"

    logger.info(f"Fetching flow metrics for {currency} {expiration}")
    metrics = repo.get_flow_metrics(currency, expiration)

    if not metrics or not metrics.get("flow_data"):
        logger.error("No flow data found!")
        return

    logger.info(f"Found {len(metrics['flow_data'])} strikes")
    logger.info(f"Spot price: {metrics['spot_price']}")

    # Generate charts
    logger.info("Generating distribution chart...")
    fig_dist = generate_flow_distribution_chart(
        flow_data=metrics,
        spot_price=metrics["spot_price"],
        currency=currency,
        expiration=expiration
    )

    logger.info("Generating net flow chart...")
    fig_net = generate_net_flow_chart(
        flow_data=metrics,
        spot_price=metrics["spot_price"],
        currency=currency,
        expiration=expiration
    )

    logger.info("Generating trend chart...")
    fig_trend = generate_flow_trend_chart(
        repository=repo,
        currency=currency,
        expiration=expiration,
        lookback_days=7
    )

    # Save to test directory
    test_dir = Path("output/test_charts")
    test_dir.mkdir(parents=True, exist_ok=True)

    dist_path = test_dir / "test_distribution.html"
    net_path = test_dir / "test_net_flow.html"
    trend_path = test_dir / "test_trend.html"

    logger.info("Saving charts...")
    fig_dist.write_html(str(dist_path))
    fig_net.write_html(str(net_path))
    fig_trend.write_html(str(trend_path))

    logger.info(f"✓ Charts saved to {test_dir}")
    logger.info(f"  - {dist_path}")
    logger.info(f"  - {net_path}")
    logger.info(f"  - {trend_path}")
    logger.info("\nOpen these files in a browser to verify they work!")


if __name__ == "__main__":
    test_chart_generation()
