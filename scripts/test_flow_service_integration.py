"""
Integration test for flow chart generation in the service layer.

Verifies that the OnChainAnalysisService correctly generates charts
and returns both the report and chart paths.
"""

import logging
from pathlib import Path
from coding.core.logging.logging_setup import init_logging
from coding.core.database.repository import DatabaseRepository
from coding.service.deribit.deribit_api_service import DeribitApiService
from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService

init_logging(level="INFO")
logger = logging.getLogger(__name__)


def test_service_integration():
    """Test that service correctly generates flow charts."""
    logger.info("Testing service integration with flow charts...")

    repository = DatabaseRepository()

    with DeribitApiService() as api_service:
        service = OnChainAnalysisService(api_service, repository=repository)

        logger.info("Fetching and analyzing BTC with buy/sell flow...")
        report, chart_paths = service.fetch_and_analyze(
            currency="BTC",
            fetch_gex_dex=False,
            fetch_buy_sell_flow=True,
            progress_callback=lambda msg: logger.info(f"Progress: {msg}")
        )

        # Verify report is returned
        logger.info(f"✓ Report returned (length: {len(report)} chars)")
        assert isinstance(report, str), "Report should be a string"
        assert len(report) > 0, "Report should not be empty"

        # Verify chart paths are returned
        logger.info(f"✓ Chart paths returned for {len(chart_paths)} expirations")
        assert isinstance(chart_paths, dict), "Chart paths should be a dict"

        if chart_paths:
            # Check first expiration
            first_expiration = sorted(chart_paths.keys())[0]
            exp_charts = chart_paths[first_expiration]

            logger.info(f"Checking charts for expiration: {first_expiration}")

            # Verify all three chart types exist
            assert "distribution" in exp_charts, "Distribution chart path missing"
            assert "net_flow" in exp_charts, "Net flow chart path missing"
            assert "trend" in exp_charts, "Trend chart path missing"

            # Verify files exist
            for chart_type, chart_path in exp_charts.items():
                path = Path(chart_path)
                assert path.exists(), f"{chart_type} chart file does not exist: {chart_path}"
                assert path.suffix == ".html", f"{chart_type} chart should be HTML"
                size_mb = path.stat().st_size / (1024 * 1024)
                logger.info(f"  ✓ {chart_type}: {path.name} ({size_mb:.1f} MB)")

            logger.info("\n✓ All integration tests passed!")
            logger.info(f"Service correctly returns tuple: (report, chart_paths)")
            logger.info(f"Charts generated for: {', '.join(chart_paths.keys())}")

        else:
            logger.warning("No chart paths returned - this may indicate no flow data available")


if __name__ == "__main__":
    test_service_integration()
