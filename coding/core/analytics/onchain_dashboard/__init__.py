"""The on-chain dashboard: one self-contained HTML page built from the typed ``OnChainAnalysisResult`` (pure, no database or network)."""

from coding.core.analytics.onchain_dashboard.page import build_onchain_dashboard_page

__all__ = ["build_onchain_dashboard_page"]
