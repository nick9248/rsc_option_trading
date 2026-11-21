"""
Workflow facade for the on-chain analysis pipeline: fetch and analyze one currency, then save the text report.

One call (``OnChainWorkflowService.run``) returns the typed ``OnChainAnalysisResult``, the report text and the folder the
report was saved to (``output/data/onchain_analysis/<CUR>/report/<timestamp>/report.txt``). The morning-note synthesis
that used to run here (and wrote ``synthesis.txt``) is not part of this workflow any more; ``MorningNoteService`` is
only used by the health check that proves the synthesis engine still runs.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from coding.core.analytics.results.analysis_result import OnChainAnalysisResult
from coding.core.database.repository import DatabaseRepository
from coding.service.deribit.deribit_api_service import DeribitApiService
from coding.service.on_chain.chain_source import CHAIN_SOURCE_LIVE
from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ONCHAIN_OUTPUT_DIRECTORY = PROJECT_ROOT / "output" / "data" / "onchain_analysis"


@dataclass(frozen=True)
class OnChainWorkflowOutput:
    """Everything a caller of the one-call workflow needs."""

    result: OnChainAnalysisResult
    report_text: str
    # Folder the report was saved to (report.txt inside); the on-chain dashboard is written next to it.
    bundle_path: Path


def save_report(currency: str, report: str, output_directory: Path = ONCHAIN_OUTPUT_DIRECTORY) -> Path:
    """
    Save the report text into a timestamped folder: ``<output_directory>/<currency>/report/<YYYYMMDD_HHMMSS>/report.txt``.

    The folder name is local time, as before (existing folders are named that way).

    Returns:
        The folder the report was saved to.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder = output_directory / currency / "report" / timestamp
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "report.txt").write_text(report, encoding="utf-8")
    logger.info("Report saved to %s", folder)
    return folder


class OnChainWorkflowService:
    """
    "Analyze this currency end to end": ``OnChainAnalysisService.fetch_and_analyze`` and then ``save_report``.

    Dependencies are optional and constructed lazily when omitted, so a caller can do
    ``OnChainWorkflowService(currency).run(...)`` with no ``DatabaseRepository``/``DeribitApiService`` of its own.
    """

    def __init__(
        self,
        currency: str,
        api_service: Optional[DeribitApiService] = None,
        repository: Optional[DatabaseRepository] = None,
        chain_source_mode: str = CHAIN_SOURCE_LIVE,
    ) -> None:
        """
        Args:
            currency: "BTC" or "ETH".
            api_service: Deribit access (built when omitted).
            repository: Database access (built when omitted).
            chain_source_mode: ``CHAIN_SOURCE_LIVE`` (default here and in ``OnChainAnalysisService``: a direct caller keeps fetching
                live) or ``CHAIN_SOURCE_STORED``. The Analysis tab's ``AnalysisRunService`` defaults to stored.
        """
        self.currency = currency
        self._chain_source_mode = chain_source_mode
        self._api_service = api_service
        self._repository = repository

    def run(self, progress_callback: Optional[Callable[[str], None]] = None) -> OnChainWorkflowOutput:
        """
        Run the analysis for ``self.currency`` and save its report text.

        Args:
            progress_callback: Receives progress lines from ``OnChainAnalysisService.fetch_and_analyze``.

        Returns:
            ``OnChainWorkflowOutput`` with the typed result, the report text and the folder it was saved to.

        Raises:
            Whatever ``fetch_and_analyze`` or the file write raise; this facade does not swallow errors.
        """
        repository = self._repository if self._repository is not None else DatabaseRepository()

        if self._api_service is not None:
            return self._run_with_api(self._api_service, repository, progress_callback)

        with DeribitApiService(timeout=90) as api_service:
            return self._run_with_api(api_service, repository, progress_callback)

    def _run_with_api(
        self,
        api_service: DeribitApiService,
        repository: DatabaseRepository,
        progress_callback: Optional[Callable[[str], None]],
    ) -> OnChainWorkflowOutput:
        on_chain_service = OnChainAnalysisService(api_service, repository=repository, chain_source_mode=self._chain_source_mode)
        report, result = on_chain_service.fetch_and_analyze(
            currency=self.currency,
            progress_callback=progress_callback,
            return_result=True,
        )
        return OnChainWorkflowOutput(result=result, report_text=report, bundle_path=save_report(self.currency, report))
