"""
Unit tests for the on-chain analysis tab (refactor_design_spec.md section T9).

Proof required by spec section 4/T9:
  - test_worker_calls_single_service_method
  - test_log_handler_removed_on_close

Both require a real QApplication (PySide6 widgets cannot be constructed
without one) -- a lightweight, module-scoped instance is created if none
exists yet (a running app under pytest normally has none).
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from coding.gui.tabs import on_chain_analysis_tab as tab_module
from coding.gui.tabs.on_chain_analysis_tab import OnChainAnalysisTab, OnChainAnalysisWorker


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


class TestWorkerSingleServiceCall:
    def test_run_calls_workflow_service_exactly_once(self, qapp):
        """
        H3 (refactor_design_spec.md section T9): the worker's run() makes
        exactly one call into OnChainWorkflowService and does not itself
        construct DatabaseRepository/DeribitApiService/OnChainAnalysisService/
        MorningNoteService (those are gone from the worker entirely -- see
        the module-level import list assertion below).
        """
        fake_output = MagicMock()
        fake_output.report_text = "REPORT"

        with patch.object(tab_module, "OnChainWorkflowService") as mock_workflow_cls:
            mock_workflow_cls.return_value.run.return_value = fake_output

            worker = OnChainAnalysisWorker(currency="BTC")
            finished_messages = []
            worker.finished.connect(finished_messages.append)
            worker.run()

        mock_workflow_cls.assert_called_once_with("BTC")
        mock_workflow_cls.return_value.run.assert_called_once()
        assert finished_messages == ["REPORT"]

    def test_run_emits_error_signal_on_exception(self, qapp):
        with patch.object(tab_module, "OnChainWorkflowService") as mock_workflow_cls:
            mock_workflow_cls.return_value.run.side_effect = RuntimeError("boom")

            worker = OnChainAnalysisWorker(currency="BTC")
            errors = []
            worker.error.connect(errors.append)
            worker.run()

        assert errors == ["boom"]

    def test_module_does_not_import_database_or_api_service_directly(self):
        """
        The worker's own module previously imported DatabaseRepository,
        DeribitApiService, OnChainAnalysisService, and MorningNoteService to
        hand-orchestrate the 3-step workflow. After T9 the worker only needs
        OnChainWorkflowService; DatabaseRepository/OnChainAnalysisService are
        still imported for _open_flow_charts's service construction (see
        refactor_design_spec.md compat map row 5), but DeribitApiService and
        MorningNoteService must not be imported at all anymore.
        """
        assert not hasattr(tab_module, "DeribitApiService")
        assert not hasattr(tab_module, "MorningNoteService")


class TestLogHandlerLifecycle:
    def test_setup_logging_attaches_to_subsystem_logger_not_root(self, qapp):
        root_logger = logging.getLogger()
        handlers_before = list(root_logger.handlers)

        tab = OnChainAnalysisTab()

        assert list(root_logger.handlers) == handlers_before, (
            "M9: the GUI handler must not be attached to the root logger"
        )
        subsystem_logger = logging.getLogger(tab_module._GUI_LOG_LOGGER_NAME)
        assert tab.gui_handler in subsystem_logger.handlers

        tab.close()

    def test_handler_removed_on_close(self, qapp):
        tab = OnChainAnalysisTab()
        subsystem_logger = logging.getLogger(tab_module._GUI_LOG_LOGGER_NAME)
        assert tab.gui_handler in subsystem_logger.handlers

        tab.close()

        assert tab.gui_handler not in subsystem_logger.handlers

    def test_two_tab_instances_do_not_double_up_handlers(self, qapp):
        """M9: reproduces the leak scenario -- creating multiple tab
        instances must not accumulate handlers beyond one per live tab."""
        subsystem_logger = logging.getLogger(tab_module._GUI_LOG_LOGGER_NAME)
        before = len(subsystem_logger.handlers)

        tab1 = OnChainAnalysisTab()
        tab2 = OnChainAnalysisTab()
        assert len(subsystem_logger.handlers) == before + 2

        tab1.close()
        tab2.close()
        assert len(subsystem_logger.handlers) == before
