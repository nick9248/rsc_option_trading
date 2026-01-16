"""
Database capture service for orchestrating data capture operations.

This service provides a clean interface for capturing on-chain data,
storing it in the database, and generating charts.
"""

import logging
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from coding.core.analytics.on_chain_analyzer import OnChainAnalyzer
from coding.core.database import DatabaseRepository
from coding.service.deribit.deribit_api_service import DeribitApiService
from coding.service.database.capture_strategies import get_capture_strategy

logger = logging.getLogger(__name__)


class CaptureResult:
    """
    Result of a capture operation.

    Attributes:
        capture_type: Type of capture performed.
        record_count: Number of records saved.
        chart_paths: List of generated chart paths.
        success: Whether capture was successful.
        error: Error message if failed.
    """

    def __init__(
        self,
        capture_type: str,
        record_count: int = 0,
        chart_paths: Optional[List[str]] = None,
        success: bool = True,
        error: Optional[str] = None
    ):
        self.capture_type = capture_type
        self.record_count = record_count
        self.chart_paths = chart_paths or []
        self.success = success
        self.error = error


class DatabaseCaptureService:
    """
    Service for orchestrating database capture operations.

    This service provides high-level methods for capturing different
    types of on-chain data and generating visualizations.
    """

    def __init__(
        self,
        repository: Optional[DatabaseRepository] = None,
        progress_callback: Optional[Callable[[str], None]] = None
    ):
        """
        Initialize capture service.

        Args:
            repository: Database repository. Creates new if not provided.
            progress_callback: Optional callback for progress updates.
        """
        self.repository = repository or DatabaseRepository()
        self.progress_callback = progress_callback

    def emit_progress(self, message: str) -> None:
        """Emit progress update."""
        if self.progress_callback:
            self.progress_callback(message)
        logger.info(message)

    def capture(
        self,
        capture_type: str,
        currency: str,
        generate_charts: bool = True
    ) -> CaptureResult:
        """
        Perform a single capture operation.

        Args:
            capture_type: Type of capture (snapshot, max_pain, etc.).
            currency: Currency symbol (ETH, BTC).
            generate_charts: Whether to generate charts after capture.

        Returns:
            CaptureResult with operation details.
        """
        try:
            captured_at = datetime.now()

            with DeribitApiService() as service:
                self.emit_progress(f"Fetching data for {currency}...")

                # Fetch book summary
                raw_data = service.get_book_summary(currency=currency, kind="option")
                self.emit_progress(f"Received {len(raw_data)} instruments")

                # Create analyzer
                analyzer = OnChainAnalyzer(raw_data, currency)
                analyzer.parse_instruments()

                # Get strategy for capture type
                strategy = get_capture_strategy(
                    capture_type=capture_type,
                    repository=self.repository,
                    service=service,
                    currency=currency,
                    progress_callback=self.progress_callback
                )

                # Capture data
                record_count = strategy.capture(analyzer, raw_data, captured_at)

                # Generate charts if requested
                chart_paths = []
                if generate_charts:
                    chart_paths = strategy.generate_charts(analyzer)

                return CaptureResult(
                    capture_type=capture_type,
                    record_count=record_count,
                    chart_paths=chart_paths,
                    success=True
                )

        except Exception as e:
            logger.exception(f"Error during {capture_type} capture")
            return CaptureResult(
                capture_type=capture_type,
                success=False,
                error=str(e)
            )

    def capture_all(
        self,
        currency: str,
        generate_charts: bool = True
    ) -> List[CaptureResult]:
        """
        Capture all data types sequentially.

        Args:
            currency: Currency symbol (ETH, BTC).
            generate_charts: Whether to generate charts after each capture.

        Returns:
            List of CaptureResult for each capture type.
        """
        capture_types = ["snapshot", "max_pain", "open_interest", "volume", "levels", "gex_dex"]
        results = []

        for capture_type in capture_types:
            self.emit_progress(f"Starting {capture_type} capture...")
            result = self.capture(capture_type, currency, generate_charts)
            results.append(result)

            if result.success:
                self.emit_progress(
                    f"{capture_type} complete: {result.record_count} records, "
                    f"{len(result.chart_paths)} charts"
                )
            else:
                self.emit_progress(f"{capture_type} failed: {result.error}")

        return results

    def get_available_expirations(self, currency: str, table: str) -> List[str]:
        """
        Get available expirations for a table.

        Args:
            currency: Currency symbol.
            table: Table name.

        Returns:
            List of expiration strings.
        """
        return self.repository.get_available_expirations(currency, table)
