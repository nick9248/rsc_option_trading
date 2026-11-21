"""
The "Overview" tab of the on-chain dashboard: market metrics, the expiries side by side, the key levels of the front-month
expiry and the historical context (percentile, z-score, regime) of the front-month metrics.
"""

import html
from typing import List, Optional

from coding.core.analytics.historical_normalizer import HistoricalNormalizer, NormalizedMetric
from coding.core.analytics.onchain_dashboard.expiry_section import key_level_rows, ordered_bundles
from coding.core.analytics.onchain_dashboard.formatting import (
    count_text, fraction_percent_text, number_text, percent_text, ratio_text, strike_text, text_or_missing, utc_text)
from coding.core.analytics.report.report_page import ReportSection, paragraph_fragment, table_fragment
from coding.core.analytics.report.section_fragments import NOT_COMPUTED
from coding.core.analytics.reporting.historical_context_formatter import METRIC_ORDER, format_metric_value, metric_label
from coding.core.analytics.results.analysis_result import ExpirationBundle, OnChainAnalysisResult


def market_metric_rows(result: OnChainAnalysisResult) -> List[List[str]]:
    """Rows [metric, value] of the currency-wide market metrics."""
    metrics = result.market_metrics
    observations = (f" ({metrics.iv_rank_observation_count} observations)"
                    if metrics.iv_rank_observation_count is not None else "")
    return [
        ["Spot", number_text(result.underlying_price, 2, " USD")],
        ["DVOL", number_text(metrics.dvol, 2)],
        ["IV percentile (365 days)", percent_text(metrics.iv_percentile)],
        ["IV rank (365 days)", percent_text(metrics.iv_rank) + (observations if metrics.iv_rank is not None else "")],
        ["Funding, current", fraction_percent_text(metrics.current_funding, 8)],
        ["Funding, 8 hours", fraction_percent_text(metrics.funding_8h, 4)],
    ]


def _regime_cell(metric: NormalizedMetric, window: str) -> List[str]:
    """[percentile, z-score, observations] of one window; "not computed" when the history is too short."""
    if window == "30d":
        percentile, z_score, count, sufficient = metric.percentile_30d, metric.z_30d, metric.n_30d, metric.sufficient
    else:
        sufficient = metric.n_90d >= HistoricalNormalizer.MIN_OBS and HistoricalNormalizer.has_sufficient_span(
            metric.span_days_90d, HistoricalNormalizer.WINDOW_DAYS_90D)
        percentile, z_score, count = metric.percentile_90d, metric.z_90d, metric.n_90d
    if not sufficient or percentile is None:
        return [NOT_COMPUTED, NOT_COMPUTED, f"{count} obs"]
    return [f"p{percentile:.0f}", number_text(z_score, 2, signed=True), f"{count} obs"]


def normalized_metric_rows(result: OnChainAnalysisResult) -> List[List[str]]:
    """Rows of the historical context table, in the fixed metric order of the text report."""
    ordered = [name for name in METRIC_ORDER if name in result.normalized_metrics]
    ordered += [name for name in result.normalized_metrics if name not in ordered]
    rows = []
    for name in ordered:
        metric = result.normalized_metrics[name]
        rows.append([metric_label(name), format_metric_value(metric.value, metric.unit)]
                    + _regime_cell(metric, "30d") + [text_or_missing(metric.regime_30d) if metric.sufficient else NOT_COMPUTED]
                    + _regime_cell(metric, "90d"))
    return rows


def has_open_interest(ratio) -> bool:
    """False for an expiry nobody has opened a position in (new listing): its put/call ratio is then undefined, not 0."""
    return (ratio.total_call_oi + ratio.total_put_oi) > 0


def expiry_comparison_rows(result: OnChainAnalysisResult) -> List[List[str]]:
    """One row per expiry: the headline numbers side by side."""
    rows = []
    for bundle in ordered_bundles(result):
        analysis, gex, surface = bundle.analysis, bundle.gex_dex, bundle.vol_surface
        levels = gex.key_levels if gex is not None else None
        rows.append([
            bundle.expiration, strike_text(analysis.max_pain.max_pain_strike),
            strike_text(levels.call_resistance.strike) if levels is not None and levels.call_resistance else NOT_COMPUTED,
            strike_text(levels.put_support.strike) if levels is not None and levels.put_support else NOT_COMPUTED,
            text_or_missing(levels.gamma_regime) if levels is not None else NOT_COMPUTED,
            ratio_text(analysis.put_call_ratio.ratio) if has_open_interest(analysis.put_call_ratio) else NOT_COMPUTED,
            count_text(analysis.put_call_ratio.total_call_oi + analysis.put_call_ratio.total_put_oi),
            percent_text(surface.atm_iv) if surface is not None else NOT_COMPUTED,
        ])
    return rows


def front_month_bundle(result: OnChainAnalysisResult) -> Optional[ExpirationBundle]:
    """The expiry the historical context describes, or the first expiry by date when that is not known."""
    if result.normalized_metrics_front_month is not None:
        bundle = result.bundle(result.normalized_metrics_front_month)
        if bundle is not None:
            return bundle
    bundles = ordered_bundles(result)
    return bundles[0] if bundles else None


def generated_text(result: OnChainAnalysisResult) -> str:
    """When the analysis was made; a naive timestamp (not UTC-aware) is reported as not known instead of guessed."""
    return f"Analysis made {utc_text(result.generated_at)}"


def build_overview_section(result: OnChainAnalysisResult) -> ReportSection:
    """The "Overview" tab."""
    section = ReportSection(key="overview", title="Overview",
                            description="Market metrics, the expiries side by side, front-month key levels and historical context.",
                            as_of=generated_text(result))
    if result.normalized_metrics_stale_since is not None:
        section.warnings.append(f"historical context is stale: its newest stored data is from {utc_text(result.normalized_metrics_stale_since)}")
    if result.delta_flow_stale_since is not None:
        section.warnings.append(f"delta flow is stale: the newest stored hour is {utc_text(result.delta_flow_stale_since)}")
    body = "<h3>Market metrics</h3>" + table_fragment(["Metric", "Value"], market_metric_rows(result))

    comparison = expiry_comparison_rows(result)
    body += "<h3>Expiries side by side</h3>"
    body += (table_fragment(["Expiry", "Max pain", "Call resistance", "Put support", "Gamma regime", "Put/call (OI)", "Open interest (coins)", "ATM IV"],
                            comparison, numeric_columns=(1, 2, 3, 5, 6, 7))
             if comparison else paragraph_fragment("No expiry was analyzed: not computed.", "note"))

    front = front_month_bundle(result)
    if front is not None:
        body += f"<h3>Key levels, {html.escape(front.expiration)}</h3>" + table_fragment(["Level", "Strike", "Detail"], key_level_rows(front, result.underlying_price))

    label = f" (front month {result.normalized_metrics_front_month})" if result.normalized_metrics_front_month else ""
    body += f"<h3>Historical context{html.escape(label)}</h3>"
    if result.normalized_metrics:
        body += table_fragment(["Metric", "Value", "30d percentile", "30d z-score", "30d obs", "30d regime", "90d percentile",
                                "90d z-score", "90d obs"], normalized_metric_rows(result), numeric_columns=(1, 2, 3, 4, 6, 7, 8))
    else:
        body += paragraph_fragment("Historical context not computed: no stored history for the front-month metrics.", "note")
    section.body_html = body
    return section
