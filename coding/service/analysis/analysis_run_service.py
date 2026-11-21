"""
One "Start Analysis" run: a thin orchestration of services that already exist. It owns no analysis logic.

Order: ONE database sync for the whole run, then for each currency (in the order given) the on-chain analysis, the on-chain
dashboard, the market report and the unified dashboard (the one page the user opens: report and on-chain dashboard merged).
The dashboard needs the analysis result, so when the analysis failed the dashboard is "skipped"; the report does not depend
on the analysis and still runs. The unified dashboard needs both files, so it is "skipped" when either is missing. When it is written, the two files are
deleted (it contains all of both); when it fails, they stay for inspection.

Failure isolation: a step that raises is logged, reported in plain words and recorded; the remaining steps still run. A stop
request (``should_stop``) is checked between steps; the remaining steps are then recorded as stopped.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Protocol, Sequence

from coding.service.report.report_service import CURRENCIES, ReportResult, ReportService, ReportStopped, SyncStep

logger = logging.getLogger(__name__)

STEP_SYNC = "database sync"
STEP_ANALYSIS = "on-chain analysis"
STEP_DASHBOARD = "on-chain dashboard"
STEP_REPORT = "report"
STEP_UNIFIED = "unified dashboard"

STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"
STATUS_STOPPED = "stopped"


class WorkflowRunner(Protocol):
    """What this service needs from ``OnChainWorkflowService(currency)``."""

    def run(self, progress_callback: Optional[Callable[[str], None]] = None):
        """Run the on-chain analysis; returns an object with ``result`` and ``bundle_path``."""
        ...


class DashboardWriter(Protocol):
    """What this service needs from ``OnChainDashboardService``."""

    def write(self, result, folder: Path) -> Path:
        """Write ``dashboard.html`` into ``folder`` and return its path."""
        ...


class UnifiedDashboardWriter(Protocol):
    """What this service needs from ``UnifiedDashboardService``."""

    def write(self, currency: str, market_report_path: Path, onchain_dashboard_path: Path, folder: Path) -> Path:
        """Write ``unified_dashboard.html`` into ``folder`` and return its path."""
        ...


def _default_workflow_factory(currency: str) -> WorkflowRunner:
    """The real on-chain workflow of one currency (imported lazily: it pulls in the API and database code)."""
    from coding.service.on_chain.on_chain_workflow_service import OnChainWorkflowService
    return OnChainWorkflowService(currency)


def _default_unified_dashboard_service_factory() -> UnifiedDashboardWriter:
    """The real unified dashboard writer (imported lazily)."""
    from coding.service.analysis.unified_dashboard_service import UnifiedDashboardService
    return UnifiedDashboardService()


def _default_dashboard_service_factory() -> DashboardWriter:
    """The real dashboard writer (imported lazily)."""
    from coding.service.analysis.onchain_dashboard_service import OnChainDashboardService
    return OnChainDashboardService()


@dataclass
class StepOutcome:
    """What happened in one step."""

    name: str
    currency: Optional[str]              # None for the database sync
    status: str                          # "succeeded" | "failed" | "skipped" | "stopped"
    message: str
    path: Optional[Path] = None          # folder (analysis), dashboard.html or report file; None when not produced

    @property
    def label(self) -> str:
        """Plain name, e.g. "BTC on-chain analysis"."""
        return f"{self.currency} {self.name}" if self.currency else self.name


@dataclass
class AnalysisRunResult:
    """All step outcomes of one run, in step order."""

    steps: List[StepOutcome] = field(default_factory=list)

    def _path(self, name: str, currency: str) -> Optional[Path]:
        for step in self.steps:
            if step.name == name and step.currency == currency and step.status == STATUS_SUCCEEDED:
                return step.path
        return None

    def dashboard_path(self, currency: str) -> Optional[Path]:
        """``dashboard.html`` of the currency, or None when it was not written. It is deleted once the unified dashboard holds it."""
        return self._path(STEP_DASHBOARD, currency)

    def report_path(self, currency: str) -> Optional[Path]:
        """The market report HTML file of the currency, or None when it was not written. It is deleted once the unified dashboard holds it."""
        return self._path(STEP_REPORT, currency)

    def unified_dashboard_path(self, currency: str) -> Optional[Path]:
        """``unified_dashboard.html`` of the currency (the page the GUI opens), or None when it was not written."""
        return self._path(STEP_UNIFIED, currency)

    def report_text_folder(self, currency: str) -> Optional[Path]:
        """Folder holding ``report.txt`` of the on-chain analysis, or None when the analysis did not finish."""
        return self._path(STEP_ANALYSIS, currency)

    @property
    def failed_steps(self) -> List[StepOutcome]:
        """The steps that raised."""
        return [step for step in self.steps if step.status == STATUS_FAILED]

    @property
    def all_succeeded(self) -> bool:
        """True when the run has steps and every one of them succeeded."""
        return bool(self.steps) and all(step.status == STATUS_SUCCEEDED for step in self.steps)

    def summary(self) -> str:
        """Plain multi-line text: one line per step, then a count."""
        lines = ["Analysis run summary:"]
        for step in self.steps:
            lines.append(f"  {step.status.upper():<10} {step.label}: {step.message}")
        counts = {status: sum(1 for step in self.steps if step.status == status)
                  for status in (STATUS_SUCCEEDED, STATUS_FAILED, STATUS_SKIPPED, STATUS_STOPPED)}
        lines.append(f"{counts[STATUS_SUCCEEDED]} succeeded, {counts[STATUS_FAILED]} failed, "
                     f"{counts[STATUS_SKIPPED]} skipped, {counts[STATUS_STOPPED]} not run (stopped).")
        return "\n".join(lines)


class AnalysisRunService:
    """Sync once, then analysis, dashboard, report and unified dashboard per currency, each step isolated from the others."""

    def __init__(self, report_service_factory: Callable[[], ReportService] = ReportService,
                 workflow_factory: Callable[[str], WorkflowRunner] = _default_workflow_factory,
                 dashboard_service_factory: Callable[[], DashboardWriter] = _default_dashboard_service_factory,
                 unified_dashboard_service_factory: Callable[[], UnifiedDashboardWriter] = _default_unified_dashboard_service_factory):
        """
        Args:
            report_service_factory: Builds the report service (sync check and report).
            workflow_factory: ``currency -> object with run(progress_callback)`` (the on-chain workflow).
            dashboard_service_factory: Builds the object with ``write(result, folder) -> Path``.
            unified_dashboard_service_factory: Builds the object with
                ``write(currency, market_report_path, onchain_dashboard_path, folder) -> Path``.
        """
        self.report_service_factory = report_service_factory
        self.workflow_factory = workflow_factory
        self.dashboard_service_factory = dashboard_service_factory
        self.unified_dashboard_service_factory = unified_dashboard_service_factory

    def run(self, currencies: Sequence[str], progress: Callable[[str], None],
            should_stop: Optional[Callable[[], bool]] = None) -> AnalysisRunResult:
        """
        Run all steps for the currencies, in the order given.

        Args:
            currencies: A non-empty subset of ("BTC", "ETH") without repeats.
            progress: Called with plain-text lines (step start, step end, final summary).
            should_stop: Asked between steps and passed to the sync and the report; True stops the run.

        Raises:
            ValueError: for an empty, unknown or repeated currency (nothing is run).
        """
        currency_list = list(currencies)
        if not currency_list:
            raise ValueError("choose at least one currency")
        unknown = [currency for currency in currency_list if currency not in CURRENCIES]
        if unknown:
            raise ValueError(f"unknown currency {unknown}; choose from {CURRENCIES}")
        if len(set(currency_list)) != len(currency_list):
            raise ValueError(f"currency listed twice: {currency_list}")

        stop_requested = should_stop or (lambda: False)
        plan = [(STEP_SYNC, None)]
        for currency in currency_list:
            plan += [(STEP_ANALYSIS, currency), (STEP_DASHBOARD, currency), (STEP_REPORT, currency), (STEP_UNIFIED, currency)]
        total = len(plan)

        run_result = AnalysisRunResult()
        report_service: Optional[ReportService] = None
        analysis_outputs = {}                # currency -> workflow output, only for finished analyses
        stopped = False

        for index, (name, currency) in enumerate(plan, start=1):
            label = f"{currency} {name}" if currency else name
            if stopped or stop_requested():
                stopped = True
                run_result.steps.append(StepOutcome(name, currency, STATUS_STOPPED, "not run: stopped"))
                continue
            if name == STEP_DASHBOARD and currency not in analysis_outputs:
                run_result.steps.append(StepOutcome(name, currency, STATUS_SKIPPED, "skipped: analysis failed"))
                progress(f"Step {index}/{total}: {label} skipped: analysis failed")
                continue

            if name == STEP_UNIFIED and (run_result.dashboard_path(currency) is None or run_result.report_path(currency) is None):
                run_result.steps.append(StepOutcome(name, currency, STATUS_SKIPPED, "skipped: the dashboard or the report is missing"))
                progress(f"Step {index}/{total}: {label} skipped: the dashboard or the report is missing")
                continue

            progress(f"Step {index}/{total}: {label} ...")
            try:
                if name == STEP_SYNC:
                    report_service = self._report_service(report_service)
                    outcome = self._sync(report_service, progress, stop_requested)
                elif name == STEP_ANALYSIS:
                    output = self.workflow_factory(currency).run(progress)
                    analysis_outputs[currency] = output
                    outcome = StepOutcome(name, currency, STATUS_SUCCEEDED, "done", output.bundle_path)
                elif name == STEP_DASHBOARD:
                    output = analysis_outputs[currency]
                    path = self.dashboard_service_factory().write(output.result, output.bundle_path)
                    outcome = StepOutcome(name, currency, STATUS_SUCCEEDED, "done", path)
                elif name == STEP_UNIFIED:
                    report_file = run_result.report_path(currency)
                    dashboard_file = run_result.dashboard_path(currency)
                    path = self.unified_dashboard_service_factory().write(currency, report_file, dashboard_file, dashboard_file.parent)
                    outcome = StepOutcome(name, currency, STATUS_SUCCEEDED, "done", path)
                    self._remove_merged_pages(report_file, dashboard_file)
                else:
                    report_service = self._report_service(report_service)
                    report: ReportResult = report_service.generate(currency, progress, skip_sync=True,
                                                                   should_stop=stop_requested)
                    outcome = StepOutcome(name, currency, STATUS_SUCCEEDED, "done", report.path)
            except ReportStopped:
                stopped = True
                outcome = StepOutcome(name, currency, STATUS_STOPPED, "stopped")
            except Exception as error:
                logger.exception("Analysis run step failed: %s", label)
                outcome = StepOutcome(name, currency, STATUS_FAILED, f"{type(error).__name__}: {error}")
                if name == STEP_ANALYSIS:
                    analysis_outputs.pop(currency, None)

            run_result.steps.append(outcome)
            if outcome.status == STATUS_SUCCEEDED:
                progress(f"Step {index}/{total}: {label} done")
            elif outcome.status == STATUS_FAILED:
                progress(f"FAILED  {label}: {outcome.message}")
            else:
                progress(f"Step {index}/{total}: {label} stopped")

        progress(run_result.summary())
        return run_result

    @staticmethod
    def _remove_merged_pages(*pages: Path) -> None:
        """
        Delete the pages that were merged into the unified dashboard (it holds all of their content). A file that cannot be
        deleted is logged; it never fails the run.
        """
        for page in pages:
            try:
                page.unlink(missing_ok=True)
                logger.info("Removed %s (merged into the unified dashboard)", page)
            except OSError as error:
                logger.warning("Could not remove %s: %s", page, error)

    def _report_service(self, existing: Optional[ReportService]) -> ReportService:
        """The one report service of this run (built on first use)."""
        return existing if existing is not None else self.report_service_factory()

    @staticmethod
    def _sync(report_service: ReportService, progress: Callable[[str], None],
              stop_requested: Callable[[], bool]) -> StepOutcome:
        """The single database sync; a failed sync is reported but does not stop anything."""
        sync: SyncStep = report_service.check_and_sync(progress, stop_requested)
        if sync.failed:
            return StepOutcome(STEP_SYNC, None, STATUS_FAILED, sync.summary)
        return StepOutcome(STEP_SYNC, None, STATUS_SUCCEEDED, sync.summary)
