from datetime import datetime, timezone
"""
Unit tests for the per-expiration flow phase of OnChainAnalysisService (_calculate_buy_sell_flow): one
fetch and one calculate() call per expiration.
"""

import pytest
from unittest.mock import MagicMock, patch
from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService


@pytest.fixture
def mock_repo():
    return MagicMock()


@pytest.fixture
def service(mock_repo):
    return OnChainAnalysisService(repository=mock_repo)


def _exp_info(expiration: str):
    return {"expiration": expiration}


def _flow_data(buy_vol=10.0, sell_vol=5.0):
    """Return a minimal flow_data dict at one strike."""
    return {
        85000: {
            "C": {
                "buy_count": 3,
                "sell_count": 2,
                "buy_volume": buy_vol,
                "sell_volume": sell_vol,
                "buy_notional": buy_vol * 85000,
                "sell_notional": sell_vol * 85000,
            },
            "P": {
                "buy_count": 1,
                "sell_count": 1,
                "buy_volume": 2.0,
                "sell_volume": 2.0,
                "buy_notional": 2.0 * 85000,
                "sell_notional": 2.0 * 85000,
            },
        }
    }


def _mock_analyzer_instance(flow_data_dict):
    """A MagicMock standing in for a BuySellFlowAnalyzer instance whose
    .calculate() returns a typed-result stand-in — .to_dict() is what the
    service actually calls."""
    instance = MagicMock()
    instance.calculate.return_value.to_dict.return_value = {"flow_data": flow_data_dict}
    return instance


# ---------------------------------------------------------------------------
# Single-fetch guarantee for the main per-expiration flow phase
# ---------------------------------------------------------------------------

class TestCalculateBuySellFlowSingleFetch:
    """bugfix_spec.md Item 6a: exactly one DB fetch, one calculate() call,
    per expiration in the main analysis pipeline."""

    def _make_analyzer(self, currency="BTC", expirations=("27MAR26",)):
        from coding.core.analytics.on_chain_analyzer import OnChainAnalyzer

        analyzer = OnChainAnalyzer([], currency)
        analyzer.parsed_data = {exp: [] for exp in expirations}
        # bugfix_spec.md Item 7: underlying_price is now a read-only
        # deprecated alias for index_price -- set it via set_index_price().
        analyzer.set_index_price(64_000.0)
        return analyzer

    def test_one_repository_fetch_and_one_calculate_call_per_expiration(self, service, mock_repo):
        mock_repo.get_trades_for_flow_analysis.return_value = []
        analyzer = self._make_analyzer()

        with patch(
            "coding.service.on_chain.on_chain_analysis_service.BuySellFlowAnalyzer"
        ) as MockAnalyzer, patch(
            "coding.service.on_chain.on_chain_analysis_service.generate_flow_distribution_chart"
        ), patch(
            "coding.service.on_chain.on_chain_analysis_service.generate_net_flow_chart"
        ), patch(
            "coding.service.on_chain.on_chain_analysis_service.generate_flow_trend_chart"
        ), patch(
            "coding.service.on_chain.on_chain_analysis_service.save_chart", return_value=""
        ), patch(
            "coding.service.on_chain.on_chain_analysis_service.inject_hover_js"
        ):
            instance = MagicMock()
            instance.calculate.return_value.to_dict.return_value = {"flow_data": {}}
            MockAnalyzer.return_value = instance

            service._calculate_buy_sell_flow(analyzer, progress_callback=lambda msg: None)

        assert mock_repo.get_trades_for_flow_analysis.call_count == 1
        # T10 (refactor_design_spec.md): generate_report_section() is no
        # longer called at all -- rendering operates on the typed result
        # the builder receives (as of institutional_metrics_spec.md
        # section 9(b) Task D2 review, via delta_flow_formatter.
        # format_delta_adjusted_flow_section, not format_flow_section --
        # see that module's docstring). The "one fetch, one calculate"
        # invariant this test exists to pin is still exactly what's
        # asserted here.
        assert instance.calculate.call_count == 1
