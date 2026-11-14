"""
Builds a ``DecisionReport`` for one currency from the existing services (read-only) and optionally stores it.

Each source is read through the service that already computes it (``ExpiryOpenInterestService``, the MenthorQ replica,
``DealerFlowLevelsService``, ``OptionFlowService``, ``TakerFlowChartService``, the stored liquidation snapshots) with ONE
fixed clock reading, so every source describes the same instant. A source that cannot be computed (``LookupError``: no
stored data) is listed under "not computed" and the report is built without it; programming errors are not caught.

Point in time: the chain snapshot, index price, option flow window, taker ratio and liquidation snapshot are all read at or
before the clock. Two inputs are not yet bounded by it (so a replay of a past instant would see later data there):
``DealerFlowLevelsService`` sums the trades up to the newest stored one, and ``get_latest_trade_time`` is the newest trade
overall (the extractor clamps it to the window end). For live use (clock = now) both are point in time.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, List, Optional

from coding.core.analytics.decision import decision_config as config
from coding.core.analytics.decision.decision_report import DecisionReport
from coding.core.analytics.decision.evidence import EvidenceItem
from coding.core.analytics.decision.scenario_engine import build_decision_report
from coding.core.analytics.menthorq_replication_calculator import replicate_menthorq, select_drawn_levels
from coding.core.database.repository import DatabaseRepository
from coding.service.decision import evidence_extractors as extractors
from coding.service.futures_flow.taker_flow_chart_service import TakerFlowChartService
from coding.service.futures_flow.taker_flow_settings import SYMBOLS
from coding.service.on_chain.expiry_open_interest_service import ExpiryOpenInterestReport, ExpiryOpenInterestService
from coding.service.on_chain.menthorq_input_loader import MenthorqInputLoader
from coding.service.option_flow.dealer_flow_levels_service import DealerFlowLevelsService
from coding.service.option_flow.level_overview_service import LIQUIDATION_SYMBOLS
from coding.service.option_flow.option_flow_service import OptionFlowService

logger = logging.getLogger(__name__)


class DecisionMakerService:
    """Collects the evidence of every source and runs the scenario engine."""

    def __init__(self, repository: Optional[DatabaseRepository] = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        """
        Args:
            repository: Database access (the configured database by default). ``build_report`` only reads;
                ``save_snapshot`` writes one row to ``decision_snapshots``.
            clock: Wall clock (UTC-aware); read once per report.
        """
        self.repository = repository or DatabaseRepository()
        self.clock = clock

    def build_report(self, currency: str) -> DecisionReport:
        """
        The report of ``currency`` as of one clock reading.

        Raises:
            LookupError: when no chain snapshot or index price is stored (there is no spot to build a map around).
        """
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("the clock must be timezone-aware (UTC)")
        fixed_clock = lambda: now   # noqa: E731 - every service sees the same instant
        open_interest = ExpiryOpenInterestService(self.repository, fixed_clock).open_interest(currency)
        evidence: List[EvidenceItem] = []
        not_computed: List[str] = []
        for name, collect in (
                ("MenthorQ replica", lambda: self._menthorq(currency, now)),
                ("dealer GEX", lambda: self._dealer(currency, now, open_interest, fixed_clock, not_computed)),
                ("option flow", lambda: self._option_flow(currency, now, fixed_clock)),
                ("futures taker ratio", lambda: self._taker_ratio(currency, now, fixed_clock)),
                ("liquidation estimate", lambda: self._liquidation(currency, now))):
            try:
                items = collect()
            except LookupError as error:
                not_computed.append(f"{name}: {error}")
                continue
            logger.info("%s %s: %d evidence items", currency, name, len(items))
            evidence.extend(items)
        context = {"open_interest_by_expiry": extractors.open_interest_context(open_interest.rows),
                   "chain_snapshot": open_interest.captured_at.isoformat()}
        return build_decision_report(currency, now, open_interest.index_price, evidence, not_computed, context)

    def save_snapshot(self, report: DecisionReport) -> bool:
        """Store the report in ``decision_snapshots`` (never rewritten). True when a row was inserted."""
        return self.repository.save_decision_snapshot(report.currency, report.as_of, report.reference_price,
                                                      report.to_dict())

    def _menthorq(self, currency: str, now: datetime) -> List[EvidenceItem]:
        """The replica's levels, computed the way ``LevelOverviewService`` computes them."""
        inputs = MenthorqInputLoader(self.repository).load_inputs(currency, now)
        replica = replicate_menthorq(inputs["chain_rows"], inputs["index_price"], inputs["snapshot_captured_at_utc"],
                                     currency=currency)
        drawn = select_drawn_levels(replica["iv_times_open_interest"]["levels"])
        return extractors.menthorq_evidence(drawn, replica["settings"]["expirations"], inputs["snapshot_captured_at_utc"], now)

    def _dealer(self, currency: str, now: datetime, open_interest: ExpiryOpenInterestReport,
                clock: Callable[[], datetime], not_computed: List[str]) -> List[EvidenceItem]:
        """Dealer GEX of the nearest monthly expiry and of the expiry with the most open interest."""
        service = DealerFlowLevelsService(self.repository, clock)
        rows = {row.expiry: row for row in open_interest.rows}
        items: List[EvidenceItem] = []
        expiries = extractors.chosen_expiries(open_interest.rows)
        if not expiries:
            raise LookupError("no unexpired expiry with open interest in the chain snapshot")
        for expiry in expiries:
            try:
                levels = service.levels(currency, expiry)
            except LookupError as error:
                not_computed.append(f"dealer GEX {expiry}: {error}")
                continue
            items.extend(extractors.dealer_evidence(levels, rows.get(expiry), now))
        return items

    def _option_flow(self, currency: str, now: datetime, clock: Callable[[], datetime]) -> List[EvidenceItem]:
        """Flow strikes and net taker flow (screen, single-leg blocks, multi-leg blocks) of the last window."""
        hours = config.OPTION_FLOW_WINDOW_HOURS
        report = OptionFlowService(self.repository, clock).strike_flow(currency, hours, end=now)
        if report.flow.trades_used == 0:
            raise LookupError(f"no option trades in the last {hours} h (sync the database)")
        start_ms, end_ms = int(report.window_start.timestamp() * 1000), int(report.window_end.timestamp() * 1000)
        legs = self.repository.get_block_trade_legs_between(currency, start_ms, end_ms)
        return (extractors.flow_strike_evidence(report.flow, report.newest_trade, report.window_end, hours)
                + extractors.option_flow_evidence(report.flow, legs, report.newest_trade, report.window_end, hours))

    def _taker_ratio(self, currency: str, now: datetime, clock: Callable[[], datetime]) -> List[EvidenceItem]:
        """The latest rolling 24 h taker ratio against its 30-day band."""
        symbol = f"{currency}USDT"
        if symbol not in SYMBOLS:
            raise LookupError(f"no taker flow is collected for {symbol}")
        data = TakerFlowChartService(self.repository, symbols=(symbol,), clock=clock).figure_data(symbol)
        item, reason = extractors.taker_ratio_evidence(symbol, data.rolling_points, now)
        if item is None:
            raise LookupError(reason or "not computed")
        return [item]

    def _liquidation(self, currency: str, now: datetime) -> List[EvidenceItem]:
        """The newest liquidation estimate snapshot computed in the last 24 h, at or before the clock."""
        symbol = LIQUIDATION_SYMBOLS.get(currency)
        if symbol is None:
            raise LookupError(f"no liquidation estimate for {currency}")
        hours = config.LIQUIDATION_SNAPSHOT_MAXIMUM_AGE_HOURS
        snapshots = self.repository.get_liquidation_heatmap_snapshots_between(
            symbol, now - timedelta(hours=hours), now + timedelta(microseconds=1))
        if not snapshots:
            raise LookupError(f"no {symbol} snapshot computed in the last {hours} h")
        return extractors.liquidation_evidence(snapshots[-1])


def summary_lines(report: DecisionReport) -> List[str]:
    """A readable summary of a report, one line per entry (for logging)."""
    lines = [f"{report.currency} decision report as of {report.as_of:%Y-%m-%d %H:%M} UTC, spot {report.reference_price:,.2f}",
             f"Score scale: {report.score_scale}"]
    price_map = report.price_map
    for name, level in (("support", price_map.support), ("resistance", price_map.resistance),
                        ("next below", price_map.next_below), ("next above", price_map.next_above)):
        lines.append(f"  {name}: " + ("unmapped" if level is None else
                                       f"{level.price:,.0f} [{level.low:,.0f}-{level.high:,.0f}] weight {level.weight:.2f} "
                                       f"groups {', '.join(level.input_groups)}"))
    for scenario in report.scenarios:
        lines.append(f"Scenario '{scenario.title}': score {scenario.evidence_score:.1f}, confidence {scenario.confidence} "
                     f"(support {scenario.support:.3f}, opposition {scenario.opposition:.3f})")
        lines.extend(f"    {reason}" for reason in scenario.confidence_reasons)
    lines.append("Decisions:")
    lines.extend(f"  - [{item.kind}] {item.text}" for item in report.decisions)
    lines.append(f"Evidence ({len(report.evidence)} items):")
    for item in report.evidence:
        where = f" at {item.price:,.0f}" if item.price is not None else ""
        lean = item.direction or item.regime or ""
        lines.append(f"  {item.id} [{item.input_group}, {item.kind}{where} {lean}] strength {item.strength:.2f} "
                     f"quality {item.quality:.2f}")
    lines.append(report.disclaimer)
    return lines
