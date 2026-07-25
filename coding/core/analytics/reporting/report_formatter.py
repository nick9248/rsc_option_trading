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

from coding.core.analytics.reporting.expiry_formatter import format_expiration_section
from coding.core.analytics.results.analysis_result import MarketMetricsResult, TrendSnapshot
from coding.core.analytics.results.expiry_results import ExpirationAnalysisResult

_SEPARATOR = "=" * 80
_SUB_SEPARATOR = "-" * 80

# Fixed order market-wide sections are rendered in — matches
# OnChainAnalyzer.generate_report()'s legacy section_name loop verbatim.
_MARKET_WIDE_SECTION_ORDER = (
    "aggregate_gex_dex",
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
                funding_pct = current_funding * 100
                funding_annualized = current_funding * 3 * 365 * 100  # 3 funding periods per day
                lines.append(
                    f"Current Funding Rate: {funding_pct:.4f}% "
                    f"({funding_annualized:.2f}% annualized)"
                )
            if funding_8h is not None:
                funding_8h_pct = funding_8h * 100
                lines.append(f"8h Funding Rate: {funding_8h_pct:.4f}%")

            lines.append("")
            lines.append(_SEPARATOR)
            lines.append("")

        return "\n".join(lines)

    def render_expiration(self, render_input: ExpirationRenderInput, spot_price: float) -> str:
        """
        Render one expiration's full section: header line, the analysis
        block, and any extra pre-rendered sections (GEX/DEX, flow, vol
        surface, OI changes), followed by the closing separator.

        Args:
            render_input: The expiration's typed analysis + trend + extras.
            spot_price: Current underlying spot price (same value for every
                expiration — matches the legacy single ``self.underlying_price``).

        Returns:
            Formatted multi-line string.
        """
        lines = [f"EXPIRATION: {render_input.expiration}", _SUB_SEPARATOR]
        lines.append(
            format_expiration_section(render_input.analysis, spot_price, render_input.trend)
        )
        for extra in render_input.extra_sections:
            lines.append(extra)
        lines.append(_SEPARATOR)
        lines.append("")
        return "\n".join(lines)

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

    def render_full(
        self,
        currency: str,
        underlying_price: float,
        generated_at: datetime,
        market_metrics: Optional[MarketMetricsResult],
        expirations: Tuple[ExpirationRenderInput, ...],
        market_wide_sections: Dict[str, str],
    ) -> str:
        """
        Render the complete report: header, every expiration in order, then
        the market-wide block.

        Returns:
            The full formatted report text.
        """
        blocks = [self.render_header(currency, underlying_price, generated_at, market_metrics)]
        for render_input in expirations:
            blocks.append(self.render_expiration(render_input, underlying_price))

        market_wide_text = self.render_market_wide(market_wide_sections)
        if market_wide_text:
            blocks.append(market_wide_text)

        return "\n".join(blocks)
