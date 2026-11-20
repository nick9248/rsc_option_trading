"""
Writes the unified dashboard (market report + on-chain dashboard of one run as ONE self-contained HTML file).
"""

import logging
from pathlib import Path

from coding.core.analytics.unified_dashboard import build_unified_dashboard_page

logger = logging.getLogger(__name__)

UNIFIED_DASHBOARD_FILE_NAME = "unified_dashboard.html"


class UnifiedDashboardService:
    """Reads the two pages of a run, merges them and saves ``unified_dashboard.html``."""

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
                                            Path(onchain_dashboard_path).read_text(encoding="utf-8"), currency)
        path = Path(folder) / UNIFIED_DASHBOARD_FILE_NAME
        path.write_text(page, encoding="utf-8")
        logger.info("unified dashboard written: %s (%d bytes)", path, len(page.encode("utf-8")))
        return path
