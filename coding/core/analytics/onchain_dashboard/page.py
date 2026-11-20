"""
Assembles the on-chain dashboard page from the typed ``OnChainAnalysisResult`` (never from the text report): one
self-contained HTML file with a tab per section, built with the same page infrastructure as the market report.
"""

from typing import List, Optional

from coding.core.analytics.onchain_dashboard.expiry_section import build_expiries_section
from coding.core.analytics.onchain_dashboard.formatting import as_utc, number_text, percent_text
from coding.core.analytics.onchain_dashboard.market_section import build_market_section
from coding.core.analytics.onchain_dashboard.overview_section import build_overview_section
from coding.core.analytics.onchain_dashboard.quality_section import build_quality_section
from coding.core.analytics.report.report_page import ReportHeader, ReportSection, build_report_page
from coding.core.analytics.results.analysis_result import OnChainAnalysisResult

DASHBOARD_KIND = "on-chain dashboard"


def build_onchain_dashboard_sections(result: OnChainAnalysisResult) -> List[ReportSection]:
    """The tabs, in order: Overview, Expiries, Market-wide, Data quality."""
    return [build_overview_section(result), build_expiries_section(result), build_market_section(result),
            build_quality_section(result)]


def build_onchain_dashboard_page(result: OnChainAnalysisResult, plotly_javascript: Optional[str]) -> str:
    """
    The complete dashboard page.

    Args:
        result: The typed result of one on-chain analysis run.
        plotly_javascript: The plotly.js source, inlined once (every dashboard has figures, so it is required).

    Raises:
        ValueError: when ``plotly_javascript`` is missing.
    """
    metrics = result.market_metrics
    facts = [("Spot", number_text(result.underlying_price, 2, " USD")), ("DVOL", number_text(metrics.dvol, 2)),
             ("IV rank (365 days)", percent_text(metrics.iv_rank)), ("Expiries analyzed", str(len(result.expirations)))]
    header = ReportHeader(result.currency, as_utc(result.generated_at), facts, kind=DASHBOARD_KIND)
    return build_report_page(header, build_onchain_dashboard_sections(result), plotly_javascript)
