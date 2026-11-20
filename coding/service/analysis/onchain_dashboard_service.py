"""
Writes the on-chain dashboard (one self-contained HTML file) next to the text report of an analysis run.
"""

import logging
from pathlib import Path
from typing import Callable

from plotly.offline import get_plotlyjs

from coding.core.analytics.onchain_dashboard import build_onchain_dashboard_page
from coding.core.analytics.results.analysis_result import OnChainAnalysisResult

logger = logging.getLogger(__name__)

DASHBOARD_FILE_NAME = "dashboard.html"


class OnChainDashboardService:
    """Builds the dashboard page from a typed analysis result and saves it as ``dashboard.html``."""

    def __init__(self, plotly_javascript: Callable[[], str] = get_plotlyjs):
        """
        Args:
            plotly_javascript: Returns the plotly.js source to inline (tests pass a small stand-in).
        """
        self._plotly_javascript = plotly_javascript

    def write(self, result: OnChainAnalysisResult, folder: Path) -> Path:
        """
        Write ``dashboard.html`` into ``folder`` (utf-8). Other files in the folder, such as ``report.txt``, are not touched.

        Args:
            result: The typed result of one on-chain analysis run.
            folder: The report folder of the run; it must exist.

        Returns:
            The path of the written file.
        """
        page = build_onchain_dashboard_page(result, self._plotly_javascript())
        path = Path(folder) / DASHBOARD_FILE_NAME
        path.write_text(page, encoding="utf-8")
        logger.info("on-chain dashboard written: %s (%d bytes)", path, len(page.encode("utf-8")))
        return path
