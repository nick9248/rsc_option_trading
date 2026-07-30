"""
Top-level report composition for the on-chain analysis text report.

``OnChainReportFormatter`` extracted from ``OnChainAnalyzer.generate_report()``
per refactor_design_spec.md section T3. It renders the header, each
expiration's section, and the market-wide section, then joins them exactly
as ``generate_report()`` used to build one flat list of lines.

T3 does not yet have a populated ``OnChainAnalysisResult`` (that aggregate is
assembled by ``OnChainAnalysisBuilder`` starting at T6) or typed GEX/DEX,
flow, vol-surface, or OI-changes results (those calculators keep returning
dicts/pre-rendered text until T4/T5/T8). ``ExpirationRenderInput`` is the
"temporary adapter" the task brief calls for: it carries the one typed model
the analyzer already computes itself (``ExpirationAnalysisResult``) plus the
already-formatted text blocks the other calculators still produce, so this
formatter can render the exact same output while the rest of the pipeline
still speaks dicts. It is not one of the frozen result models defined in
refactor_design_spec.md section 2 and does not need to survive past T8.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional, Tuple

from coding.core.analytics.market_wide_calculator import FUNDING_PERIODS_PER_YEAR
from coding.core.analytics.reporting.dealer_inventory_formatter import (
    format_dealer_inventory_section,
)
from coding.core.analytics.reporting.expiry_formatter import format_expiration_section
from coding.core.analytics.reporting.flow_formatter import format_flow_section
from coding.core.analytics.reporting.gex_dex_formatter import (
    format_aggregate_gex_dex_section,
    format_gex_dex_section,
)
from coding.core.analytics.reporting.historical_context_formatter import (
    format_historical_context_section,
)
from coding.core.analytics.reporting.market_wide_formatter import (
    format_block_trades_section,
    format_cross_asset_correlation_section,
    format_futures_basis_section,
    format_perpetual_funding_section,
    format_realized_volatility_section,
    format_skew_term_structure_section,
    format_term_structure_section,
    format_volatility_cone_section,
    format_vrp_section,
)
from coding.core.analytics.reporting.oi_changes_formatter import (
    format_iv_percentile_section,
    format_oi_changes_section,
)
from coding.core.analytics.reporting.vol_surface_formatter import format_vol_surface_section
from coding.core.analytics.results.analysis_result import (
    MarketMetricsResult,
    OnChainAnalysisResult,
    TrendSnapshot,
)
from coding.core.analytics.results.expiry_results import ExpirationAnalysisResult
from coding.core.analytics.results.flow_results import FlowResult

_SEPARATOR = "=" * 80
_SUB_SEPARATOR = "-" * 80

# Fixed order market-wide sections are rendered in — matches
# OnChainAnalyzer.generate_report()'s legacy section_name loop verbatim.
_MARKET_WIDE_SECTION_ORDER = (
    "aggregate_gex_dex",
    # institutional_metrics_spec.md section 9(b)'s new market-wide order
    # places SKEW TERM STRUCTURE (section 3) between GAMMA ROLL-OFF
    # (section 5, not yet implemented) and IV TERM STRUCTURE -- inserted
    # directly before iv_term_structure here since section 5 doesn't exist
    # yet; the full section 9 reorder (with section 5) is a later task.
    "skew_term_structure",
    "iv_term_structure",
    "futures_basis",
    "realized_volatility",
    "vrp",
    "volatility_cone",
    "perpetual_funding",
    "block_trades",
    "cross_asset_correlation",
)


@dataclass
class ExpirationRenderInput:
    """
    Temporary adapter bundling one expiration's typed analysis result with
    the previous-snapshot trend and any already-formatted extra section text
    (GEX/DEX, buy/sell flow, vol surface, OI changes) the legacy calculators
    still produce. See module docstring.
    """

    expiration: str
    analysis: ExpirationAnalysisResult
    trend: Optional[TrendSnapshot] = None
    extra_sections: Tuple[str, ...] = field(default_factory=tuple)
    # bugfix_spec.md Item 6 / F6.3.4 (carried from A4 review): a one-line
    # evidence caveat ("EVIDENCE: OI/GEX from full book | Flow: ...")
    # printed right under the per-expiration header, so PCR/GEX conclusions
    # printed alongside an empty/insufficient flow section carry an
    # explicit caveat. None when the analyzer has no flow bookkeeping yet.
    evidence_line: Optional[str] = None


class OnChainReportFormatter:
    """Composes the full on-chain analysis text report from its sections."""

    def render_header(
        self,
        currency: str,
        underlying_price: float,
        generated_at: datetime,
        market_metrics: Optional[MarketMetricsResult],
    ) -> str:
        """
        Render the report header and, if present, the MARKET METRICS block.

        Args:
            currency: Currency symbol (BTC, ETH).
            underlying_price: Current underlying spot price.
            generated_at: Report generation timestamp.
            market_metrics: Currency-wide market metrics, or None if
                ``set_market_metrics()`` was never called (matches the
                legacy ``if self.market_metrics:`` truthiness gate on an
                empty dict).

        Returns:
            Formatted multi-line string.
        """
        lines = []
        timestamp = generated_at.strftime("%Y-%m-%d %H:%M:%S")

        lines.append(_SEPARATOR)
        lines.append("ON CHAIN ANALYSIS REPORT")
        lines.append(f"Generated: {timestamp}")
        lines.append(f"Currency: {currency}")
        lines.append(f"Current Underlying Price: ${underlying_price:,.2f}")
        lines.append(_SEPARATOR)
        lines.append("")

        if market_metrics is not None:
            lines.append("MARKET METRICS")
            lines.append(_SUB_SEPARATOR)

            dvol = market_metrics.dvol
            iv_percentile = market_metrics.iv_percentile
            current_funding = market_metrics.current_funding
            funding_8h = market_metrics.funding_8h
            iv_rank = market_metrics.iv_rank

            if dvol is not None:
                lines.append(f"DVOL (Volatility Index): {dvol:.2f}")
            if iv_percentile is not None:
                lines.append(f"IV Percentile (365d): {iv_percentile:.1f}%")
            if iv_rank is not None:
                lines.append(f"IV Rank (365d): {iv_rank:.1f}%")
            if dvol is not None:
                daily_move = dvol / 100 / math.sqrt(365) * underlying_price
                weekly_move = dvol / 100 / math.sqrt(52) * underlying_price
                monthly_move = dvol / 100 / math.sqrt(12) * underlying_price
                daily_pct = dvol / 100 / math.sqrt(365) * 100
                weekly_pct = dvol / 100 / math.sqrt(52) * 100
                monthly_pct = dvol / 100 / math.sqrt(12) * 100
                lines.append(f"Expected Daily Move:    ${daily_move:,.2f}  ({daily_pct:.1f}%)")
                lines.append(f"Expected Weekly Move:   ${weekly_move:,.2f}  ({weekly_pct:.1f}%)")
                lines.append(f"Expected Monthly Move:  ${monthly_move:,.2f}  ({monthly_pct:.1f}%)")
            if current_funding is not None:
                # CARRIED FINDING #2 (A5 review, task A6 brief): this line
                # used to compute funding_annualized = current_funding * 3
                # * 365 * 100 -- current_funding is the instantaneous
                # accruing rate, not the realised 8h rate; bugfix_spec.md
                # Item 4 defect (b) already fixed the calculator's own
                # annualization (market_wide_formatter.format_
                # perpetual_funding_section) to use funding_8h but missed
                # this second site, so the header and the funding section
                # printed two contradictory annualized numbers for the same
                # instant. Same basis as the calculator fix: funding_8h *
                # FUNDING_PERIODS_PER_YEAR. When funding_8h is unavailable
                # there is no correct annualization basis -- show the
                # instantaneous rate alone rather than a fabricated figure.
                funding_pct = current_funding * 100
                if funding_8h is not None:
                    funding_annualized = funding_8h * FUNDING_PERIODS_PER_YEAR * 100
                    lines.append(
                        f"Current Funding Rate: {funding_pct:.4f}% "
                        f"({funding_annualized:.2f}% annualized)"
                    )
                else:
                    lines.append(f"Current Funding Rate: {funding_pct:.4f}%")
            if funding_8h is not None:
                funding_8h_pct = funding_8h * 100
                lines.append(f"8h Funding Rate: {funding_8h_pct:.4f}%")

            lines.append("")
            lines.append(_SEPARATOR)
            lines.append("")

        return "\n".join(lines)

    def render_header_from_result(self, result: OnChainAnalysisResult) -> str:
        """
        Render the report header directly from the typed
        ``OnChainAnalysisResult`` (refactor_design_spec.md section T8).

        Thin wrapper over ``render_header`` — extracts the same four
        arguments from the result aggregate instead of the caller passing
        them separately, so ``_save_reports_per_expiration`` can render
        from the result without string-scanning the full report text.
        """
        return self.render_header(
            currency=result.currency,
            underlying_price=result.underlying_price,
            generated_at=result.generated_at,
            market_metrics=result.market_metrics,
        )

    def render_expiration(self, render_input: ExpirationRenderInput, spot_price: float) -> str:
        """
        Render one expiration's full section: header line, the analysis
        block, and any extra pre-rendered sections (GEX/DEX, flow, vol
        surface, OI changes), followed by the closing separator.

        Args:
            render_input: The expiration's typed analysis + trend + extras.
            spot_price: Underlying price to anchor this section's
                settlement-space distances (max-pain distance, "current
                price" label on support/resistance) against. bugfix_spec.md
                Item 7: this is THIS expiration's own forward price, not a
                single value shared across every expiration.

        Returns:
            Formatted multi-line string.
        """
        lines = [f"EXPIRATION: {render_input.expiration}", _SUB_SEPARATOR]
        if render_input.evidence_line is not None:
            lines.append(render_input.evidence_line)
            lines.append("")
        lines.append(
            format_expiration_section(render_input.analysis, spot_price, render_input.trend)
        )
        for extra in render_input.extra_sections:
            lines.append(extra)
        lines.append(_SEPARATOR)
        lines.append("")
        return "\n".join(lines)

    def render_expiration_from_result(self, result: OnChainAnalysisResult, expiration: str) -> str:
        """
        Render one expiration's full section directly from the typed
        ``OnChainAnalysisResult`` (refactor_design_spec.md section T8 —
        kills the report-text splitter in ``_save_reports_per_expiration``).

        Builds an ``ExpirationRenderInput`` from the expiration's
        ``ExpirationBundle`` — extra sections come from the reporting
        package's own formatters (``format_gex_dex_section``,
        ``format_flow_section``, ``format_vol_surface_section``,
        ``format_oi_changes_section``/``format_iv_percentile_section``)
        operating on the bundle's typed sub-results, in the same fixed
        order the legacy pre-rendered-text adapter used (GEX/DEX, flow,
        vol surface, OI-changes+IV-percentile) — so this renders the exact
        same text ``render_expiration`` would for an
        ``ExpirationRenderInput`` built by ``OnChainAnalyzer.generate_report()``.

        Returns "" if the expiration is not in the result (matches the
        legacy behavior of skipping an expiration with no analysis).
        """
        bundle = result.bundle(expiration)
        if bundle is None:
            return ""

        extra_sections = []
        if bundle.gex_dex is not None:
            extra_sections.append(format_gex_dex_section(bundle.gex_dex, result.currency))
        # institutional_metrics_spec.md section 2 / task C3: additive new
        # section, placed immediately after GEX/DEX so the two "ASSUMED
        # DEALER VIEW" labels (gex_dex_formatter's and this one's) read as
        # the same convention (D7). Never rendered without a dealer_
        # inventory result -- unlike gex_dex, there is no legacy fallback
        # path for this section.
        if bundle.dealer_inventory is not None:
            extra_sections.append(
                format_dealer_inventory_section(bundle.dealer_inventory, bundle.gex_dex, result.currency)
            )
        if bundle.flow is not None:
            extra_sections.append(format_flow_section(bundle.flow, bundle.flow.lookback_hours))
        if bundle.vol_surface is not None:
            extra_sections.append(format_vol_surface_section(bundle.vol_surface, expiration))

        # OI changes + IV percentile concatenate into ONE block with no
        # separator between them (matches the legacy in-service
        # `existing + iv_section` string concatenation exactly — joining
        # them as two separate extra_sections entries would insert an
        # extra blank line neither the legacy path nor format_oi_changes_section
        # /format_iv_percentile_section's own text expects).
        oi_iv_text = ""
        if bundle.oi_changes is not None and bundle.oi_changes.has_previous_snapshot:
            oi_iv_text += format_oi_changes_section(bundle.oi_changes)
        if bundle.iv_percentile is not None:
            oi_iv_text += format_iv_percentile_section(bundle.iv_percentile)
        if oi_iv_text:
            extra_sections.append(oi_iv_text)

        render_input = ExpirationRenderInput(
            expiration=expiration,
            analysis=bundle.analysis,
            trend=bundle.trend,
            extra_sections=tuple(extra_sections),
            evidence_line=self._evidence_line_from_flow(bundle.flow),
        )
        # bugfix_spec.md Item 7 anchor table: format_expiration_section's
        # spot_price feeds max-pain distance and the "current price" label
        # on support/resistance levels -- both settlement-space (strike vs.
        # where THIS expiry's contract settles), so this expiry's own
        # forward (bundle.analysis.underlying_price, already anchored there
        # by analyze_expiration) is correct here, not the aggregate
        # result.underlying_price (the index, same for every expiration).
        return self.render_expiration(render_input, bundle.analysis.underlying_price)

    @staticmethod
    def _evidence_line_from_flow(flow: Optional[FlowResult]) -> Optional[str]:
        """
        bugfix_spec.md Item 6 / F6.3.4 (carried from A4 review): the same
        evidence-caveat text ``OnChainAnalyzer._build_evidence_line``
        builds from the dict bookkeeping, built here directly from the
        typed ``FlowResult`` — the two must stay in lockstep since both
        render the same per-expiration header line.
        """
        if flow is None:
            return "EVIDENCE: OI/GEX from full book | Flow: NOT ANALYZED"
        status = "OK" if flow.sufficient_data else "INSUFFICIENT"
        return (
            f"EVIDENCE: OI/GEX from full book | "
            f"Flow: {status} ({flow.trade_count} trades in {flow.lookback_hours:.0f}h)"
        )

    def render_market_wide(self, sections: Dict[str, str]) -> str:
        """
        Render the MARKET-WIDE METRICS block from already-formatted section
        text, in the fixed legacy order.

        Args:
            sections: Mapping of section name -> pre-rendered text. Missing
                keys are skipped (matches the legacy
                ``if section_name in self.market_wide_sections`` gate).

        Returns:
            Formatted multi-line string, or "" if ``sections`` is empty
            (matches the legacy ``if self.market_wide_sections:`` gate —
            callers should not append an empty result).
        """
        if not sections:
            return ""

        lines = [_SEPARATOR, f"{'MARKET-WIDE METRICS':^80}", _SEPARATOR, ""]
        for section_name in _MARKET_WIDE_SECTION_ORDER:
            if section_name in sections:
                lines.append(sections[section_name])
                lines.append("")
        lines.append(_SEPARATOR)
        return "\n".join(lines)

    def render_market_wide_from_result(self, result: OnChainAnalysisResult) -> str:
        """
        Render the MARKET-WIDE METRICS block directly from the typed
        ``OnChainAnalysisResult.market_wide`` (refactor_design_spec.md
        section T8), in the same fixed order ``render_market_wide`` uses.

        Not called by ``_save_reports_per_expiration`` — the legacy
        text-splitter's naive "EXPIRATION:" scan ran the LAST expiration's
        slice to the end of the full report string, so that one
        expiration's saved file also picked up the trailing MARKET-WIDE
        METRICS block (never the intent per this method's own "each
        expiration folder gets only its section" contract). T8 does not
        reproduce that leak: per-expiration files now contain only that
        expiration's own section, for every expiration including the last.
        This method is live as of T10: it is ``render_full_from_result``'s
        market-wide block, called from ``fetch_and_analyze``'s report path
        (task A6 flipped this render path from dead code to the sole full
        -report renderer — this docstring previously called it "not
        currently called", which stopped being true then; task A7 review
        caught the stale comment).

        A section is included only when its typed sub-result is not None —
        matches the legacy ``if section_name in self.market_wide_sections``
        gate (a phase whose try/except caught an exception, or whose guard
        condition wasn't met, never got a dict entry either).

        Returns "" if every sub-result is None (matches the legacy
        ``if self.market_wide_sections:`` gate on an empty dict).
        """
        mw = result.market_wide
        sections: Dict[str, str] = {}

        if mw.aggregate_gex_dex is not None:
            sections["aggregate_gex_dex"] = format_aggregate_gex_dex_section(
                mw.aggregate_gex_dex, result.underlying_price, result.currency,
            )
        if mw.skew_term_structure is not None:
            sections["skew_term_structure"] = format_skew_term_structure_section(
                mw.skew_term_structure
            )
        if mw.term_structure is not None:
            sections["iv_term_structure"] = format_term_structure_section(mw.term_structure)
        if mw.futures_basis is not None:
            sections["futures_basis"] = format_futures_basis_section(mw.futures_basis)
        if mw.realized_volatility is not None:
            sections["realized_volatility"] = format_realized_volatility_section(mw.realized_volatility)
        if mw.variance_risk_premium is not None:
            sections["vrp"] = format_vrp_section(mw.variance_risk_premium)
        if mw.volatility_cone is not None:
            sections["volatility_cone"] = format_volatility_cone_section(mw.volatility_cone)
        if mw.perpetual_funding is not None:
            sections["perpetual_funding"] = format_perpetual_funding_section(mw.perpetual_funding)
        if mw.block_trades is not None:
            sections["block_trades"] = format_block_trades_section(mw.block_trades)
        if mw.cross_asset_correlation is not None:
            sections["cross_asset_correlation"] = format_cross_asset_correlation_section(
                mw.cross_asset_correlation, result.currency,
            )

        return self.render_market_wide(sections)

    def render_full_from_result(self, result: OnChainAnalysisResult) -> str:
        """
        Render the complete report directly from the typed
        ``OnChainAnalysisResult`` (refactor_design_spec.md section T10).

        The result-based counterpart to ``render_full`` -- used by
        ``OnChainAnalysisService.fetch_and_analyze`` once
        ``OnChainAnalyzer.generate_report()`` (a pure delegator to
        ``render_full`` as of T3) is deleted. Composes
        ``render_header_from_result`` + ``render_expiration_from_result``
        per expiration (both already proven byte-identical to the legacy
        per-argument renderers by T8's per-expiration characterization
        test) + ``render_market_wide_from_result`` (dead code before this
        task -- going live here for the first time), joined exactly as
        ``render_full`` joins its pieces.
        """
        blocks = [self.render_header_from_result(result)]
        for expiration in result.expiration_names():
            blocks.append(self.render_expiration_from_result(result, expiration))

        market_wide_text = self.render_market_wide_from_result(result)
        if market_wide_text:
            blocks.append(market_wide_text)

        # institutional_metrics_spec.md section 1: front-month percentile/
        # z-score context. Appended last, after market-wide -- absent
        # entirely (format_historical_context_section returns "") when
        # result.normalized_metrics is empty (e.g. no repository, or an
        # offline fixture with no recorded trailing history).
        historical_context_text = format_historical_context_section(
            result.normalized_metrics,
            front_month_expiration=result.normalized_metrics_front_month,
            stale_since=result.normalized_metrics_stale_since,
        )
        if historical_context_text:
            blocks.append(historical_context_text)

        return "\n".join(blocks)
