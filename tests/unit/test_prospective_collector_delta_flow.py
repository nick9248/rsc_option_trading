"""
Tests for signed delta-weighted flow persistence in ProspectiveCollector
(institutional_metrics_spec.md section 6 / infra_spec.md section 2 --
task C7).

Mirrors test_prospective_collector_ohlcv.py's pattern (mocked collaborators,
a _collect_currency wiring test) plus the isolation guarantees
test_prospective_collector_volatility_reconstruction.py established for a
similarly-positioned per-currency step.
"""
from datetime import datetime
from unittest.mock import MagicMock

from coding.core.analytics.results.delta_flow_results import FlowBucket


def _make_collector():
    """Build a ProspectiveCollector with mocked dependencies."""
    from coding.service.data_collection.prospective_collector import ProspectiveCollector
    collector = ProspectiveCollector.__new__(ProspectiveCollector)
    collector.api = MagicMock()
    collector.repo = MagicMock()
    collector.aggregation_service = MagicMock()
    collector._delta_flow_calculator = MagicMock()
    return collector


def _bucket(expiration, trade_count=5, skipped_count=0):
    return FlowBucket(
        expiration=expiration, hiro_usd=100.0, premium_usd=10.0, gross_delta_usd=200.0,
        net_contracts=3.0, gross_contracts=6.0, trade_count=trade_count, buy_count=3,
        sell_count=2, skipped_count=skipped_count,
    )


class TestPersistDeltaFlow:
    def test_computes_and_saves_one_row_per_bucket(self):
        collector = _make_collector()
        collector.repo.get_trades_for_delta_flow.return_value = [{"fake": "trade"}]
        collector._delta_flow_calculator.compute_hourly_buckets.return_value = {
            "ALL": _bucket("ALL", trade_count=7),
            "27MAR26": _bucket("27MAR26", trade_count=7),
        }

        hour = datetime(2026, 7, 31, 14, 0, 0)
        collector._persist_delta_flow("BTC", hour)

        assert collector.repo.save_delta_flow_hourly.call_count == 2
        saved_expirations = {
            call.kwargs["bucket"].expiration
            for call in collector.repo.save_delta_flow_hourly.call_args_list
        }
        assert saved_expirations == {"ALL", "27MAR26"}
        for call in collector.repo.save_delta_flow_hourly.call_args_list:
            assert call.kwargs["currency"] == "BTC"
            assert call.kwargs["snapshot_hour"] == hour

    def test_fetches_trades_for_the_exact_hour_window(self):
        collector = _make_collector()
        collector.repo.get_trades_for_delta_flow.return_value = []
        collector._delta_flow_calculator.compute_hourly_buckets.return_value = {}

        hour = datetime(2026, 7, 31, 14, 0, 0)
        collector._persist_delta_flow("BTC", hour)

        collector.repo.get_trades_for_delta_flow.assert_called_once()
        call_args = collector.repo.get_trades_for_delta_flow.call_args
        currency, start_ms, end_ms = call_args[0]
        assert currency == "BTC"
        assert end_ms - start_ms == 3600 * 1000
        assert start_ms == int(hour.timestamp() * 1000)

    def test_zero_trades_still_persists_a_synthesized_all_zero_row(self):
        """Enumeration case: a currency with genuinely zero trades this hour
        must never produce silence -- an absent row is indistinguishable
        from 'the daemon didn't run this hour at all'. The pure calculator
        deliberately returns {} for an empty trade list (core stays pure);
        this service-layer method is where the zero-row decision is made."""
        collector = _make_collector()
        collector.repo.get_trades_for_delta_flow.return_value = []
        collector._delta_flow_calculator.compute_hourly_buckets.return_value = {}

        hour = datetime(2026, 7, 31, 14, 0, 0)
        collector._persist_delta_flow("BTC", hour)

        collector.repo.save_delta_flow_hourly.assert_called_once()
        call = collector.repo.save_delta_flow_hourly.call_args
        bucket = call.kwargs["bucket"]
        assert bucket.expiration == "ALL"
        assert bucket.trade_count == 0
        assert bucket.skipped_count == 0
        assert bucket.hiro_usd == 0.0
        assert bucket.gross_delta_usd == 0.0

    def test_all_trades_skipped_does_not_trigger_synthesis(self):
        """If the calculator already produced an 'ALL' bucket (even an
        all-skipped, zero-trade_count one), the service must persist THAT
        bucket, not overwrite it with a fabricated zero-skip synthetic
        one -- skipped_count must survive to the DB."""
        collector = _make_collector()
        collector.repo.get_trades_for_delta_flow.return_value = [{"fake": "trade"}] * 5
        collector._delta_flow_calculator.compute_hourly_buckets.return_value = {
            "ALL": _bucket("ALL", trade_count=0, skipped_count=5),
            "27MAR26": _bucket("27MAR26", trade_count=0, skipped_count=5),
        }

        hour = datetime(2026, 7, 31, 14, 0, 0)
        collector._persist_delta_flow("BTC", hour)

        assert collector.repo.save_delta_flow_hourly.call_count == 2
        all_calls = [
            c for c in collector.repo.save_delta_flow_hourly.call_args_list
            if c.kwargs["bucket"].expiration == "ALL"
        ]
        assert len(all_calls) == 1
        assert all_calls[0].kwargs["bucket"].skipped_count == 5

    def test_returns_summary_dict(self):
        collector = _make_collector()
        collector.repo.get_trades_for_delta_flow.return_value = [{"fake": "trade"}]
        collector._delta_flow_calculator.compute_hourly_buckets.return_value = {
            "ALL": _bucket("ALL", trade_count=7, skipped_count=1),
        }

        hour = datetime(2026, 7, 31, 14, 0, 0)
        result = collector._persist_delta_flow("BTC", hour)

        assert result["total_trade_count"] == 7
        assert result["total_skipped_count"] == 1
        assert result["expirations_written"] == 1


class TestCollectCurrencyWiring:
    def test_collect_currency_calls_persist_delta_flow(self):
        """Mirrors test_prospective_collector_ohlcv.py's
        test_collect_currency_calls_fetch_ohlcv -- same per-currency step
        pattern, one new step added."""
        collector = _make_collector()
        collector._fetch_trades = MagicMock(return_value={"count": 5})
        collector._fetch_book_summary = MagicMock(return_value={"count": 10, "instruments": []})
        collector._run_onchain_analysis = MagicMock()
        collector._fetch_dvol = MagicMock()
        collector._fetch_funding_rate = MagicMock()
        collector._fetch_ohlcv = MagicMock()
        collector._persist_delta_flow = MagicMock()

        hour = datetime(2026, 3, 14)
        collector._collect_currency("BTC", hour)

        collector._persist_delta_flow.assert_called_once_with("BTC", hour)

    def test_persist_delta_flow_failure_does_not_break_collect_currency(self):
        """Own try/except, isolated from the other per-currency steps
        (task-C7-brief.md constraint) -- a failure here must not raise out
        of _collect_currency, and must not suppress or be suppressed by
        the other steps' own try/except blocks."""
        collector = _make_collector()
        collector._fetch_trades = MagicMock(return_value={"count": 5})
        collector._fetch_book_summary = MagicMock(return_value={"count": 10, "instruments": []})
        collector._run_onchain_analysis = MagicMock()
        collector._fetch_dvol = MagicMock()
        collector._fetch_funding_rate = MagicMock()
        collector._fetch_ohlcv = MagicMock()
        collector._persist_delta_flow = MagicMock(side_effect=Exception("boom"))

        result = collector._collect_currency("BTC", datetime(2026, 3, 14))  # must not raise

        assert result["trades"] == 5
        collector._fetch_dvol.assert_called_once()
        collector._fetch_ohlcv.assert_called_once()

    def test_other_step_failure_does_not_skip_persist_delta_flow(self):
        """The isolation cuts both ways: an earlier step's failure (e.g.
        _fetch_dvol) must not prevent _persist_delta_flow from still
        running for this currency."""
        collector = _make_collector()
        collector._fetch_trades = MagicMock(return_value={"count": 5})
        collector._fetch_book_summary = MagicMock(return_value={"count": 10, "instruments": []})
        collector._run_onchain_analysis = MagicMock()
        collector._fetch_dvol = MagicMock(side_effect=Exception("dvol boom"))
        collector._fetch_funding_rate = MagicMock()
        collector._fetch_ohlcv = MagicMock()
        collector._persist_delta_flow = MagicMock()

        collector._collect_currency("BTC", datetime(2026, 3, 14))

        collector._persist_delta_flow.assert_called_once()
