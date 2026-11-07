"""
Tests for straddle scanner wiring in ProspectiveCollector.collect_hour()
(increment 2, Part 4). Mirrors the pattern used in
test_prospective_collector_volatility_reconstruction.py.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch


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

    hour = datetime(2026, 7, 13, 14, 0, 0)
    result = collector.collect_hour(currencies=["BTC", "ETH"], hour=hour)

    assert collector._straddle_scan_service.scan.call_count == 2
    assert collector._straddle_harness.record_scan.call_count == 2
    assert collector._straddle_harness.resolve_due.call_count == 2

    assert result["straddle_scanner"]["BTC"] == {"inserted": 1}
    assert result["straddle_scanner"]["ETH"] == {"inserted": 1}


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
    # Force an exception at the outermost level of the scanner block: the
    # currencies list itself fails to iterate, outside the per-currency try.
    collector._straddle_scan_service.scan.return_value = _fake_scan_result("BTC")
    collector._run_defined_risk_scanners = MagicMock()

    class _ExplodingCurrencies(list):
        """Iterates normally for collection, then raises on the scanner loop."""

        def __init__(self, items):
            super().__init__(items)
            self.iterations = 0

        def __iter__(self):
            self.iterations += 1
            if self.iterations > 3:  # collection, aggregation, reconstruction pass
                raise RuntimeError("total meltdown")
            return super().__iter__()

    hour = datetime(2026, 7, 13, 14, 0, 0)
    # Must not raise.
    result = collector.collect_hour(currencies=_ExplodingCurrencies(["BTC"]), hour=hour)
    assert result["status"] == "success"
    assert result["straddle_scanner"] == {}
    collector._straddle_scan_service.scan.assert_not_called()


def test_collect_hour_skips_scanner_when_no_trades_collected():
    collector = _make_collector()
    collector._collect_currency = MagicMock(return_value={"trades": 0, "instruments": 0})

    hour = datetime(2026, 7, 13, 14, 0, 0)
    result = collector.collect_hour(currencies=["BTC"], hour=hour)

    collector._straddle_scan_service.scan.assert_not_called()
    assert "straddle_scanner" not in result


def test_collect_hour_records_all_scan_history_and_sends_nothing_to_telegram():
    """The scanner Telegram alerts are removed: one collect_hour cycle must still
    write straddle, iron condor and butterfly scan history rows through the real
    forward-test harnesses, and make no Telegram request at all."""
    from coding.service.data_collection.prospective_collector import ProspectiveCollector
    from coding.service.scanner.telegram_alert_service import TelegramAlertService

    hour = datetime(2026, 7, 13, 14, 0, 0)
    straddle_scan = {
        "as_of": hour, "currency": "BTC", "index_price": 65000.0, "excluded": [],
        "expiries": [{
            "expiry": "25SEP26", "dte": 68.0, "F": 65100.0, "iv_percentile": 8.2,
            "best": {"strike": 65000.0, "cost_usd": 5000.0,
                     "breakeven_down": 60000.0, "breakeven_up": 70000.0},
        }],
    }
    regime = {"net_gex": 1000.0, "rv_10d": 40.0, "rv_30d": 45.0, "rv_ratio": 0.888, "gate_pass": True}
    iron_condor_scan = {
        "as_of": hour, "currency": "BTC", "index_price": 65000.0, "excluded": [],
        "expiries": [{
            "expiry": "1SEP26", "dte": 39.0, "F": 65000.0, "regime": regime,
            "best": {"short_call": 70000.0, "long_call": 72000.0, "short_put": 58000.0,
                     "long_put": 56000.0, "cost_or_credit": 500.0, "max_loss": 1500.0,
                     "breakeven_lo": 57500.0, "breakeven_hi": 70500.0, "ev": 10.0},
        }],
    }
    butterfly_scan = {
        "as_of": hour, "currency": "BTC", "index_price": 65000.0, "excluded": [],
        "expiries": [{
            "expiry": "1SEP26", "dte": 39.0, "F": 65000.0, "regime": regime,
            "best": {"k1": 60000.0, "k2": 65000.0, "k3": 70000.0, "cost_or_credit": 800.0,
                     "max_profit": 4200.0, "breakeven_lo": 60800.0, "breakeven_hi": 69200.0, "ev": 10.0},
        }],
    }

    with patch("coding.service.data_collection.prospective_collector.DeribitApiService"), \
         patch("coding.service.data_collection.prospective_collector.DatabaseRepository") as repository_class, \
         patch("requests.post") as requests_post, \
         patch("requests.get") as requests_get:
        collector = ProspectiveCollector()
        repo = repository_class.return_value  # shared by the real harnesses built in __init__
        assert collector.repo is repo
        repo.save_straddle_scan.return_value = True
        repo.save_defined_risk_scan.return_value = True
        repo.get_unresolved_straddle_scans.return_value = []
        repo.get_unresolved_defined_risk_scans.return_value = []
        collector._collect_currency = MagicMock(return_value={"trades": 5, "instruments": 10})
        collector.aggregation_service = MagicMock()
        collector.aggregation_service.aggregate_unaggregated_hours.return_value = {"snapshots_created": 1}
        collector._volatility_reconstruction = MagicMock()
        collector._straddle_scan_service = MagicMock()
        collector._straddle_scan_service.scan.return_value = straddle_scan
        collector._regime_gate_service = MagicMock()
        collector._regime_gate_service.compute.return_value = regime
        collector._iron_condor_scan_service = MagicMock()
        collector._iron_condor_scan_service.scan.return_value = iron_condor_scan
        collector._butterfly_scan_service = MagicMock()
        collector._butterfly_scan_service.scan.return_value = butterfly_scan

        result = collector.collect_hour(currencies=["BTC"], hour=hour)

    assert result["straddle_scanner"]["BTC"] == {"inserted": 1}
    assert result["defined_risk_scanner"]["BTC"]["iron_condor"] == {"inserted": 1}
    assert result["defined_risk_scanner"]["BTC"]["butterfly"] == {"inserted": 1}

    straddle_row = repo.save_straddle_scan.call_args.args[0]
    assert (straddle_row["currency"], straddle_row["expiration"], straddle_row["scan_time"]) == ("BTC", "25SEP26", hour)
    assert straddle_row["strike"] == 65000.0
    defined_risk_rows = [call.args[0] for call in repo.save_defined_risk_scan.call_args_list]
    assert [row["structure_type"] for row in defined_risk_rows] == ["iron_condor", "butterfly"]
    assert all(row["scan_time"] == hour and row["gate_pass"] is True for row in defined_risk_rows)

    requests_post.assert_not_called()
    requests_get.assert_not_called()
    # Without credentials a sender would skip the request, so also check that the
    # collector holds no Telegram sender at all.
    assert not any(isinstance(member, TelegramAlertService) for member in vars(collector).values())
