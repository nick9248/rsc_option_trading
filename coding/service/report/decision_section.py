"""
The "Decision" section of the market report: the decision maker's report for the report currency, shown as it was produced.

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
    The scenarios, price map, decisions and the full evidence table of the decision maker.

    Raises:
        LookupError: when the decision maker has no spot to build a map around (``ReportService`` shows it as missing data).
    """
    report = DecisionMakerService(context.repository, context.clock).build_report(context.currency)
    logger.info("%s decision report: %d evidence items, top scenario %s", context.currency, len(report.evidence),
                report.top_scenario)
    return ReportSection(
        key="decision", title="Decision",
        as_of=f"Report time {format_utc(report.as_of)}; spot {report.reference_price:,.0f}.",
        body_html=decision_body_fragment(report),
        description=("What the evidence of the other tabs supports: a price map around the spot, the scenarios it allows with "
                     "an uncalibrated evidence score and an ordinal confidence label, and the evidence behind each. Decision "
                     "support, not a forecast, not an order."),
        notes=[NOT_RECORDED_NOTE, report.disclaimer, f"Method version {report.method_version}."])
