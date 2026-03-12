"""
Unit tests for OnChainAnalysisService.get_filtered_aggregate_flow.

Tests the filtered aggregation path that re-runs BuySellFlowAnalyzer per expiration
instead of using the pre-aggregated table (which lacks raw amount/index_price columns).
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


# ---------------------------------------------------------------------------
# Early-exit paths
# ---------------------------------------------------------------------------

class TestGetFilteredAggregateFlowEarlyExit:

    def test_returns_empty_when_repository_is_none(self):
        svc = OnChainAnalysisService(repository=None)
        result = svc.get_filtered_aggregate_flow("BTC", "block")
        assert result == {"flow_data": {}, "spot_price": 0.0}

    def test_returns_empty_when_no_active_expirations(self, service, mock_repo):
        mock_repo.get_active_expirations_with_flow.return_value = []
        result = service.get_filtered_aggregate_flow("BTC", "block")
        assert result == {"flow_data": {}, "spot_price": 0.0}

    def test_spot_price_comes_from_first_expiration(self, service, mock_repo):
        mock_repo.get_active_expirations_with_flow.return_value = [
            _exp_info("28MAR26"),
            _exp_info("25APR26"),
        ]
        mock_repo.get_flow_metrics.return_value = {"spot_price": 83000.0}

        with patch(
            "coding.service.on_chain.on_chain_analysis_service.BuySellFlowAnalyzer"
        ) as MockAnalyzer:
            mock_instance = MagicMock()
            mock_instance.calculate.return_value = {"flow_data": {}}
            MockAnalyzer.return_value = mock_instance

            result = service.get_filtered_aggregate_flow("BTC", "block")

        mock_repo.get_flow_metrics.assert_called_once_with("BTC", "28MAR26")
        assert result["spot_price"] == 83000.0

    def test_spot_price_defaults_to_zero_when_metrics_missing(self, service, mock_repo):
        mock_repo.get_active_expirations_with_flow.return_value = [_exp_info("28MAR26")]
        mock_repo.get_flow_metrics.return_value = {}

        with patch(
            "coding.service.on_chain.on_chain_analysis_service.BuySellFlowAnalyzer"
        ) as MockAnalyzer:
            mock_instance = MagicMock()
            mock_instance.calculate.return_value = {"flow_data": {}}
            MockAnalyzer.return_value = mock_instance

            result = service.get_filtered_aggregate_flow("BTC", "block")

        assert result["spot_price"] == 0.0


# ---------------------------------------------------------------------------
# Aggregation logic
# ---------------------------------------------------------------------------

class TestGetFilteredAggregateFlowAggregation:

    def test_aggregates_across_expirations(self, service, mock_repo):
        """Volumes from two expirations are summed at the same strike."""
        mock_repo.get_active_expirations_with_flow.return_value = [
            _exp_info("28MAR26"),
            _exp_info("25APR26"),
        ]
        mock_repo.get_flow_metrics.return_value = {"spot_price": 85000.0}

        with patch(
            "coding.service.on_chain.on_chain_analysis_service.BuySellFlowAnalyzer"
        ) as MockAnalyzer:
            mock_instance = MagicMock()
            # Both expirations return the same flow_data (10 buy, 5 sell)
            mock_instance.calculate.return_value = {"flow_data": _flow_data()}
            MockAnalyzer.return_value = mock_instance

            result = service.get_filtered_aggregate_flow("BTC", "non_block")

        strike_data = result["flow_data"][85000]["C"]
        assert strike_data["buy_volume"] == pytest.approx(20.0)   # 10+10
        assert strike_data["sell_volume"] == pytest.approx(10.0)  # 5+5
        assert strike_data["buy_count"] == 6   # 3+3
        assert strike_data["sell_count"] == 4  # 2+2

    def test_net_flow_derived_correctly(self, service, mock_repo):
        mock_repo.get_active_expirations_with_flow.return_value = [_exp_info("28MAR26")]
        mock_repo.get_flow_metrics.return_value = {"spot_price": 85000.0}

        with patch(
            "coding.service.on_chain.on_chain_analysis_service.BuySellFlowAnalyzer"
        ) as MockAnalyzer:
            mock_instance = MagicMock()
            mock_instance.calculate.return_value = {"flow_data": _flow_data(buy_vol=10.0, sell_vol=4.0)}
            MockAnalyzer.return_value = mock_instance

            result = service.get_filtered_aggregate_flow("BTC", "block")

        c_data = result["flow_data"][85000]["C"]
        assert c_data["net_flow"] == pytest.approx(6.0)  # 10-4

    def test_buy_sell_ratio_derived_correctly(self, service, mock_repo):
        mock_repo.get_active_expirations_with_flow.return_value = [_exp_info("28MAR26")]
        mock_repo.get_flow_metrics.return_value = {"spot_price": 85000.0}

        with patch(
            "coding.service.on_chain.on_chain_analysis_service.BuySellFlowAnalyzer"
        ) as MockAnalyzer:
            mock_instance = MagicMock()
            mock_instance.calculate.return_value = {"flow_data": _flow_data(buy_vol=8.0, sell_vol=4.0)}
            MockAnalyzer.return_value = mock_instance

            result = service.get_filtered_aggregate_flow("BTC", "block")

        c_data = result["flow_data"][85000]["C"]
        assert c_data["buy_sell_ratio"] == pytest.approx(2.0)  # 8/4

    def test_buy_sell_ratio_is_none_when_sell_volume_is_zero(self, service, mock_repo):
        mock_repo.get_active_expirations_with_flow.return_value = [_exp_info("28MAR26")]
        mock_repo.get_flow_metrics.return_value = {"spot_price": 85000.0}

        with patch(
            "coding.service.on_chain.on_chain_analysis_service.BuySellFlowAnalyzer"
        ) as MockAnalyzer:
            mock_instance = MagicMock()
            mock_instance.calculate.return_value = {"flow_data": _flow_data(buy_vol=5.0, sell_vol=0.0)}
            MockAnalyzer.return_value = mock_instance

            result = service.get_filtered_aggregate_flow("BTC", "block")

        c_data = result["flow_data"][85000]["C"]
        assert c_data["buy_sell_ratio"] is None

    def test_skips_expiration_on_exception_and_continues(self, service, mock_repo):
        """A failing expiration is skipped; others are still processed."""
        mock_repo.get_active_expirations_with_flow.return_value = [
            _exp_info("28MAR26"),
            _exp_info("25APR26"),
        ]
        mock_repo.get_flow_metrics.return_value = {"spot_price": 85000.0}

        call_count = 0

        def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            m = MagicMock()
            if call_count == 1:
                m.calculate.side_effect = RuntimeError("DB error")
            else:
                m.calculate.return_value = {"flow_data": _flow_data()}
            return m

        with patch(
            "coding.service.on_chain.on_chain_analysis_service.BuySellFlowAnalyzer",
            side_effect=side_effect,
        ):
            result = service.get_filtered_aggregate_flow("BTC", "block")

        # Second expiration was processed successfully
        assert 85000 in result["flow_data"]
        c_data = result["flow_data"][85000]["C"]
        assert c_data["buy_volume"] == pytest.approx(10.0)

    def test_trade_filter_passed_to_analyzer(self, service, mock_repo):
        mock_repo.get_active_expirations_with_flow.return_value = [_exp_info("28MAR26")]
        mock_repo.get_flow_metrics.return_value = {"spot_price": 85000.0}

        with patch(
            "coding.service.on_chain.on_chain_analysis_service.BuySellFlowAnalyzer"
        ) as MockAnalyzer:
            mock_instance = MagicMock()
            mock_instance.calculate.return_value = {"flow_data": {}}
            MockAnalyzer.return_value = mock_instance

            service.get_filtered_aggregate_flow("ETH", "non_block")

        _, kwargs = MockAnalyzer.call_args
        assert kwargs["trade_filter"] == "non_block"
        assert kwargs["currency"] == "ETH"
