"""Tests for volatility reconstruction wiring in ProspectiveCollector.collect_hour()."""
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
    return collector


def test_collect_hour_runs_volatility_reconstruction_per_currency():
    """After a successful collection cycle, reconstruction runs once per currency
    scoped to exactly the collected hour (start == end == hour)."""
    collector = _make_collector()
    collector._collect_currency = MagicMock(return_value={"trades": 5, "instruments": 10})
    collector._volatility_reconstruction.reconstruct_range.return_value = {
        "pairs_found": 2, "rows_saved": 2, "rows_skipped": 0, "percentile_updated": 1
    }

    hour = datetime(2026, 7, 13, 14, 0, 0)
    result = collector.collect_hour(currencies=["BTC", "ETH"], hour=hour)

    assert collector._volatility_reconstruction.reconstruct_range.call_count == 2
    calls = collector._volatility_reconstruction.reconstruct_range.call_args_list
    called_currencies = {c.kwargs["currency"] for c in calls}
    assert called_currencies == {"BTC", "ETH"}
    for c in calls:
        assert c.kwargs["start"] == hour
        assert c.kwargs["end"] == hour

    assert result["volatility_reconstruction"]["BTC"]["rows_saved"] == 2
    assert result["volatility_reconstruction"]["ETH"]["rows_saved"] == 2


def test_collect_hour_reconstruction_failure_does_not_break_collection():
    """A reconstruction failure for one currency must not raise or flip the
    overall collection result status -- matches the try/except + logger.warning
    pattern used for ForwardTestingHarness."""
    collector = _make_collector()
    collector._collect_currency = MagicMock(return_value={"trades": 5, "instruments": 10})
    collector._volatility_reconstruction.reconstruct_range.side_effect = Exception("boom")

    hour = datetime(2026, 7, 13, 14, 0, 0)
    result = collector.collect_hour(currencies=["BTC"], hour=hour)

    assert result["status"] == "success"
    assert "error" in result["volatility_reconstruction"]["BTC"]


def test_collect_hour_skips_reconstruction_when_no_trades_collected():
    """Reconstruction (like aggregation) only runs when trades were actually
    collected this cycle."""
    collector = _make_collector()
    collector._collect_currency = MagicMock(return_value={"trades": 0, "instruments": 0})

    hour = datetime(2026, 7, 13, 14, 0, 0)
    result = collector.collect_hour(currencies=["BTC"], hour=hour)

    collector._volatility_reconstruction.reconstruct_range.assert_not_called()
    assert "volatility_reconstruction" not in result
