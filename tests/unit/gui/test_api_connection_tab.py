"""
Unit tests for the API Connection tab's workers (Task Wave-J-F Fix 1).

Mirrors tests/unit/gui/test_database_tab.py's pattern (mock the service
class inside the tab module, assert the worker calls it exactly once and
translates the result into Qt signals -- no business logic, no direct API
client construction, in the GUI file itself).

Review finding this fix addresses: api_connection_tab.py's ApiWorker,
InstrumentLoaderWorker, and ExternalApiWorker used to own an endpoint-name
-> service-method dispatch table, per-endpoint parameter coercion, and
construct DeribitApiService/ExternalMetricsFetcher directly inside QThread
workers. That logic moved to
coding.service.api_testing.api_test_service.ApiTestService; the workers now
only call ApiTestService().invoke(...)/.load_instruments(...) and emit
Qt signals.
"""
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from coding.gui.tabs import api_connection_tab as tab_module
from coding.gui.tabs.api_connection_tab import ApiWorker, InstrumentLoaderWorker


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class TestApiWorkerCallsServiceOnce:
    def test_run_calls_api_test_service_invoke_exactly_once_and_emits_result(self, qapp):
        with patch.object(tab_module, "ApiTestService") as mock_service_cls:
            mock_service_cls.return_value.invoke.return_value = {"status": "ok"}

            worker = ApiWorker("Test Connection", {})
            results = []
            worker.finished.connect(lambda result: results.append(result))
            worker.run()

        mock_service_cls.assert_called_once_with()
        mock_service_cls.return_value.invoke.assert_called_once_with("Test Connection", {})
        assert results == [{"status": "ok"}]

    def test_run_forwards_endpoint_name_and_parameters_unmodified(self, qapp):
        """
        Regression guard: the GUI must not coerce/mutate parameters itself
        (that logic lives in ApiTestService now) -- whatever the user
        entered is passed straight through.
        """
        params = {"start_timestamp": "123", "end_timestamp": "456", "currency": "BTC"}
        with patch.object(tab_module, "ApiTestService") as mock_service_cls:
            mock_service_cls.return_value.invoke.return_value = []

            worker = ApiWorker("Get Last Trades By Time", params)
            worker.run()

        mock_service_cls.return_value.invoke.assert_called_once_with(
            "Get Last Trades By Time", params
        )

    def test_run_emits_error_signal_when_service_raises(self, qapp):
        with patch.object(tab_module, "ApiTestService") as mock_service_cls:
            mock_service_cls.return_value.invoke.side_effect = RuntimeError("api down")

            worker = ApiWorker("Get Ticker", {})
            errors = []
            worker.error.connect(lambda msg: errors.append(msg))
            worker.run()

        assert errors == ["api down"]


class TestInstrumentLoaderWorkerCallsServiceOnce:
    def test_run_calls_load_instruments_and_emits_result(self, qapp):
        instruments = [{"instrument_name": "BTC-1JAN27-100000-C"}]
        with patch.object(tab_module, "ApiTestService") as mock_service_cls:
            mock_service_cls.return_value.load_instruments.return_value = instruments

            worker = InstrumentLoaderWorker("BTC", "option")
            results = []
            worker.finished.connect(lambda result: results.append(result))
            worker.run()

        mock_service_cls.assert_called_once_with()
        mock_service_cls.return_value.load_instruments.assert_called_once_with("BTC", "option")
        assert results == [instruments]

    def test_run_emits_error_signal_when_service_raises(self, qapp):
        with patch.object(tab_module, "ApiTestService") as mock_service_cls:
            mock_service_cls.return_value.load_instruments.side_effect = RuntimeError("timeout")

            worker = InstrumentLoaderWorker("BTC", "option")
            errors = []
            worker.error.connect(lambda msg: errors.append(msg))
            worker.run()

        assert errors == ["timeout"]


class TestModuleDoesNotConstructApiClientsDirectly:
    def test_module_does_not_import_deribit_api_service_or_external_fetcher(self):
        """
        Review fix: api_connection_tab.py previously imported
        DeribitApiService and ExternalMetricsFetcher and constructed them
        directly inside its workers. After this fix, only ApiTestService
        (the service-layer facade) is referenced in the GUI module.
        """
        assert not hasattr(tab_module, "DeribitApiService")
        assert not hasattr(tab_module, "ExternalMetricsFetcher")
        assert hasattr(tab_module, "ApiTestService")
