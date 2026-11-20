"""
The "Data quality" tab of the on-chain dashboard: what each number rests on. Per expiry the flow sample, the inferred
dealer view and its coverage, missing Greeks, and which optional results exist; then the market-wide parts that were not
computed and how fresh the stored history is.
"""

from typing import List, Tuple

from coding.core.analytics.onchain_dashboard.expiry_section import ordered_bundles
from coding.core.analytics.onchain_dashboard.formatting import (
    count_text, fraction_percent_text, number_text, text_or_missing, utc_text, yes_no)
from coding.core.analytics.report.report_page import ReportSection, paragraph_fragment, table_fragment
from coding.core.analytics.report.section_fragments import NOT_COMPUTED
from coding.core.analytics.results.analysis_result import ExpirationBundle, OnChainAnalysisResult

MARKET_PARTS: List[Tuple[str, str]] = [
    ("aggregate_gex_dex", "Aggregate GEX / DEX"), ("term_structure", "IV term structure"),
    ("skew_term_structure", "Skew term structure"), ("gamma_rolloff", "Gamma roll-off"), ("forward_vol", "Forward vol"),
    ("variance_risk_premium", "Variance risk premium"), ("volatility_cone", "Volatility cone"),
    ("realized_volatility", "Realized volatility"), ("perpetual_funding", "Perpetual funding"),
    ("futures_basis", "Futures basis"), ("block_trades", "Block trades"), ("cross_asset_correlation", "Cross-asset correlation")]


def _flow_text(bundle: ExpirationBundle) -> str:
    flow = bundle.flow
    if flow is None:
        return NOT_COMPUTED
    window = f"{flow.lookback_hours:g} hours"
    if not flow.sufficient_data:
        return f"insufficient: {flow.trade_count} trades in {window}"
    return f"sufficient: {flow.trade_count} trades in {window}" + (", low confidence" if flow.low_confidence else "")


def _inferred_text(bundle: ExpirationBundle) -> str:
    inferred = bundle.dealer_inventory
    if inferred is None:
        return NOT_COMPUTED
    if inferred.render_inferred:
        return (f"shown (coverage {fraction_percent_text(inferred.coverage, 1)}, violations "
                f"{fraction_percent_text(inferred.violation_rate, 1)}, {inferred.n_signed_trades} signed trades)")
    return f"unavailable: {text_or_missing(inferred.unavailable_reason)}"


def _missing_gamma_text(bundle: ExpirationBundle) -> str:
    gex = bundle.gex_dex
    if gex is None:
        return NOT_COMPUTED
    text = f"{gex.instruments_missing_gamma} instruments, {number_text(gex.oi_missing_gamma, 0)} coins of open interest"
    inferred = bundle.dealer_inventory
    if inferred is not None and inferred.instruments_missing_gamma:
        text += f"; inferred view: {inferred.instruments_missing_gamma} instruments"
    return text


def _iv_change_text(bundle: ExpirationBundle) -> str:
    comparison = bundle.fixed_strike_vol
    if comparison is None:
        return NOT_COMPUTED
    if comparison.stale_prior:
        return f"prior snapshot stale (expected {comparison.expected_prior_date.isoformat()})"
    return f"{comparison.n_strikes_matched} strikes matched, {comparison.n_strikes_unmatched} not; regime {comparison.regime}"


def expiry_quality_rows(result: OnChainAnalysisResult) -> List[List[str]]:
    """One row per expiry: the evidence behind it."""
    rows = []
    for bundle in ordered_bundles(result):
        history = bundle.oi_changes
        rows.append([
            bundle.expiration, _flow_text(bundle), _inferred_text(bundle), _missing_gamma_text(bundle),
            NOT_COMPUTED if bundle.exposure_profile is None else f"computed ({bundle.exposure_profile.skipped_instruments} skipped)",
            _iv_change_text(bundle), NOT_COMPUTED if bundle.vol_surface is None else "computed",
            NOT_COMPUTED if history is None else yes_no(history.has_previous_snapshot)])
    return rows


def build_quality_section(result: OnChainAnalysisResult) -> ReportSection:
    """The "Data quality" tab."""
    section = ReportSection(key="data-quality", title="Data quality",
                            description="What each number rests on, and what could not be computed. Missing is never drawn as zero.")
    market = result.market_wide
    if market.failed_sections:
        section.warnings.append("these market-wide sections failed: " + ", ".join(market.failed_sections))
    body = "<h3>Per expiry</h3>"
    rows = expiry_quality_rows(result)
    body += (table_fragment(["Expiry", "Taker flow", "Inferred dealer view", "Missing Greeks", "Vanna / charm", "IV change, 1 day",
                             "Vol surface", "Previous OI snapshot"], rows)
             if rows else paragraph_fragment("No expiry was analyzed: not computed.", "note"))
    body += "<h3>Market-wide parts</h3>" + table_fragment(
        ["Part", "Status"], [[title, NOT_COMPUTED if getattr(market, attribute) is None else "computed"]
                             for attribute, title in MARKET_PARTS])
    body += "<h3>Freshness</h3>" + table_fragment(["Item", "Status"], [
        ["Historical context (percentiles)", "fresh or age not known" if result.normalized_metrics_stale_since is None
         else f"stale: newest stored data {utc_text(result.normalized_metrics_stale_since)}"],
        ["Delta flow", ("fresh or age not known" if result.delta_flow_stale_since is None
                        else f"stale: newest stored hour {utc_text(result.delta_flow_stale_since)}")
         + f"; {result.delta_flow_hours_present} hourly rows in the {result.delta_flow_lookback_hours:g}-hour window"],
        ["Recent trades fetched", count_text(len(result.recent_trades))],
        ["Expiries with an ATM IV", f"{len(result.atm_iv_by_expiration)} of {len(result.expirations)}"]])
    section.body_html = body
    return section
