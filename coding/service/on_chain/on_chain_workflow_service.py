"""
GUI-facing workflow facade for the on-chain analysis pipeline.

refactor_design_spec.md section T9 / Decision D4: the GUI worker's three-step
orchestration (fetch+analyze -> morning-note synthesis -> save report bundle)
moves here, behind ONE call. This is a new, small facade rather than a method
on ``OnChainAnalysisService`` because ``MorningNoteService.__init__`` already
takes an ``OnChainAnalysisService`` (morning_note_service.py:27) -- putting
the workflow inside ``OnChainAnalysisService`` would create an import cycle
(``OnChainAnalysisService`` -> ``MorningNoteService`` -> ``OnChainAnalysisService``).
The facade composes both services instead; no dependency-inversion churn.

Decision D2 (§0 of the spec) motivates the mutable-builder split elsewhere in
this refactor for the same reason this facade exists: keep the frozen result
models simple by pushing composition/orchestration into the service layer.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from coding.core.analytics.results.analysis_result import OnChainAnalysisResult
from coding.core.database.repository import DatabaseRepository
from coding.service.deribit.deribit_api_service import DeribitApiService
from coding.service.morning_note.morning_note_service import MorningNoteService
from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OnChainWorkflowOutput:
    """Everything a caller of the one-call workflow needs."""

    result: OnChainAnalysisResult
    report_text: str
    synthesis_text: str
    bundle_path: Path


class OnChainWorkflowService:
    """
    Single GUI entry point for "analyze this currency end to end".

    Runs ``OnChainAnalysisService.fetch_and_analyze`` -> ``MorningNoteService.
    generate`` -> ``MorningNoteService.save_report_bundle`` as one call, so
    consumers (the GUI worker, the morning-note health checker) do not
    orchestrate three services/steps themselves.

    Dependencies are optional and constructed lazily when omitted, so a GUI
    caller can do ``OnChainWorkflowService(currency).run(...)`` with zero
    ``DatabaseRepository``/``DeribitApiService`` construction of its own,
    while a caller that already owns a repository (e.g. a health checker
    running against a specific environment's DB) can inject it and have that
    exact instance used instead of a freshly constructed one.
    """

    def __init__(
        self,
        currency: str,
        api_service: Optional[DeribitApiService] = None,
        repository: Optional[DatabaseRepository] = None,
    ) -> None:
        self.currency = currency
        self._api_service = api_service
        self._repository = repository

    def run(
        self,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> OnChainWorkflowOutput:
        """
        Run the full analyze -> synthesize -> save pipeline for ``self.currency``.

        Args:
            progress_callback: Optional callback for progress updates,
                forwarded to ``OnChainAnalysisService.fetch_and_analyze``.

        Returns:
            ``OnChainWorkflowOutput`` with the typed result, report text,
            synthesis text, and the path the report bundle was saved to.

        Raises:
            Whatever ``fetch_and_analyze``/``generate``/``save_report_bundle``
            raise -- this facade does not swallow errors; callers (the GUI
            worker, the health checker) decide how to handle failure.
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
        on_chain_service = OnChainAnalysisService(api_service, repository=repository)

        report, result = on_chain_service.fetch_and_analyze(
            currency=self.currency,
            progress_callback=progress_callback,
            return_result=True,
        )

        morning_service = MorningNoteService(on_chain_service)
        synthesis = morning_service.generate(result)
        bundle_path = morning_service.save_report_bundle(self.currency, report, synthesis)

        return OnChainWorkflowOutput(
            result=result,
            report_text=report,
            synthesis_text=synthesis,
            bundle_path=bundle_path,
        )
