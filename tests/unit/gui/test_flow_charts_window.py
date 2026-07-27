"""
Unit tests for FlowChartsWindow (refactor_design_spec.md section T9,
review fix Important #2 on task A6).

Requires a real QApplication (PySide6 widgets, including QWebEngineView,
cannot be constructed without one) -- a lightweight, module-scoped
instance is created if none exists yet.
"""
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from coding.gui.dialogs import flow_charts_window as dialog_module
from coding.gui.dialogs.flow_charts_window import FlowChartsWindow


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _make_service():
    service = MagicMock()
    service.get_active_expirations_with_flow.return_value = []
    return service


class TestNoDirectRepositoryAccess:
    def test_constructor_does_not_store_a_repository_attribute(self, qapp):
        """
        Review fix (task A6 Important #2): the dialog must not keep a
        `.repository` shortcut to `service.repository` -- every read goes
        through the injected service now.
        """
        service = _make_service()
        dialog = FlowChartsWindow("BTC", service)

        assert not hasattr(dialog, "repository")

        dialog.close()

    def test_module_does_not_import_generate_flow_trend_chart_directly(self):
        """
        generate_flow_trend_chart (the raw chart_generator function that
        takes a repository) must not be imported into this module anymore
        -- chart generation goes through
        service.generate_flow_trend_chart_figure() instead.
        """
        assert not hasattr(dialog_module, "generate_flow_trend_chart")


class TestServicePassthroughsUsed:
    def test_load_expirations_calls_service_not_repository(self, qapp):
        service = _make_service()
        dialog = FlowChartsWindow("BTC", service)

        service.get_active_expirations_with_flow.assert_called_with("BTC")

        dialog.close()

    def test_generate_charts_from_db_uses_service_trend_chart_figure(self, qapp):
        service = _make_service()
        service.get_flow_metrics.return_value = {
            "flow_data": {70000.0: {"C": {"buy_count": 1, "sell_count": 0, "buy_volume": 1.0,
                                            "sell_volume": 0.0, "buy_notional": 1.0, "sell_notional": 0.0}}},
            "spot_price": 70000.0,
        }
        service.generate_flow_trend_chart_figure.return_value = MagicMock()

        dialog = FlowChartsWindow("BTC", service)
        dialog._generate_charts_from_db("28MAR26")

        service.get_flow_metrics.assert_called_with("BTC", "28MAR26")
        service.generate_flow_trend_chart_figure.assert_called_once()
        _, kwargs = service.generate_flow_trend_chart_figure.call_args
        assert kwargs["currency"] == "BTC"
        assert kwargs["expiration"] == "28MAR26"

        dialog.close()

    def test_generate_aggregate_charts_all_filter_uses_aggregated_metrics(self, qapp):
        service = _make_service()
        service.get_aggregated_flow_metrics.return_value = {
            "flow_data": {70000.0: {"C": {"buy_count": 1, "sell_count": 0, "buy_volume": 1.0,
                                            "sell_volume": 0.0, "buy_notional": 1.0, "sell_notional": 0.0}}},
            "spot_price": 70000.0,
        }
        service.generate_flow_trend_chart_figure.return_value = MagicMock()

        dialog = FlowChartsWindow("BTC", service)
        # _load_expirations() already triggered one aggregate-chart pass at
        # construction time ("All Expirations" is combo index 0) -- reset
        # before the explicit call this test actually exercises.
        service.reset_mock()
        dialog.current_filter = "all"
        dialog._generate_aggregate_charts()

        service.get_aggregated_flow_metrics.assert_called_once_with("BTC")
        service.generate_flow_trend_chart_figure.assert_called_once()

        dialog.close()

    def test_generate_aggregate_charts_filtered_uses_filtered_aggregate_flow(self, qapp):
        service = _make_service()
        service.get_filtered_aggregate_flow.return_value = {
            "flow_data": {70000.0: {"C": {"buy_count": 1, "sell_count": 0, "buy_volume": 1.0,
                                            "sell_volume": 0.0, "buy_notional": 1.0, "sell_notional": 0.0}}},
            "spot_price": 70000.0,
        }
        service.generate_flow_trend_chart_figure.return_value = MagicMock()

        dialog = FlowChartsWindow("BTC", service)
        service.reset_mock()
        dialog.current_filter = "block"
        dialog._generate_aggregate_charts()

        service.get_filtered_aggregate_flow.assert_called_once_with("BTC", "block")
        service.get_aggregated_flow_metrics.assert_not_called()

        dialog.close()
