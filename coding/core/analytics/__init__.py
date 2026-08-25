"""
Analytics module for on-chain data analysis.

Provides classes for calculating options market analytics including
max pain, put/call ratios, support/resistance levels, and GEX/DEX exposure.
"""

# CHANGELOG / removal candidate (carried finding #5, A6 review; re-verified
# at task A7): OnChainAnalyzer is the pre-T10 name, kept as a back-compat
# alias for OnChainMetricsCalculator (see on_chain_analyzer.py's own
# changelog comment on the alias assignment for the full rationale and the
# two in-repo call sites that still use this name). Not removed here --
# removing a public alias is a breaking-change decision beyond T12's
# janitorial scope.
from coding.core.analytics.on_chain_analyzer import OnChainAnalyzer, OnChainMetricsCalculator
from coding.core.analytics.gex_dex_calculator import GexDexCalculator
from coding.core.analytics import chart_generator

__all__ = ["OnChainAnalyzer", "OnChainMetricsCalculator", "GexDexCalculator", "chart_generator"]
