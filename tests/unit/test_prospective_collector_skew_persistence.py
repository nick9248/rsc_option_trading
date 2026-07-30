"""
Producer -> consumer wiring test for ProspectiveCollector._run_onchain_analysis
-> DatabaseRepository.save_volatility_skew (Task C4, institutional_metrics
_spec.md Migration M3 / section 3).

Mirrors test_prospective_collector_onchain_analysis.py's pattern (mocked
collaborators, plus one real-repository end-to-end test) applied to the new
RR25/BF25 skew persistence added alongside the existing GEX/DEX save.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from coding.core.database.repository import DatabaseRepository


def _make_collector():
    from coding.service.data_collection.prospective_collector import ProspectiveCollector

    collector = ProspectiveCollector.__new__(ProspectiveCollector)
    collector.api = MagicMock()
    collector.api.get_index_price.return_value = 70_000.0
    collector.repo = MagicMock()
    collector._forward_harness = MagicMock()
    return collector


def _instrument(strike, option_type, delta, gamma, mark_iv=60.0, bid_price=1.0, ask_price=1.0, oi=100.0, volume=10.0):
    """
    Like test_prospective_collector_onchain_analysis.py's ``_instrument``,
    plus bid_price/ask_price on the raw (book-summary-shaped) item --
    _enrich_with_greeks now carries these onto the enriched instrument dict
    for the "quoted" filter (institutional_metrics_spec.md section 3(b)
    step 1).
    """
    name = f"BTC-27MAR27-{int(strike)}-{option_type}"
    return {
        "instrument_name": name,
        "open_interest": oi,
        "volume": volume,
        "volume_usd": volume * 70_000.0,
        "mark_price": 0.05,
        "mark_iv": mark_iv,
        "underlying_price": 70_000.0,
        "bid_price": bid_price,
        "ask_price": ask_price,
        "greeks": {"delta": delta, "gamma": gamma, "vega": 100.0, "theta": -50.0},
    }


@pytest.fixture
def instruments():
    """
    A chain that brackets both 25-delta and ATM on both sides, so
    calculate_risk_reversal_butterfly() produces real (non-None) numbers --
    this file is about the WIRING (is the call made, with what params),
    not the interpolation math itself (covered in
    test_volatility_surface_calculator.py).
    """
    return [
        _instrument(80_000, "C", delta=0.30, gamma=0.00002, mark_iv=32.0),
        _instrument(85_000, "C", delta=0.20, gamma=0.00002, mark_iv=36.0),
        _instrument(70_000, "C", delta=0.50, gamma=0.00003, mark_iv=35.0),
        _instrument(60_000, "P", delta=-0.30, gamma=0.00002, mark_iv=40.0),
        _instrument(65_000, "P", delta=-0.20, gamma=0.00002, mark_iv=44.0),
        _instrument(70_000, "P", delta=-0.50, gamma=0.00003, mark_iv=35.0),
    ]


class TestRunOnchainAnalysisSavesSkew:
    def test_save_volatility_skew_called_once_per_expiration(self, instruments):
        collector = _make_collector()

        collector._run_onchain_analysis(
            currency="BTC",
            hour=datetime(2026, 7, 25, 12, 0, 0),
            instruments=instruments,
        )

        # One expiration (27MAR27) in this fixture.
        assert collector.repo.save_volatility_skew.call_count == 1

    def test_save_volatility_skew_receives_the_expected_shape(self, instruments):
        collector = _make_collector()

        collector._run_onchain_analysis(
            currency="BTC",
            hour=datetime(2026, 7, 25, 12, 0, 0),
            instruments=instruments,
        )

        _, kwargs = collector.repo.save_volatility_skew.call_args
        assert kwargs["currency"] == "BTC"
        assert kwargs["expiration"] == "27MAR27"
        assert kwargs["snapshot_hour"] == datetime(2026, 7, 25, 12, 0, 0)
        assert kwargs["dte_years"] is None or isinstance(kwargs["dte_years"], float)

        skew = kwargs["skew"]
        assert skew["rr_25d"] == pytest.approx(-8.0)  # 34.0 - 42.0, same as T3.1
        assert skew["bf_25d"] == pytest.approx(3.0)
        assert skew["method"] == "linear_delta"

    def test_dte_years_is_positive_for_a_future_expiration(self, instruments):
        collector = _make_collector()

        collector._run_onchain_analysis(
            currency="BTC",
            hour=datetime(2026, 7, 25, 12, 0, 0),
            instruments=instruments,
        )

        _, kwargs = collector.repo.save_volatility_skew.call_args
        assert kwargs["dte_years"] > 0

    def test_skew_save_failure_does_not_block_gex_dex_save(self, instruments):
        """A skew-calculation/save failure must be isolated -- the GEX/DEX
        save (which runs first and already succeeded) must not be undone,
        and the daemon must not crash."""
        collector = _make_collector()
        collector.repo.save_volatility_skew.side_effect = RuntimeError("db hiccup")

        collector._run_onchain_analysis(
            currency="BTC",
            hour=datetime(2026, 7, 25, 12, 0, 0),
            instruments=instruments,
        )

        assert collector.repo.save_onchain_snapshot.called

    def test_skew_save_failure_is_logged_not_silently_swallowed(self, instruments, caplog):
        collector = _make_collector()
        collector.repo.save_volatility_skew.side_effect = RuntimeError("db hiccup")

        with caplog.at_level("WARNING"):
            collector._run_onchain_analysis(
                currency="BTC",
                hour=datetime(2026, 7, 25, 12, 0, 0),
                instruments=instruments,
            )

        assert any(
            "Failed to compute/save RR25/BF25 skew" in r.message for r in caplog.records
        )


class TestEndToEndAgainstRealSaveVolatilitySkew:
    """Same rigor as test_prospective_collector_onchain_analysis.py's
    end-to-end class: run the REAL _run_onchain_analysis against a REAL
    DatabaseRepository.save_volatility_skew (only the SQL cursor is
    mocked), so the params reaching execute() are produced by the real
    daemon code path, not hand-built in the test."""

    def _make_collector_with_real_repository(self):
        from coding.service.data_collection.prospective_collector import ProspectiveCollector

        collector = ProspectiveCollector.__new__(ProspectiveCollector)
        collector.api = MagicMock()
        collector.api.get_index_price.return_value = 70_000.0
        collector.repo = DatabaseRepository.__new__(DatabaseRepository)
        collector._forward_harness = MagicMock()
        return collector

    def test_real_save_volatility_skew_executes_without_raising(self, instruments):
        collector = self._make_collector_with_real_repository()

        mock_cursor = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_cursor)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        with patch.object(collector.repo, "_db_cursor", return_value=mock_ctx):
            collector._run_onchain_analysis(
                currency="BTC",
                hour=datetime(2026, 7, 25, 12, 0, 0),
                instruments=instruments,
            )

        # save_onchain_snapshot + save_volatility_skew -> at least 2 execute()s.
        assert mock_cursor.execute.call_count >= 2
        insert_calls = [
            call for call in mock_cursor.execute.call_args_list
            if "volatility_skew_history" in call.args[0]
        ]
        assert len(insert_calls) == 1
        _, params = insert_calls[0].args
        assert not isinstance(params, dict) or all(
            not isinstance(v, dict) for v in params.values()
        )
