"""
Strategy finder service for scanning multiple currencies and expirations.

This service provides high-level "find best strategies" functionality across
multiple currencies and expirations, aggregating and re-ranking globally.
"""

import logging
from typing import Callable, List, Optional

from coding.core.database.repository import DatabaseRepository
from coding.core.strategy.models import StrategyConfig, StrategySignal

from .strategy_evaluation_service import StrategyEvaluationService

logger = logging.getLogger(__name__)


class StrategyFinderService:
    """
    Service for finding best strategies across multiple currencies and expirations.

    This is the "click button → find best" automation that scans multiple
    markets and returns the globally best-ranked strategies.
    """

    def __init__(
        self,
        api_service,
        repository: DatabaseRepository,
        progress_callback: Optional[Callable[[str, int, int], None]] = None
    ):
        """
        Initialize finder service.

        Args:
            api_service: Deribit API service instance
            repository: Database repository instance
            progress_callback: Optional callback for progress updates (message, current, total)
        """
        self.api_service = api_service
        self.repository = repository
        self.progress_callback = progress_callback

        # Initialize evaluation service
        self.evaluation_service = StrategyEvaluationService(
            api_service=api_service,
            repository=repository,
            progress_callback=progress_callback
        )

        logger.info("StrategyFinderService initialized")

    def find_best_strategies(
        self,
        currencies: List[str],
        config: StrategyConfig,
        max_expirations_per_currency: int = 3
    ) -> List[StrategySignal]:
        """
        Find best strategies across multiple currencies and expirations.

        Args:
            currencies: List of currency symbols (e.g., ["BTC", "ETH"])
            config: Strategy configuration
            max_expirations_per_currency: Maximum expirations to scan per currency

        Returns:
            List of top-ranked signals across all scans
        """
        all_signals = []
        total_scans = 0

        try:
            logger.info(
                f"Starting strategy finder: currencies={currencies}, "
                f"max_expirations={max_expirations_per_currency}"
            )

            # Calculate total scans for progress reporting
            for currency in currencies:
                # Get available expirations (limited by max_expirations_per_currency)
                expirations = config.expirations

                if not expirations:
                    # If no expirations specified in config, fetch from database
                    try:
                        expirations = self.repository.get_available_expirations(
                            currency=currency,
                            table="max_pain"
                        )[:max_expirations_per_currency]
                    except Exception as e:
                        logger.warning(f"Failed to get expirations for {currency}: {e}")
                        continue
                else:
                    # Use configured expirations (limited)
                    expirations = expirations[:max_expirations_per_currency]

                total_scans += len(expirations)

            logger.info(f"Total scans to perform: {total_scans}")

            scan_count = 0

            # Scan each currency and expiration
            for currency in currencies:
                # Get expirations to scan
                expirations = config.expirations

                if not expirations:
                    try:
                        expirations = self.repository.get_available_expirations(
                            currency=currency,
                            table="max_pain"
                        )[:max_expirations_per_currency]
                    except Exception as e:
                        logger.warning(f"Failed to get expirations for {currency}: {e}")
                        continue
                else:
                    expirations = expirations[:max_expirations_per_currency]

                if not expirations:
                    logger.warning(f"No expirations found for {currency}")
                    continue

                logger.info(f"Scanning {currency}: {len(expirations)} expirations")

                # Evaluate each expiration
                for expiration in expirations:
                    scan_count += 1

                    try:
                        self._report_progress(
                            f"Scanning {currency}-{expiration}",
                            scan_count,
                            total_scans
                        )

                        result = self.evaluation_service.evaluate_strategies(
                            currency=currency,
                            expiration=expiration,
                            config=config
                        )

                        if result.success:
                            all_signals.extend(result.signals)
                            logger.info(
                                f"{currency}-{expiration}: {len(result.signals)} signals generated"
                            )
                        else:
                            logger.warning(
                                f"{currency}-{expiration}: Evaluation failed, "
                                f"{len(result.errors)} errors"
                            )

                    except Exception as e:
                        logger.error(
                            f"Failed to evaluate {currency}-{expiration}: {e}",
                            exc_info=True
                        )
                        continue

            # Re-rank globally
            logger.info(f"Re-ranking {len(all_signals)} signals globally")

            all_signals.sort(key=lambda s: s.composite_score, reverse=True)

            # Assign global ranks
            for rank, signal in enumerate(all_signals[:config.top_n], start=1):
                signal.rank = rank

            # Return top N
            top_signals = all_signals[:config.top_n]

            logger.info(
                f"Strategy finder complete: {len(top_signals)} top signals selected "
                f"from {len(all_signals)} total signals"
            )

            self._report_progress("Complete", total_scans, total_scans)

            return top_signals

        except Exception as e:
            logger.error(f"Strategy finder failed: {e}", exc_info=True)
            return []

    def _report_progress(self, message: str, current: int, total: int) -> None:
        """
        Report progress to callback if configured.

        Args:
            message: Progress message
            current: Current step
            total: Total steps
        """
        if self.progress_callback:
            try:
                self.progress_callback(message, current, total)
            except Exception as e:
                logger.warning(f"Progress callback failed: {e}")
