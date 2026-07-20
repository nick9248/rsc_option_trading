"""
Tests that the defined-risk scanners are wired into ProspectiveCollector
with the same isolation guarantee as the straddle scanner: a failure here
can never break the main collection cycle.
"""
from unittest.mock import MagicMock, patch

import pytest


class TestDefinedRiskScannerWiring:
    def test_collect_hour_survives_defined_risk_scanner_exception(self):
        from coding.service.data_collection.prospective_collector import ProspectiveCollector

        with patch("coding.service.data_collection.prospective_collector.DeribitApiService"), \
             patch("coding.service.data_collection.prospective_collector.DatabaseRepository"):
            collector = ProspectiveCollector()
            collector.repo = MagicMock()
            collector._iron_condor_scan_service = MagicMock()
            collector._iron_condor_scan_service.scan.side_effect = Exception("boom")
            collector._butterfly_scan_service = MagicMock()
            collector._butterfly_scan_service.scan.side_effect = Exception("boom")
            collector._regime_gate_service = MagicMock()
            collector._regime_gate_service.compute.return_value = {"gate_pass": False}

            # collect_hour has many other stages; this test only asserts the
            # defined-risk block itself is isolated -- call it directly.
            result = {}
            collector._run_defined_risk_scanners(currencies=["BTC"], hour=MagicMock(), result=result)
            assert "defined_risk_scanner" in result
            assert "error" in result["defined_risk_scanner"]["BTC"]["iron_condor"]
            assert "error" in result["defined_risk_scanner"]["BTC"]["butterfly"]

    def test_successful_cycle_records_and_checks_alert_for_both_structures(self):
        from coding.service.data_collection.prospective_collector import ProspectiveCollector

        with patch("coding.service.data_collection.prospective_collector.DeribitApiService"), \
             patch("coding.service.data_collection.prospective_collector.DatabaseRepository"):
            collector = ProspectiveCollector()
            collector.repo = MagicMock()
            empty_scan = {"currency": "BTC", "as_of": MagicMock(), "index_price": 65000.0, "expiries": [], "excluded": []}
            collector._iron_condor_scan_service = MagicMock()
            collector._iron_condor_scan_service.scan.return_value = empty_scan
            collector._butterfly_scan_service = MagicMock()
            collector._butterfly_scan_service.scan.return_value = empty_scan
            collector._regime_gate_service = MagicMock()
            # Two DISTINCT dict objects (not one fixed return_value) so that a
            # regression which calls compute() once per structure type -- instead
            # of once per currency, with the result shared across both scans --
            # is detectable via object identity below, independent of the
            # call-count assertion.
            regime_call_results = [{"gate_pass": False, "call_seq": 1}, {"gate_pass": False, "call_seq": 2}]
            collector._regime_gate_service.compute.side_effect = regime_call_results
            collector._defined_risk_harness = MagicMock()
            collector._defined_risk_harness.record_scan.return_value = 0
            collector._defined_risk_harness.resolve_due.return_value = 0
            collector._iron_condor_alert_rule = MagicMock()
            collector._iron_condor_alert_rule.should_alert.return_value = (False, None, "no included expiries")
            collector._butterfly_alert_rule = MagicMock()
            collector._butterfly_alert_rule.should_alert.return_value = (False, None, "no included expiries")

            result = {}
            collector._run_defined_risk_scanners(currencies=["BTC"], hour=MagicMock(), result=result)

            assert result["defined_risk_scanner"]["BTC"]["iron_condor"]["alert_sent"] is False
            assert result["defined_risk_scanner"]["BTC"]["butterfly"]["alert_sent"] is False
            collector._defined_risk_harness.record_scan.assert_called()
            collector._defined_risk_harness.resolve_due.assert_called()

            # Design invariant (the entire point of _run_defined_risk_scanners):
            # compute() is called exactly ONCE per currency per cycle, and that
            # SAME precomputed regime object is passed into both the iron condor
            # and butterfly scan calls -- never recomputed per structure type.
            collector._regime_gate_service.compute.assert_called_once_with("BTC")

            ic_regime = collector._iron_condor_scan_service.scan.call_args.kwargs["regime"]
            bf_regime = collector._butterfly_scan_service.scan.call_args.kwargs["regime"]
            assert ic_regime is bf_regime is regime_call_results[0]
