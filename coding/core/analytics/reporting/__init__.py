"""
Text-report formatting for on-chain analysis.

Pure functions per domain (Decision D3 in refactor_design_spec.md) plus
``OnChainReportFormatter``, the class that composes them (it needs the whole
aggregate + section ordering, so a class earns its keep there). Extracted
from ``OnChainAnalyzer.generate_report()`` and the various calculators'
``generate_report_section()`` methods.
"""

from coding.core.analytics.reporting.expiry_formatter import (
    format_context_section,
    format_expiration_section,
)
from coding.core.analytics.reporting.flow_formatter import format_flow_section
from coding.core.analytics.reporting.gex_dex_formatter import (
    format_aggregate_gex_dex_section,
    format_gex_dex_section,
)
from coding.core.analytics.reporting.market_wide_formatter import (
    format_block_trades_section,
    format_cross_asset_correlation_line,
    format_expected_move_line,
    format_futures_basis_section,
    format_market_wide_context_section,
    format_perpetual_funding_section,
    format_realized_volatility_section,
    format_term_structure_section,
    format_volatility_cone_section,
    format_vrp_section,
)
from coding.core.analytics.reporting.oi_changes_formatter import (
    format_iv_percentile_section,
    format_oi_changes_section,
)
from coding.core.analytics.reporting.report_formatter import (
    ExpirationRenderInput,
    OnChainReportFormatter,
)
from coding.core.analytics.reporting.vol_surface_formatter import format_vol_surface_section

__all__ = [
    "format_expiration_section",
    "format_context_section",
    "format_gex_dex_section",
    "format_aggregate_gex_dex_section",
    "format_flow_section",
    "format_vol_surface_section",
    "format_term_structure_section",
    "format_futures_basis_section",
    "format_realized_volatility_section",
    "format_vrp_section",
    "format_volatility_cone_section",
    "format_perpetual_funding_section",
    "format_block_trades_section",
    "format_cross_asset_correlation_line",
    "format_expected_move_line",
    "format_market_wide_context_section",
    "format_oi_changes_section",
    "format_iv_percentile_section",
    "ExpirationRenderInput",
    "OnChainReportFormatter",
]
