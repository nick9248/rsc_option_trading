"""
Tests for straddle scanner wiring in ProspectiveCollector.collect_hour()
(increment 2, Part 4). Mirrors the pattern used in
test_prospective_collector_volatility_reconstruction.py.
"""

from datetime import datetime
from unittest.mock import MagicMock


def _make_collector():
    """Build a ProspectiveCollector with mocked dependencies."""
    from coding.service.data_collection.prospective_collector import ProspectiveCollector
    collector = ProspectiveCollector.__new__(ProspectiveCollector)
    collector.api = MagicMock()
    collector.repo = MagicMock()
    collector.aggregation_service = MagicMock()
    collector.aggregation_service.aggregate_unaggregated_hours.return_value = {"snapshots_created": 3}
    collector._forward_harness = MagicMock()
    collector._volatility_reconstruction = MagicMock()
    collector._volatility_reconstruction.reconstruct_range.return_value = {
        "pairs_found": 0, "rows_saved": 0, "rows_skipped": 0, "percentile_updated": 0
    }
    collector._straddle_scan_service = MagicMock()
    collector._straddle_harness = MagicMock()
    collector._straddle_alert_rule = MagicMock()
    collector._straddle_telegram = MagicMock()
    return collector


def _fake_scan_result(currency="BTC"):
    return {
        "as_of": datetime(2026, 7, 13, 14, 0, 0),
        "currency": currency,
        "index_price": 65000.0,
        "expiries": [{"expiry": "25SEP26", "iv_percentile": 8.2, "dte": 68.0}],
        "excluded": [],
    }


def test_collect_hour_runs_straddle_scanner_per_currency():
    collector = _make_collector()
    collector._collect_currency = MagicMock(return_value={"trades": 5, "instruments": 10})
    collector._straddle_scan_service.scan.side_effect = lambda currency: _fake_scan_result(currency)
    collector._straddle_harness.record_scan.return_value = 1
    collector._straddle_harness.resolve_due.return_value = 0
    collector._straddle_alert_rule.should_alert.return_value = (False, {"expiry": "25SEP26"}, "below threshold")

    hour = datetime(2026, 7, 13, 14, 0, 0)
    result = collector.collect_hour(currencies=["BTC", "ETH"], hour=hour)

    assert collector._straddle_scan_service.scan.call_count == 2
    assert collector._straddle_harness.record_scan.call_count == 2
    assert collector._straddle_harness.resolve_due.call_count == 2
    assert collector._straddle_alert_rule.should_alert.call_count == 2
    collector._straddle_telegram.send.assert_not_called()

    assert result["straddle_scanner"]["BTC"]["inserted"] == 1
    assert result["straddle_scanner"]["ETH"]["alert_sent"] is False


def test_collect_hour_sends_alert_and_marks_sent_row_when_rule_fires():
    collector = _make_collector()
    collector._collect_currency = MagicMock(return_value={"trades": 5, "instruments": 10})
    collector._straddle_scan_service.scan.return_value = _fake_scan_result("BTC")
    collector._straddle_harness.record_scan.return_value = 1
    collector._straddle_harness.resolve_due.return_value = 0
    top_entry = {"expiry": "25SEP26", "iv_percentile": 8.2}
    collector._straddle_alert_rule.should_alert.return_value = (True, top_entry, "no prior alert")
    collector._straddle_scan_service.format_alert.return_value = "STRADDLE SCANNER ALERT TEXT"
    collector._straddle_telegram.send.return_value = True

    hour = datetime(2026, 7, 13, 14, 0, 0)
    result = collector.collect_hour(currencies=["BTC"], hour=hour)

    collector._straddle_telegram.send.assert_called_once_with("STRADDLE SCANNER ALERT TEXT")
    collector.repo.mark_straddle_scan_alert_sent.assert_called_once_with(
        currency="BTC", expiration="25SEP26", scan_time=hour,
    )
    assert result["straddle_scanner"]["BTC"]["alert_sent"] is True


def test_collect_hour_alert_send_failure_does_not_mark_row_sent():
    collector = _make_collector()
    collector._collect_currency = MagicMock(return_value={"trades": 5, "instruments": 10})
    collector._straddle_scan_service.scan.return_value = _fake_scan_result("BTC")
    collector._straddle_harness.record_scan.return_value = 1
    top_entry = {"expiry": "25SEP26", "iv_percentile": 8.2}
    collector._straddle_alert_rule.should_alert.return_value = (True, top_entry, "no prior alert")
    collector._straddle_telegram.send.return_value = False  # Telegram send failed

    hour = datetime(2026, 7, 13, 14, 0, 0)
    result = collector.collect_hour(currencies=["BTC"], hour=hour)

    collector.repo.mark_straddle_scan_alert_sent.assert_not_called()
    assert result["straddle_scanner"]["BTC"]["alert_sent"] is False


def test_collect_hour_scanner_failure_does_not_break_collection():
    """A scan()/harness failure for one currency must not raise or flip the
    overall collection result status -- same isolation guarantee as
    ForwardTestingHarness/VolatilityReconstructionService."""
    collector = _make_collector()
    collector._collect_currency = MagicMock(return_value={"trades": 5, "instruments": 10})
    collector._straddle_scan_service.scan.side_effect = Exception("boom")

    hour = datetime(2026, 7, 13, 14, 0, 0)
    result = collector.collect_hour(currencies=["BTC"], hour=hour)

    assert result["status"] == "success"
    assert "error" in result["straddle_scanner"]["BTC"]


def test_collect_hour_entire_scanner_block_exception_does_not_propagate():
    """Even a failure constructing/using the scanner components entirely
    (not just a per-currency exception) must never escape collect_hour."""
    collector = _make_collector()
    collector._collect_currency = MagicMock(return_value={"trades": 5, "instruments": 10})
    # Force an exception at the outermost level of the scanner block, e.g.
    # a bug iterating currencies itself, not just inside the per-currency try.
    collector._straddle_alert_rule.should_alert.side_effect = RuntimeError("total meltdown")
    collector._straddle_scan_service.scan.return_value = _fake_scan_result("BTC")

    hour = datetime(2026, 7, 13, 14, 0, 0)
    # Must not raise.
    result = collector.collect_hour(currencies=["BTC"], hour=hour)
    assert result["status"] == "success"


def test_collect_hour_skips_scanner_when_no_trades_collected():
    collector = _make_collector()
    collector._collect_currency = MagicMock(return_value={"trades": 0, "instruments": 0})

    hour = datetime(2026, 7, 13, 14, 0, 0)
    result = collector.collect_hour(currencies=["BTC"], hour=hour)

    collector._straddle_scan_service.scan.assert_not_called()
    assert "straddle_scanner" not in result
