"""
Writes the unified dashboard (market report + on-chain dashboard of one run as ONE self-contained HTML file).
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from coding.core.analytics.unified_dashboard import build_unified_dashboard_page
from coding.core.analytics.unified_dashboard.strategy_panel import StoredSuggestion, build_strategy_panel
from coding.core.database.repository import DatabaseRepository

logger = logging.getLogger(__name__)

UNIFIED_DASHBOARD_FILE_NAME = "unified_dashboard.html"


class UnifiedDashboardService:
    """Reads the two pages of a run, merges them and saves ``unified_dashboard.html``."""

    def __init__(self, repository_factory: Optional[Callable[[], DatabaseRepository]] = None):
        """
        Args:
            repository_factory: Builds the database access (read only) for the stored strategy suggestions of the Strategy tab. It is
                called when the page is built, inside the error handling, so a database that cannot be reached shows in the tab and
                does not stop the dashboard. Without it the page has no Strategy tab.
        """
        self.repository_factory = repository_factory

    def write(self, currency: str, market_report_path: Path, onchain_dashboard_path: Path, folder: Path) -> Path:
        """
        Write ``unified_dashboard.html`` into ``folder`` (utf-8). The two input files are not changed.

        Args:
            currency: "BTC" or "ETH".
            market_report_path: The market report HTML file of the run.
            onchain_dashboard_path: The on-chain ``dashboard.html`` of the run.
            folder: Where to write; it must exist.

        Returns:
            The path of the written file.

        Raises:
            ValueError: when an input is not a page of the expected structure.
            OSError: when an input cannot be read or the output cannot be written.
        """
        page = build_unified_dashboard_page(Path(market_report_path).read_text(encoding="utf-8"),
                                            Path(onchain_dashboard_path).read_text(encoding="utf-8"), currency,
                                            strategy_panel_html=self._strategy_panel(currency))
        path = Path(folder) / UNIFIED_DASHBOARD_FILE_NAME
        path.write_text(page, encoding="utf-8")
        logger.info("unified dashboard written: %s (%d bytes)", path, len(page.encode("utf-8")))
        return path

    def _strategy_panel(self, currency: str) -> Optional[str]:
        """The Strategy tab, or None without a repository. A database error is shown in the tab, it does not stop the dashboard."""
        if self.repository_factory is None:
            return None
        now = datetime.now(timezone.utc)
        try:
            rows = self.repository_factory().get_latest_strategy_suggestions(currency)
            return build_strategy_panel([StoredSuggestion(**row) for row in rows], now, currency)
        except Exception as error:
            logger.exception("Reading the stored strategy suggestions failed")
            return build_strategy_panel([], now, currency, read_error=f"{type(error).__name__}: {error}")
