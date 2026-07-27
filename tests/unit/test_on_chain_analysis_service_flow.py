"""
Unit tests for OnChainAnalysisService.get_filtered_aggregate_flow.

Tests the filtered aggregation path that re-runs BuySellFlowAnalyzer per expiration
instead of using the pre-aggregated table (which lacks raw amount/index_price columns).

T5 (refactor_design_spec.md, compatibility-map consumer row #16): this service
method now owns the DB fetch (repository.get_trades_for_flow_analysis) and
injects trades + an explicit window into BuySellFlowAnalyzer — the analyzer's
constructor no longer takes a repository or trade_filter.
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
            MockAnalyzer.return_value = _mock_analyzer_instance({})

            result = service.get_filtered_aggregate_flow("BTC", "block")

        mock_repo.get_flow_metrics.assert_called_once_with("BTC", "28MAR26")
        assert result["spot_price"] == 83000.0

    def test_spot_price_defaults_to_zero_when_metrics_missing(self, service, mock_repo):
        mock_repo.get_active_expirations_with_flow.return_value = [_exp_info("28MAR26")]
        mock_repo.get_flow_metrics.return_value = {}

        with patch(
            "coding.service.on_chain.on_chain_analysis_service.BuySellFlowAnalyzer"
        ) as MockAnalyzer:
            MockAnalyzer.return_value = _mock_analyzer_instance({})

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
            # Both expirations return the same flow_data (10 buy, 5 sell)
            MockAnalyzer.return_value = _mock_analyzer_instance(_flow_data())

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
            MockAnalyzer.return_value = _mock_analyzer_instance(_flow_data(buy_vol=10.0, sell_vol=4.0))

            result = service.get_filtered_aggregate_flow("BTC", "block")

        c_data = result["flow_data"][85000]["C"]
        assert c_data["net_flow"] == pytest.approx(6.0)  # 10-4

    def test_buy_sell_ratio_derived_correctly(self, service, mock_repo):
        mock_repo.get_active_expirations_with_flow.return_value = [_exp_info("28MAR26")]
        mock_repo.get_flow_metrics.return_value = {"spot_price": 85000.0}

        with patch(
            "coding.service.on_chain.on_chain_analysis_service.BuySellFlowAnalyzer"
        ) as MockAnalyzer:
            MockAnalyzer.return_value = _mock_analyzer_instance(_flow_data(buy_vol=8.0, sell_vol=4.0))

            result = service.get_filtered_aggregate_flow("BTC", "block")

        c_data = result["flow_data"][85000]["C"]
        assert c_data["buy_sell_ratio"] == pytest.approx(2.0)  # 8/4

    def test_buy_sell_ratio_is_none_when_sell_volume_is_zero(self, service, mock_repo):
        mock_repo.get_active_expirations_with_flow.return_value = [_exp_info("28MAR26")]
        mock_repo.get_flow_metrics.return_value = {"spot_price": 85000.0}

        with patch(
            "coding.service.on_chain.on_chain_analysis_service.BuySellFlowAnalyzer"
        ) as MockAnalyzer:
            MockAnalyzer.return_value = _mock_analyzer_instance(_flow_data(buy_vol=5.0, sell_vol=0.0))

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
            if call_count == 1:
                m = MagicMock()
                m.calculate.side_effect = RuntimeError("DB error")
                return m
            return _mock_analyzer_instance(_flow_data())

        with patch(
            "coding.service.on_chain.on_chain_analysis_service.BuySellFlowAnalyzer",
            side_effect=side_effect,
        ):
            result = service.get_filtered_aggregate_flow("BTC", "block")

        # Second expiration was processed successfully
        assert 85000 in result["flow_data"]
        c_data = result["flow_data"][85000]["C"]
        assert c_data["buy_volume"] == pytest.approx(10.0)

    def test_trade_filter_and_window_passed_to_repository_fetch(self, service, mock_repo):
        """T5: trade_filter and the explicit window go to the repository
        fetch, not to the BuySellFlowAnalyzer constructor (which no longer
        accepts either)."""
        mock_repo.get_active_expirations_with_flow.return_value = [_exp_info("28MAR26")]
        mock_repo.get_flow_metrics.return_value = {"spot_price": 85000.0}
        mock_repo.get_trades_for_flow_analysis.return_value = []

        with patch(
            "coding.service.on_chain.on_chain_analysis_service.BuySellFlowAnalyzer"
        ) as MockAnalyzer:
            MockAnalyzer.return_value = _mock_analyzer_instance({})

            service.get_filtered_aggregate_flow("ETH", "non_block")

        _, fetch_kwargs = mock_repo.get_trades_for_flow_analysis.call_args
        assert fetch_kwargs["trade_filter"] == "non_block"
        assert fetch_kwargs["currency"] == "ETH"
        assert fetch_kwargs["expiration"] == "28MAR26"
        assert isinstance(fetch_kwargs["start_ts"], int)
        assert isinstance(fetch_kwargs["end_ts"], int)
        assert fetch_kwargs["start_ts"] < fetch_kwargs["end_ts"]

        _, analyzer_kwargs = MockAnalyzer.call_args
        assert "trade_filter" not in analyzer_kwargs
        assert "repository" not in analyzer_kwargs
        assert analyzer_kwargs["currency"] == "ETH"
        assert analyzer_kwargs["window_start_ms"] == fetch_kwargs["start_ts"]
        assert analyzer_kwargs["window_end_ms"] == fetch_kwargs["end_ts"]


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
        analyzer.underlying_price = 64_000.0
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
        # longer called at all -- rendering is format_flow_section's job
        # now, operating on the typed result the builder receives. The
        # "one fetch, one calculate" invariant this test exists to pin is
        # still exactly what's asserted here.
        assert instance.calculate.call_count == 1


# ---------------------------------------------------------------------------
# T9 (refactor_design_spec.md): get_flow_metrics / get_aggregated_flow_metrics
# passthroughs -- so GUI callers (FlowChartsWindow) go through the service
# instead of holding a raw DatabaseRepository reference directly.
# ---------------------------------------------------------------------------


def test_get_flow_metrics_delegates_to_repository(service, mock_repo):
    mock_repo.get_flow_metrics.return_value = {"flow_data": {"x": 1}, "spot_price": 90000.0}

    result = service.get_flow_metrics("BTC", "28MAR26")

    mock_repo.get_flow_metrics.assert_called_once_with("BTC", "28MAR26")
    assert result == {"flow_data": {"x": 1}, "spot_price": 90000.0}


def test_get_flow_metrics_without_repository_returns_empty_shape():
    service = OnChainAnalysisService(repository=None)
    assert service.get_flow_metrics("BTC", "28MAR26") == {"flow_data": {}, "spot_price": 0.0}


def test_get_aggregated_flow_metrics_delegates_to_repository(service, mock_repo):
    mock_repo.get_aggregated_flow_metrics.return_value = {"flow_data": {"y": 2}, "spot_price": 91000.0}

    result = service.get_aggregated_flow_metrics("BTC")

    mock_repo.get_aggregated_flow_metrics.assert_called_once_with("BTC")
    assert result == {"flow_data": {"y": 2}, "spot_price": 91000.0}


def test_get_aggregated_flow_metrics_without_repository_returns_empty_shape():
    service = OnChainAnalysisService(repository=None)
    assert service.get_aggregated_flow_metrics("BTC") == {"flow_data": {}, "spot_price": 0.0}


# ---------------------------------------------------------------------------
# Review fix (task A6): OnChainAnalysisService.create_default() -- the
# service-layer factory GUI callers use instead of constructing
# DatabaseRepository themselves (the review bar: "zero business logic,
# zero direct repository/API access" in GUI modules).
# ---------------------------------------------------------------------------


def test_create_default_constructs_its_own_repository():
    with patch(
        "coding.service.on_chain.on_chain_analysis_service.DatabaseRepository"
    ) as mock_repo_cls:
        service = OnChainAnalysisService.create_default()

    mock_repo_cls.assert_called_once_with()
    assert service.repository is mock_repo_cls.return_value
    assert service.api is None
