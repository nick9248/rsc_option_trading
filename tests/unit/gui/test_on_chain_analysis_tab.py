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

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from coding.gui.tabs import on_chain_analysis_tab as tab_module
from coding.gui.tabs.on_chain_analysis_tab import OnChainAnalysisTab, OnChainAnalysisWorker
from coding.gui.theme.colors import Colors


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

        Task D3: ``finished`` now carries the whole ``OnChainWorkflowOutput``
        object (not just ``report_text``) so the tab can read
        ``output.result`` for the Levels table / normalized-metric strip.
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
        assert finished_messages == [fake_output]
        assert finished_messages[0].report_text == "REPORT"

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
        Review fix: the module previously imported DatabaseRepository,
        DeribitApiService, OnChainAnalysisService, and MorningNoteService to
        hand-orchestrate the 3-step workflow. After T9, neither
        DatabaseRepository nor DeribitApiService may be imported anywhere in
        this module -- the brief's review bar is "zero business logic, zero
        direct repository/API access". _open_flow_charts gets its service
        via OnChainAnalysisService.create_default() (a service-layer
        factory, see on_chain_analysis_service.py), not by constructing
        DatabaseRepository itself. OnChainAnalysisService itself is still
        imported (for that one call plus its class reference), and
        MorningNoteService must not be imported at all anymore.
        """
        assert not hasattr(tab_module, "DatabaseRepository")
        assert not hasattr(tab_module, "DeribitApiService")
        assert not hasattr(tab_module, "MorningNoteService")

    def test_open_flow_charts_uses_service_factory_not_database_repository(self, qapp):
        """
        Review fix: pin the actual call path, not just the absent import --
        _open_flow_charts must go through OnChainAnalysisService.
        create_default(), never construct DatabaseRepository directly.
        """
        with patch.object(tab_module.OnChainAnalysisService, "create_default") as mock_factory, \
             patch.object(tab_module, "FlowChartsWindow") as mock_dialog_cls:
            mock_dialog = mock_dialog_cls.return_value
            tab = OnChainAnalysisTab()
            tab._last_analyzed_currency = "BTC"

            tab._open_flow_charts()

            mock_factory.assert_called_once_with()
            mock_dialog_cls.assert_called_once_with("BTC", mock_factory.return_value, parent=tab)
            mock_dialog.exec.assert_called_once()

            tab.close()


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


# ---------------------------------------------------------------------------
# Task D3 (institutional_metrics_spec.md section 9(c)): Levels tab / metric
# strip population. Pure data-transformation logic (LevelsTable ->
# QTableWidgetItem values/colors) is unit-tested here directly against
# `_current_levels_table` -- the join itself (ExpirationBundle ->
# LevelsTable) is proven separately in
# tests/unit/analytics/test_levels_table_builder.py.
# ---------------------------------------------------------------------------

from coding.core.analytics.historical_normalizer import NormalizedMetric
from coding.core.analytics.results.levels_table_results import LevelsTable, LevelsTableRow


def _row(
    strike,
    net_gex_holder=1.0,
    net_gex_assumed=0.0,
    net_gex_inferred=None,
    call_oi=10.0,
    put_oi=5.0,
    net_dex=0.0,
    vex=0.0,
    cex=0.0,
    net_taker_flow=None,
    delta_1d_iv=None,
    is_call_wall_assumed=False,
    is_put_support_assumed=False,
    is_hvl_assumed=False,
    is_call_wall_inferred=False,
    is_put_support_inferred=False,
    is_hvl_inferred=False,
    is_max_pain=False,
):
    return LevelsTableRow(
        strike=strike, call_oi=call_oi, put_oi=put_oi, net_gex_holder=net_gex_holder,
        net_gex_assumed=net_gex_assumed, net_gex_inferred=net_gex_inferred, net_dex=net_dex,
        vex=vex, cex=cex, net_taker_flow=net_taker_flow, delta_1d_iv=delta_1d_iv,
        is_call_wall_assumed=is_call_wall_assumed, is_put_support_assumed=is_put_support_assumed,
        is_hvl_assumed=is_hvl_assumed, is_call_wall_inferred=is_call_wall_inferred,
        is_put_support_inferred=is_put_support_inferred, is_hvl_inferred=is_hvl_inferred,
        is_max_pain=is_max_pain,
    )


class TestLevelsTablePopulation:
    def test_populate_sets_row_count_and_formats_cell_text(self, qapp):
        tab = OnChainAnalysisTab()
        tab._current_levels_table = LevelsTable(
            expiration="27MAR26",
            rows=(_row(95000.0, call_oi=20.0, put_oi=8.0, net_gex_assumed=-1234.5),),
            gex_dex_available=True, exposure_available=True,
            inferred_available=False, net_taker_flow_available=False, delta_1d_iv_available=False,
        )

        tab._populate_levels_table()

        assert tab.levels_table.rowCount() == 1
        assert tab.levels_table.item(0, tab_module._COL_STRIKE).text() == "95,000"
        assert tab.levels_table.item(0, tab_module._COL_CALL_OI).text() == "20"
        assert tab.levels_table.item(0, tab_module._COL_PUT_OI).text() == "8"
        assert tab.levels_table.item(0, tab_module._COL_GEX_ASSUMED).text() == "-1,234.50"

        tab.close()

    def test_none_values_render_as_na(self, qapp):
        tab = OnChainAnalysisTab()
        tab._current_levels_table = LevelsTable(
            expiration="27MAR26",
            rows=(_row(95000.0, net_gex_inferred=None, net_taker_flow=None, delta_1d_iv=None),),
            gex_dex_available=True, exposure_available=True,
            inferred_available=False, net_taker_flow_available=False, delta_1d_iv_available=False,
        )

        tab._populate_levels_table()

        assert tab.levels_table.item(0, tab_module._COL_GEX_INFERRED).text() == "N/A"
        assert tab.levels_table.item(0, tab_module._COL_FLOW).text() == "N/A"
        assert tab.levels_table.item(0, tab_module._COL_IV_CHANGE).text() == "N/A"

        tab.close()

    def test_sorting_by_strike_orders_numerically_not_lexicographically(self, qapp):
        """Reproduces the classic string-sort bug: '95000' would sort
        before '9000' as text; the numeric sort item must not do that."""
        tab = OnChainAnalysisTab()
        tab._current_levels_table = LevelsTable(
            expiration="27MAR26",
            rows=(_row(95000.0), _row(9000.0), _row(20000.0)),
            gex_dex_available=True, exposure_available=True,
            inferred_available=False, net_taker_flow_available=False, delta_1d_iv_available=False,
        )

        tab._populate_levels_table()
        tab.levels_table.sortItems(tab_module._COL_STRIKE, Qt.SortOrder.AscendingOrder)

        strikes_in_order = [
            tab.levels_table.item(i, tab_module._COL_STRIKE).text()
            for i in range(tab.levels_table.rowCount())
        ]
        assert strikes_in_order == ["9,000", "20,000", "95,000"]

        tab.close()

    def test_assumed_convention_colors_the_assumed_gex_column_by_sign(self, qapp):
        tab = OnChainAnalysisTab()
        tab._current_levels_table = LevelsTable(
            expiration="27MAR26",
            rows=(_row(95000.0, net_gex_assumed=-500.0), _row(100000.0, net_gex_assumed=500.0)),
            gex_dex_available=True, exposure_available=True,
            inferred_available=False, net_taker_flow_available=False, delta_1d_iv_available=False,
        )
        tab.assumed_radio.setChecked(True)

        tab._populate_levels_table()

        negative_item = tab.levels_table.item(0, tab_module._COL_GEX_ASSUMED)
        positive_item = tab.levels_table.item(1, tab_module._COL_GEX_ASSUMED)
        assert negative_item.background().color().name() == QColor(Colors.LOSS).name()
        assert positive_item.background().color().name() == QColor(Colors.PROFIT).name()
        # Neither the holder nor the inferred column carries a signal
        # while "assumed" is active -- exactly one column colors at a time.
        assert (
            tab.levels_table.item(0, tab_module._COL_GEX_HOLDER).background().color().name()
            == QColor(Colors.SURFACE).name()
        )
        assert (
            tab.levels_table.item(0, tab_module._COL_GEX_INFERRED).background().color().name()
            == QColor(Colors.SURFACE).name()
        )

        tab.close()

    def test_switching_to_holder_convention_colors_its_own_column_not_assumed(self, qapp):
        """
        Independent review round 1, Important #2: "Holder" must drive its
        own always-visible "Net GEX (holder)" column -- not repaint the
        "Net GEX (assumed)" column using the holder value (the earlier,
        rejected design, which always rendered that column green since
        holder-side gamma exposure is structurally >= 0, regardless of
        the expiry's real assumed-dealer data).
        """
        tab = OnChainAnalysisTab()
        # holder is negative (must color red on its own column); assumed is
        # positive (must reset to neutral once "assumed" is no longer active).
        tab._current_levels_table = LevelsTable(
            expiration="27MAR26",
            rows=(_row(95000.0, net_gex_assumed=500.0, net_gex_holder=-250.0),),
            gex_dex_available=True, exposure_available=True,
            inferred_available=False, net_taker_flow_available=False, delta_1d_iv_available=False,
        )
        tab._populate_levels_table()

        tab.holder_radio.setChecked(True)

        holder_item = tab.levels_table.item(0, tab_module._COL_GEX_HOLDER)
        assumed_item = tab.levels_table.item(0, tab_module._COL_GEX_ASSUMED)
        assert holder_item.background().color().name() == QColor(Colors.LOSS).name()
        assert assumed_item.background().color().name() == QColor(Colors.SURFACE).name()
        # The displayed assumed value itself is untouched by the radio.
        assert assumed_item.text() == "500.00"

        tab.close()

    def test_holder_column_displays_the_holder_value_regardless_of_convention(self, qapp):
        """The "Net GEX (holder)" column's displayed number never changes
        with the radio -- only which column is colored changes."""
        tab = OnChainAnalysisTab()
        tab._current_levels_table = LevelsTable(
            expiration="27MAR26",
            rows=(_row(95000.0, net_gex_holder=42.5),),
            gex_dex_available=True, exposure_available=True,
            inferred_available=False, net_taker_flow_available=False, delta_1d_iv_available=False,
        )
        tab._populate_levels_table()

        assert tab.levels_table.item(0, tab_module._COL_GEX_HOLDER).text() == "42.50"

        tab.assumed_radio.setChecked(True)
        assert tab.levels_table.item(0, tab_module._COL_GEX_HOLDER).text() == "42.50"

        tab.close()

    def test_none_holder_value_skips_coloring_and_renders_na(self, qapp):
        """
        Independent review round 1, Important #1: a missing gex_dex row
        for this strike means net_gex_holder is None, not 0.0 -- the
        holder column must show "N/A" and must not be colored either way.
        """
        tab = OnChainAnalysisTab()
        tab._current_levels_table = LevelsTable(
            expiration="27MAR26",
            rows=(_row(95000.0, net_gex_holder=None, net_gex_assumed=None, net_dex=None, vex=None, cex=None),),
            gex_dex_available=False, exposure_available=False,
            inferred_available=False, net_taker_flow_available=False, delta_1d_iv_available=False,
        )
        tab._populate_levels_table()
        tab.holder_radio.setChecked(True)

        holder_item = tab.levels_table.item(0, tab_module._COL_GEX_HOLDER)
        assert holder_item.text() == "N/A"
        assert holder_item.background().color().name() == QColor(Colors.SURFACE).name()

        tab.close()

    def test_inferred_radio_disabled_when_expiry_has_no_inferred_view(self, qapp):
        tab = OnChainAnalysisTab()
        tab._current_levels_table = LevelsTable(
            expiration="27MAR26", rows=(_row(95000.0),),
            gex_dex_available=True, exposure_available=True,
            inferred_available=False, net_taker_flow_available=False, delta_1d_iv_available=False,
        )

        tab._update_sign_convention_availability()

        assert tab.inferred_radio.isEnabled() is False

        tab.close()

    def test_inferred_radio_enabled_and_colors_inferred_column_when_available(self, qapp):
        tab = OnChainAnalysisTab()
        tab._current_levels_table = LevelsTable(
            expiration="27MAR26",
            rows=(_row(95000.0, net_gex_inferred=-77.0),),
            gex_dex_available=True, exposure_available=True,
            inferred_available=True, net_taker_flow_available=False, delta_1d_iv_available=False,
        )
        tab._update_sign_convention_availability()
        tab._populate_levels_table()

        assert tab.inferred_radio.isEnabled() is True

        tab.inferred_radio.setChecked(True)

        item = tab.levels_table.item(0, tab_module._COL_GEX_INFERRED)
        assert item.background().color().name() == QColor(Colors.LOSS).name()

        tab.close()

    def test_row_marker_shows_call_wall_put_support_hvl_and_max_pain(self, qapp):
        """
        Markers are anchored to the Strike cell itself (background +
        tooltip), not the vertical header -- QTableWidget.sortItems()
        does not move header items with a sorted row, so a header-based
        marker would silently point at the wrong row after any sort (see
        ``_apply_sign_convention_view``'s docstring for the full reasoning).
        """
        tab = OnChainAnalysisTab()
        tab._current_levels_table = LevelsTable(
            expiration="27MAR26",
            rows=(
                _row(90000.0, is_put_support_assumed=True),
                _row(95000.0, is_hvl_assumed=True, is_max_pain=True),
                _row(100000.0, is_call_wall_assumed=True),
                _row(105000.0),
            ),
            gex_dex_available=True, exposure_available=True,
            inferred_available=False, net_taker_flow_available=False, delta_1d_iv_available=False,
        )

        tab._populate_levels_table()

        marker_color = QColor(Colors.ACCENT).name()
        neutral_color = QColor(Colors.SURFACE).name()

        item0 = tab.levels_table.item(0, tab_module._COL_STRIKE)
        item1 = tab.levels_table.item(1, tab_module._COL_STRIKE)
        item2 = tab.levels_table.item(2, tab_module._COL_STRIKE)
        item3 = tab.levels_table.item(3, tab_module._COL_STRIKE)

        assert item0.background().color().name() == marker_color
        assert item0.toolTip() == "Put Support"
        assert item1.background().color().name() == marker_color
        assert item1.toolTip() == "HVL / Max Pain"
        assert item2.background().color().name() == marker_color
        assert item2.toolTip() == "Call Wall"
        assert item3.background().color().name() == neutral_color
        assert item3.toolTip() == ""

        tab.close()

    def test_marker_and_coloring_survive_a_user_sort(self, qapp):
        """
        Reproduces the bug this design had to avoid: sorting the table
        (which Qt implements by moving whole QTableWidgetItems, not by
        moving vertical-header items or re-running per-row logic keyed by
        construction order) must not leave a row's marker/coloring
        pointing at a different row's data.
        """
        tab = OnChainAnalysisTab()
        tab._current_levels_table = LevelsTable(
            expiration="27MAR26",
            rows=(
                _row(85000.0, net_gex_assumed=-500.0, is_put_support_assumed=True),
                _row(100000.0, net_gex_assumed=700.0, is_call_wall_assumed=True),
            ),
            gex_dex_available=True, exposure_available=True,
            inferred_available=False, net_taker_flow_available=False, delta_1d_iv_available=False,
        )
        tab._populate_levels_table()

        # Descending sort flips physical row order: 100000 now at row 0.
        tab.levels_table.sortItems(tab_module._COL_STRIKE, Qt.SortOrder.DescendingOrder)
        tab._apply_sign_convention_view()

        row0_strike = tab.levels_table.item(0, tab_module._COL_STRIKE)
        row1_strike = tab.levels_table.item(1, tab_module._COL_STRIKE)
        assert row0_strike.text() == "100,000"
        assert row1_strike.text() == "85,000"

        # The call-wall marker (100000's) must follow to row 0, not stay at row 1.
        assert row0_strike.toolTip() == "Call Wall"
        assert row1_strike.toolTip() == "Put Support"

        # Coloring must follow too: 100000's assumed GEX (+700) is positive/green,
        # 85000's (-500) is negative/red, regardless of physical row position.
        assert (
            tab.levels_table.item(0, tab_module._COL_GEX_ASSUMED).background().color().name()
            == QColor(Colors.PROFIT).name()
        )
        assert (
            tab.levels_table.item(1, tab_module._COL_GEX_ASSUMED).background().color().name()
            == QColor(Colors.LOSS).name()
        )

        tab.close()


class TestNormalizedMetricsStripPopulation:
    def test_populate_sets_row_count_and_formatted_values(self, qapp):
        tab = OnChainAnalysisTab()
        metrics = {
            "net_gex": NormalizedMetric(
                name="net_gex", value=1_500_000.0, percentile_30d=72.0, z_30d=0.85,
                percentile_90d=64.0, z_90d=0.5, regime_30d="ELEVATED", n_30d=30, n_90d=90,
                sufficient=True, unit="USD",
            ),
            "pcr_oi": NormalizedMetric(
                name="pcr_oi", value=0.842, percentile_30d=None, z_30d=None,
                percentile_90d=None, z_90d=None, regime_30d=None, n_30d=5, n_90d=5,
                sufficient=False, unit="ratio",
            ),
        }

        tab._populate_normalized_metrics_table(metrics)

        assert tab.metrics_table.rowCount() == 2
        # Human-readable labels (Minor, independent review round 1) --
        # not the raw dict key.
        assert tab.metrics_table.item(0, 0).text() == "Net GEX"
        assert tab.metrics_table.item(1, 0).text() == "PCR (OI)"
        assert tab.metrics_table.item(0, 2).text() == "72.0"
        assert tab.metrics_table.item(0, 3).text() == "+0.85"
        assert tab.metrics_table.item(0, 6).text() == "ELEVATED"
        # insufficient history -> N/A, not a crash/blank
        assert tab.metrics_table.item(1, 2).text() == "N/A"
        assert tab.metrics_table.item(1, 6).text() == "N/A"

        tab.close()

    def test_row_order_follows_report_metric_order_not_dict_insertion_order(self, qapp):
        """
        Minor (independent review round 1): the dict is built with "funding"
        inserted before "net_gex" -- the strip must still render net_gex
        first, matching METRIC_ORDER (the same order the text report's
        HISTORICAL CONTEXT section uses), not raw insertion order.
        """
        tab = OnChainAnalysisTab()
        metrics = {
            "funding": NormalizedMetric(
                name="funding", value=0.0002, percentile_30d=50.0, z_30d=0.1,
                percentile_90d=50.0, z_90d=0.1, regime_30d="NORMAL", n_30d=30, n_90d=90,
                sufficient=True, unit="%",
            ),
            "net_gex": NormalizedMetric(
                name="net_gex", value=1_500_000.0, percentile_30d=72.0, z_30d=0.85,
                percentile_90d=64.0, z_90d=0.5, regime_30d="ELEVATED", n_30d=30, n_90d=90,
                sufficient=True, unit="USD",
            ),
            "some_future_metric": NormalizedMetric(
                name="some_future_metric", value=1.0, percentile_30d=50.0, z_30d=0.0,
                percentile_90d=50.0, z_90d=0.0, regime_30d="NORMAL", n_30d=30, n_90d=90,
                sufficient=True, unit="ratio",
            ),
        }

        tab._populate_normalized_metrics_table(metrics)

        labels_in_order = [tab.metrics_table.item(i, 0).text() for i in range(tab.metrics_table.rowCount())]
        # net_gex before funding (METRIC_ORDER), and the unrecognized key
        # (forward-compat) appended last, falling back to its raw name.
        assert labels_in_order == ["Net GEX", "Funding (8h)", "some_future_metric"]

        tab.close()


class TestExpirySelectorWiring:
    def test_on_analysis_finished_populates_expiry_combo_and_levels_table(self, qapp):
        """
        End-to-end through the public seam the worker actually calls:
        ``_on_analysis_finished`` -> stores the typed result -> rebuilds
        the expiry combo -> triggers Levels-table population for the first
        entry -- without a real analysis run (fake OnChainWorkflowOutput
        carrying a minimal real ``OnChainAnalysisResult``).
        """
        from datetime import datetime

        from coding.core.analytics.results.analysis_result import (
            ExpirationBundle,
            MarketMetricsResult,
            OnChainAnalysisResult,
        )
        from coding.core.analytics.results.expiry_results import (
            ExpirationAnalysisResult,
            MaxPainResult,
            MoneynessLeg,
            MoneynessResult,
            PutCallRatioResult,
            StrikeOiRow,
            SupportResistanceResult,
            VolumeStatsResult,
        )

        def _leg():
            return MoneynessLeg(
                itm_oi=0.0, otm_oi=0.0, total_oi=0.0, itm_notional=0.0,
                otm_notional=0.0, total_notional=0.0, itm_pct=0.0, otm_pct=0.0,
            )

        analysis = ExpirationAnalysisResult(
            expiration="27MAR26", underlying_price=95000.0, total_instruments=0,
            call_count=0, put_count=0,
            strike_rows=(StrikeOiRow(strike=95000.0, call_oi=10.0, put_oi=5.0, call_volume=0.0, put_volume=0.0),),
            max_pain=MaxPainResult(max_pain_strike=95000.0, pain_by_strike={}, min_pain_value=0.0),
            put_call_ratio=PutCallRatioResult(total_call_oi=10.0, total_put_oi=5.0, ratio=2.0, bias="Neutral"),
            volume_stats=VolumeStatsResult(total_call_volume=0.0, total_put_volume=0.0, total_volume=0.0, volume_ratio=0.0),
            moneyness=MoneynessResult(calls=_leg(), puts=_leg(), totals=_leg(), oi_skew="Balanced"),
            support_resistance=SupportResistanceResult(
                resistance_levels=(), support_levels=(), short_term_resistance=None, short_term_support=None,
            ),
        )
        bundle = ExpirationBundle(
            expiration="27MAR26", analysis=analysis, gex_dex=None, flow=None, vol_surface=None,
            oi_changes=None, iv_percentile=None, trend=None, flow_chart_paths={}, enriched_instruments=(),
        )
        result = OnChainAnalysisResult(
            currency="BTC", underlying_price=95000.0, generated_at=datetime(2026, 7, 25, 12, 0, 0),
            market_metrics=MarketMetricsResult(dvol=None, iv_percentile=None, iv_rank=None, current_funding=None, funding_8h=None),
            expirations=(bundle,), market_wide=MagicMock(), parsed_instruments={}, atm_iv_by_expiration={},
            recent_trades=(), normalized_metrics={},
        )

        fake_output = MagicMock()
        fake_output.report_text = "REPORT TEXT"
        fake_output.result = result

        tab = OnChainAnalysisTab()
        tab._last_analyzed_currency = "BTC"

        tab._on_analysis_finished(fake_output)

        assert tab.expiry_combo.count() == 1
        # PySide6 round-trips itemData through QVariant, so a stored tuple
        # comes back as a list -- compare element-wise, not tuple-vs-tuple.
        assert list(tab.expiry_combo.itemData(0)) == ["BTC", "27MAR26"]
        assert tab.levels_table.rowCount() == 1
        assert tab.levels_table.item(0, tab_module._COL_STRIKE).text() == "95,000"
        assert tab.levels_table.item(0, tab_module._COL_STRIKE).toolTip() == "Max Pain"
        assert "REPORT TEXT" in tab.report_display.toPlainText()

        tab.close()

    def test_metrics_strip_populates_even_when_result_has_no_expirations(self, qapp):
        """
        Minor (independent review round 1): a result whose
        ``expiration_names()`` is empty leaves the expiry combo empty, so
        ``_on_expiry_selection_changed`` never fires -- but
        ``normalized_metrics`` is currency-wide, not per-expiry, so the
        strip must still populate for the most recently analyzed currency
        rather than staying permanently blank.
        """
        from datetime import datetime

        from coding.core.analytics.historical_normalizer import NormalizedMetric as NM
        from coding.core.analytics.results.analysis_result import MarketMetricsResult, OnChainAnalysisResult

        result = OnChainAnalysisResult(
            currency="BTC", underlying_price=95000.0, generated_at=datetime(2026, 7, 25, 12, 0, 0),
            market_metrics=MarketMetricsResult(dvol=None, iv_percentile=None, iv_rank=None, current_funding=None, funding_8h=None),
            expirations=(), market_wide=MagicMock(), parsed_instruments={}, atm_iv_by_expiration={},
            recent_trades=(),
            normalized_metrics={
                "net_gex": NM(name="net_gex", value=1.0, percentile_30d=50.0, z_30d=0.0,
                              percentile_90d=50.0, z_90d=0.0, regime_30d="NORMAL", n_30d=30, n_90d=90,
                              sufficient=True, unit="USD"),
            },
        )

        fake_output = MagicMock()
        fake_output.report_text = "REPORT TEXT"
        fake_output.result = result

        tab = OnChainAnalysisTab()
        tab._last_analyzed_currency = "BTC"

        tab._on_analysis_finished(fake_output)

        assert tab.expiry_combo.count() == 0
        assert tab.levels_table.rowCount() == 0
        assert tab.metrics_table.rowCount() == 1
        assert tab.metrics_table.item(0, 0).text() == "Net GEX"

        tab.close()


class TestClearResetsSignConvention:
    def test_clear_resets_radios_and_disables_inferred(self, qapp):
        """Minor (independent review round 1): _clear() must not leave a
        previous run's sign-convention state (e.g. "Inferred" checked and
        enabled) stale once that run's data is gone."""
        tab = OnChainAnalysisTab()
        tab.inferred_radio.setEnabled(True)
        tab.inferred_radio.setChecked(True)

        tab._clear()

        assert tab.assumed_radio.isChecked() is True
        assert tab.inferred_radio.isEnabled() is False

        tab.close()
