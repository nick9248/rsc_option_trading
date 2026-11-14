"""
Generates the market report of one currency: first brings the local database in step with the VPS when it is behind, then
builds the five sections and writes ONE self-contained HTML file to ``outputs/report/``.

Sync rule (``VpsSyncService.sync_if_behind``): the newest watermark (the column the sync itself uses) of the tables the
report reads -- historical_trades, snapshots, taker_flow_hourly, liquidation_heatmap_snapshots -- is compared between the
VPS (read-only query) and the local database. When the VPS is ahead on any of them, or one cannot be compared, the normal
incremental sync of all tables runs through the same tunnel. When the check itself fails (no SSH, VPS down), the report is
still built from the local data and the header says so.

Failure isolation: a section that raises becomes a section that explains what failed and how to fix it; the others are
unaffected. Every service sees the same report instant.
"""

import logging
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, List, Optional, Protocol, Sequence, Tuple

from plotly.offline import get_plotlyjs

from coding.core.analytics.report.how_to_read import REPORT_HELP, SECTION_HELP
from coding.core.analytics.report.report_page import (
    ReportHeader, ReportSection, SectionFailure, build_report_page, format_utc, sentence)
from coding.core.database.repository import DatabaseRepository
from coding.service.on_chain.menthorq_input_loader import MissingIndexPriceError
from coding.service.database.vps_sync_service import REPORT_SYNC_TABLES, SyncCheckResult, VpsSyncService
from coding.service.report.report_sections import SECTION_BUILDERS, SectionContext

logger = logging.getLogger(__name__)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_DIRECTORY = REPOSITORY_ROOT / "outputs" / "report"
CURRENCIES = ("BTC", "ETH")
SYNC_HINT = "Sync the database (Database tab or scripts/sync_from_vps.py) and generate the report again."

Builder = Tuple[str, str, Callable[[SectionContext], ReportSection]]


class Synchronizer(Protocol):
    """What the report needs from the sync machinery (``VpsSyncService``; a fake in tests)."""

    def sync_if_behind(self, table_names: Sequence[str] = REPORT_SYNC_TABLES,
                       progress_callback: Optional[Callable[[str], None]] = None,
                       should_stop: Optional[Callable[[], bool]] = None) -> SyncCheckResult:
        """Compare the newest rows with the VPS and sync when the VPS is ahead."""
        ...


class ReportStopped(Exception):
    """The report was stopped on request (the application is closing); no file was written."""


@dataclass
class SyncStep:
    """What happened in the sync step, for the status line and the report header."""

    summary: str                     # one plain sentence
    sync_ran: Optional[bool]         # None when the check could not be made or was skipped
    failed: bool = False


@dataclass
class SectionOutcome:
    """One section's result for the status line."""

    key: str
    title: str
    built: bool
    message: str                     # "built" or what failed


@dataclass
class ReportResult:
    """What one generation produced."""

    path: Path
    currency: str
    generated_at: datetime
    sync: SyncStep
    sections: List[SectionOutcome] = field(default_factory=list)
    size_bytes: int = 0

    @property
    def failed_sections(self) -> List[str]:
        """Titles of the sections that could not be built."""
        return [outcome.title for outcome in self.sections if not outcome.built]


def is_missing_data(error: Exception) -> bool:
    """
    True for the errors the services raise when data is missing (a plain ``LookupError``, or ``MissingIndexPriceError``).
    ``KeyError`` and ``IndexError`` are lookup errors too, but they are programming errors, not missing data.
    """
    return type(error) is LookupError or isinstance(error, MissingIndexPriceError)


def failure_for(title: str, error: Exception) -> SectionFailure:
    """A plain explanation of a failed section and how to fix it."""
    message = str(error) or type(error).__name__
    if is_missing_data(error):
        return SectionFailure(sentence(message.rstrip(".")) + ".", SYNC_HINT)
    return SectionFailure(f"{title} failed with {type(error).__name__}: {message.rstrip('.')}.",
                          "This is unexpected; the log (output/log/) has the details. Check that the local database is running "
                          "on localhost:5433, then generate the report again.")


class ReportService:
    """Sync check, sections, one HTML file."""

    def __init__(self, repository_factory: Callable[[], DatabaseRepository] = DatabaseRepository,
                 synchronizer: Optional[Synchronizer] = None,
                 output_directory: Path = DEFAULT_OUTPUT_DIRECTORY,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 builders: Sequence[Builder] = tuple(SECTION_BUILDERS),
                 plotly_javascript: Callable[[], str] = get_plotlyjs):
        """
        Args:
            repository_factory: Builds the database access (after the sync, so it reads the synced data).
            synchronizer: Sync machinery; ``VpsSyncService()`` by default. Pass ``skip_sync=True`` to ``generate`` to skip.
            output_directory: Where the HTML file is written.
            clock: Wall clock (UTC-aware); the whole report is as of one reading.
            builders: The sections as (key, title, builder), in tab order.
            plotly_javascript: Returns the plotly.js source to inline.
        """
        self.repository_factory = repository_factory
        self.synchronizer = synchronizer if synchronizer is not None else VpsSyncService()
        self.output_directory = output_directory
        self.clock = clock
        self.builders = list(builders)
        self.plotly_javascript = plotly_javascript

    def check_and_sync(self, progress: Callable[[str], None],
                       should_stop: Optional[Callable[[], bool]] = None) -> SyncStep:
        """Bring the local database in step with the VPS when it is behind; never raises."""
        progress("Checking whether the local database is behind the VPS...")
        try:
            result = self.synchronizer.sync_if_behind(REPORT_SYNC_TABLES, progress, should_stop)
        except Exception as error:
            logger.exception("Sync check failed")
            return SyncStep(f"Sync check failed ({type(error).__name__}: {error}); the report uses the local data as it is.",
                            None, failed=True)
        failed = result.sync_result is not None and not result.sync_result.success
        return SyncStep(result.summary_message, result.sync_ran, failed=failed)

    def generate(self, currency: str, progress: Optional[Callable[[str], None]] = None,
                 skip_sync: bool = False, should_stop: Optional[Callable[[], bool]] = None) -> ReportResult:
        """
        Build and write the report.

        Args:
            currency: "BTC" or "ETH".
            progress: Called with one line per step.
            skip_sync: Do not contact the VPS (the header says the sync was skipped).
            should_stop: Asked between sync tables and between sections; True stops the run.

        Raises:
            ReportStopped: ``should_stop`` returned True.
            ValueError: for an unknown currency.
            OSError: when the file cannot be written.
        """
        if currency not in CURRENCIES:
            raise ValueError(f"currency must be one of {CURRENCIES}, not {currency!r}")
        emit = progress or (lambda message: logger.info(message))
        sync = (SyncStep("Sync skipped: the VPS was not contacted; the report uses the local data as it is.", None)
                if skip_sync else self.check_and_sync(emit, should_stop))
        emit(sync.summary)
        stop_requested = should_stop or (lambda: False)

        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("the clock must return a timezone-aware time")
        repository = self.repository_factory()
        sections: List[ReportSection] = []
        outcomes: List[SectionOutcome] = []
        with tempfile.TemporaryDirectory(prefix="market_report_") as scratch:
            for key, title, builder in self.builders:
                if stop_requested():
                    raise ReportStopped(f"stopped before {title}")
                emit(f"Building {title}...")
                context = SectionContext(currency, now, repository, Path(scratch) / key)
                try:
                    section = builder(context)
                    outcomes.append(SectionOutcome(key, title, True, "built"))
                except Exception as error:
                    logger.exception("Report section %s failed", title)
                    failure = failure_for(title, error)
                    section = ReportSection(key=key, title=title, failure=failure)
                    outcomes.append(SectionOutcome(key, title, False, failure.what))
                section.help = SECTION_HELP.get(key)          # also on a failed section
                sections.append(section)

        header = ReportHeader(currency, now, self._freshness(repository, currency, now, sync), help=REPORT_HELP)
        emit("Writing the report file...")
        javascript = self.plotly_javascript() if any(section.figures for section in sections if not section.failed) else None
        page = build_report_page(header, sections, javascript)
        self.output_directory.mkdir(parents=True, exist_ok=True)
        path = self.output_directory / f"{currency.lower()}_market_report_{now:%Y%m%dT%H%M%S}Z.html"
        path.write_text(page, encoding="utf-8")
        result = ReportResult(path, currency, now, sync, outcomes, path.stat().st_size)
        logger.info("Wrote %s (%.1f MB)", path, result.size_bytes / 1e6)
        return result

    @staticmethod
    def _freshness(repository: DatabaseRepository, currency: str, now: datetime, sync: SyncStep) -> List[Tuple[str, str]]:
        """Header facts: newest stored trade, newest chain snapshot and what the sync step did."""
        facts: List[Tuple[str, str]] = []
        for label, read in (("Newest stored trade", lambda: repository.get_latest_trade_time(currency)),
                            ("Chain snapshot", lambda: repository.get_latest_option_chain_capture_at_or_before(currency, now))):
            try:
                facts.append((label, format_utc(read(), missing="none stored")))
            except Exception as error:
                logger.exception("Could not read %s", label)
                facts.append((label, f"not read ({type(error).__name__})"))
        if sync.sync_ran is True:
            database = "synced from the VPS just now" + (" (with errors)" if sync.failed else "")
        elif sync.sync_ran is False:
            database = "already in step with the VPS"
        elif sync.failed:
            database = "sync check failed; local data as it is"
        else:
            database = "sync skipped; local data as it is"
        facts.append(("Database", database))
        return facts
