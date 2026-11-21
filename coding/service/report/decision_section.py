"""
The "Evidence" section of the market report (key "decision"): the evidence report (price map, reference lines, evidence, caveats) for the report currency,
shown as it was produced. It no longer scores scenarios or labels confidence (removed 2026-11-21).

Nothing is computed a second way here. ``DecisionMakerService.build_report`` runs once with the report's own clock (the
report's single ``now``) and its result is rendered by ``decision_fragments``. The section never writes to the database:
``scripts/decision_maker.py --save`` is the only way a decision is recorded.
"""

import logging
from typing import TYPE_CHECKING

from coding.core.analytics.report.decision_fragments import decision_body_fragment
from coding.core.analytics.report.report_page import ReportSection, format_utc
from coding.service.decision.decision_maker_service import DecisionMakerService

if TYPE_CHECKING:       # report_sections imports this module, so the runtime import would be circular
    from coding.service.report.report_sections import SectionContext

logger = logging.getLogger(__name__)

NOT_RECORDED_NOTE = ("This report does not record the decision; scripts/decision_maker.py --save stores a snapshot in "
                     "decision_snapshots.")


def build_decision_section(context: "SectionContext") -> ReportSection:
    """
    The price map, reference lines, the full evidence table and the caveats of the evidence report.

    Raises:
        LookupError: when the decision maker has no spot to build a map around (``ReportService`` shows it as missing data).
    """
    report = DecisionMakerService(context.repository, context.clock).build_report(context.currency)
    logger.info("%s evidence report: %d evidence items, %d sources not computed", context.currency, len(report.evidence),
                len(report.not_computed))
    return ReportSection(
        key="decision", title="Evidence",
        as_of=f"Report time {format_utc(report.as_of)}; spot {report.reference_price:,.0f}.",
        body_html=decision_body_fragment(report),
        description=("What the sources say: a price map of the levels around the spot, reference lines, and every evidence item "
                     "with its strength, quality and caveats. Levels and evidence only: not a forecast, not an order, no ranking "
                     "of scenarios."),
        notes=[NOT_RECORDED_NOTE, report.disclaimer, f"Method version {report.method_version}."])
