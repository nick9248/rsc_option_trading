"""
Producer -> consumer wiring test for ProspectiveCollector._run_onchain_analysis
-> DatabaseRepository.save_onchain_snapshot (the production daemon's hourly
on-chain snapshot write).

History: this file originally pinned a Task A4 regression where
GexDexCalculator's typed GexDexResult (T4) reached save_onchain_snapshot's
dict-style ``.get()`` reads, raising AttributeError on every hourly write,
silently swallowed by the per-expiration ``except Exception``. That bug was
fixed by keeping gex_dex_data as a plain dict (via ``.to_dict()``) until
T10, when both the producer AND the consumer flip to typed together.

refactor_design_spec.md section T10 (compat-map rows #8/#9): that flip has
now happened in THIS commit --
``OnChainMetricsCalculator.analyze_expiration()`` returns
``ExpirationAnalysisResult`` directly (not a dict) and
``DatabaseRepository.save_onchain_snapshot`` reads ``analysis_data``/
``gex_dex_data`` via attribute access (not ``.get()``). This file's
assertions are inverted to match: it now pins that a *dict* reaching
save_onchain_snapshot would be the bug (no ``.max_pain`` attribute), and
adds an end-to-end test that exercises the REAL ``save_onchain_snapshot``
body (not a mock) against exactly what ``_run_onchain_analysis`` produces --
the class of gap that let the original A4 bug through 717 green tests: a
producer and consumer each individually tested against a shape the other
side didn't actually send.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from coding.core.analytics.results.expiry_results import ExpirationAnalysisResult
from coding.core.analytics.results.gex_dex_results import GexDexResult
from coding.core.database.repository import DatabaseRepository


def _make_collector():
    """Build a ProspectiveCollector with mocked I/O dependencies (no real
    API/DB calls) — mirrors the pattern in test_prospective_collector_ohlcv.py.

    bugfix_spec.md Item 7: ``_run_onchain_analysis`` now calls
    ``self.api.get_index_price`` -- a bare ``MagicMock()`` would return
    another MagicMock here, which arithmetic (``spot_price ** 2`` etc.)
    silently propagates as a MagicMock instead of raising, corrupting
    ``total_net_gex``/``total_net_dex`` (fails ``isinstance(..., float)``
    downstream, e.g. in
    ``test_gex_dex_data_has_the_attributes_save_onchain_snapshot_reads``)
    without ever surfacing as an error. Fixed return value keeps the
    daemon's real arithmetic real.
    """
    from coding.service.data_collection.prospective_collector import ProspectiveCollector

    collector = ProspectiveCollector.__new__(ProspectiveCollector)
    collector.api = MagicMock()
    collector.api.get_index_price.return_value = 70_000.0
    collector.repo = MagicMock()
    collector._forward_harness = MagicMock()
    return collector


def _instrument(strike, option_type, delta, gamma, oi=100.0, volume=10.0):
    name = f"BTC-27MAR26-{int(strike)}-{option_type}"
    return {
        "instrument_name": name,
        "open_interest": oi,
        "volume": volume,
        "volume_usd": volume * 70_000.0,
        "mark_price": 0.05,
        "mark_iv": 60.0,
        "underlying_price": 70_000.0,
        # nested "greeks" — _enrich_with_greeks reads this from raw_by_name
        # (parse_instruments() strips greeks from the top-level parsed dict).
        "greeks": {"delta": delta, "gamma": gamma, "vega": 100.0, "theta": -50.0},
    }


@pytest.fixture
def instruments():
    return [
        _instrument(70_000, "C", delta=0.5, gamma=0.00003),
        _instrument(70_000, "P", delta=-0.5, gamma=0.00003),
    ]


class TestRunOnchainAnalysisSavesTypedResults:
    """
    T10: save_onchain_snapshot's params must be the typed producer objects,
    not dicts -- the inverse of the pre-T10 pinned bug now that both sides
    have flipped together in the same commit.
    """

    def test_analysis_data_passed_to_save_is_the_typed_result(self, instruments):
        collector = _make_collector()

        collector._run_onchain_analysis(
            currency="BTC",
            hour=datetime(2026, 7, 25, 12, 0, 0),
            instruments=instruments,
        )

        assert collector.repo.save_onchain_snapshot.call_count == 1
        _, kwargs = collector.repo.save_onchain_snapshot.call_args
        analysis_data = kwargs["analysis_data"]

        assert isinstance(analysis_data, ExpirationAnalysisResult), (
            f"analysis_data must be the typed ExpirationAnalysisResult for "
            f"save_onchain_snapshot's attribute reads to work; got {type(analysis_data)!r}"
        )
        assert not isinstance(analysis_data, dict)

    def test_gex_dex_data_passed_to_save_is_the_typed_result(self, instruments):
        collector = _make_collector()

        collector._run_onchain_analysis(
            currency="BTC",
            hour=datetime(2026, 7, 25, 12, 0, 0),
            instruments=instruments,
        )

        _, kwargs = collector.repo.save_onchain_snapshot.call_args
        gex_dex_data = kwargs["gex_dex_data"]

        assert isinstance(gex_dex_data, GexDexResult), (
            f"gex_dex_data must be the typed GexDexResult for "
            f"save_onchain_snapshot's attribute reads to work; got {type(gex_dex_data)!r}"
        )
        assert not isinstance(gex_dex_data, dict)

    def test_gex_dex_data_has_the_attributes_save_onchain_snapshot_reads(self, instruments):
        """
        repository.py reads gex_dex_data.total_net_gex/.total_net_dex and
        gex_dex_data.key_levels.{call_resistance,put_support,hvl}. Assert
        the exact producer output satisfies the exact consumer read
        pattern — the wiring the original 717-green-test suite had zero
        coverage of (that gap is exactly what let the A4 bug through).
        """
        collector = _make_collector()

        collector._run_onchain_analysis(
            currency="BTC",
            hour=datetime(2026, 7, 25, 12, 0, 0),
            instruments=instruments,
        )

        _, kwargs = collector.repo.save_onchain_snapshot.call_args
        gex_dex_data = kwargs["gex_dex_data"]

        assert isinstance(gex_dex_data.total_net_gex, float)
        assert isinstance(gex_dex_data.total_net_dex, float)

        key_levels = gex_dex_data.key_levels
        assert key_levels.hvl is None or isinstance(key_levels.hvl, float)
        for level in (key_levels.call_resistance, key_levels.put_support):
            assert level is None or (hasattr(level, "strike") and hasattr(level, "net_gex"))

    def test_no_exception_swallowed_by_broad_except(self, instruments, caplog):
        """Before the A4 fix, a shape mismatch here raised AttributeError,
        caught by the per-expiration `except Exception` and only surfaced
        as a warning log — the daemon kept running, saving nothing, with
        no loud failure. After the fix (both sides typed together),
        save_onchain_snapshot is actually called with no such warning."""
        collector = _make_collector()

        collector._run_onchain_analysis(
            currency="BTC",
            hour=datetime(2026, 7, 25, 12, 0, 0),
            instruments=instruments,
        )

        assert collector.repo.save_onchain_snapshot.called
        failure_logs = [
            r for r in caplog.records if "Failed to analyze expiration" in r.message
        ]
        assert failure_logs == []


class TestEndToEndAgainstRealSaveOnchainSnapshot:
    """
    The A4 lesson, applied: a producer-side test asserting shapes and a
    consumer-side test asserting reads can BOTH pass while disagreeing
    with each other, if neither actually calls the other for real. This
    class removes that gap — it runs the REAL _run_onchain_analysis
    against a REAL DatabaseRepository.save_onchain_snapshot (only the SQL
    cursor is mocked, exactly as test_repository_onchain_snapshot.py
    already does), so the params that actually reach the SQL execute()
    call are produced by the real daemon code path end to end, not
    reconstructed by hand in a test fixture.
    """

    def _make_collector_with_real_repository(self):
        from coding.service.data_collection.prospective_collector import ProspectiveCollector

        collector = ProspectiveCollector.__new__(ProspectiveCollector)
        collector.api = MagicMock()
        # bugfix_spec.md Item 7: see _make_collector()'s docstring above --
        # a bare MagicMock return value would silently corrupt the real
        # GEX/DEX float arithmetic this class exercises end to end.
        collector.api.get_index_price.return_value = 70_000.0
        collector.repo = DatabaseRepository.__new__(DatabaseRepository)
        collector._forward_harness = MagicMock()
        return collector

    def test_real_save_onchain_snapshot_executes_without_raising(self, instruments):
        collector = self._make_collector_with_real_repository()

        mock_cursor = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__enter__ = MagicMock(return_value=mock_cursor)
        mock_ctx.__exit__ = MagicMock(return_value=False)

        with patch.object(collector.repo, "_db_cursor", return_value=mock_ctx):
            # No exception means the real save_onchain_snapshot body
            # (attribute access on analysis_data/gex_dex_data) accepted
            # exactly what _run_onchain_analysis produced.
            collector._run_onchain_analysis(
                currency="BTC",
                hour=datetime(2026, 7, 25, 12, 0, 0),
                instruments=instruments,
            )

        assert mock_cursor.execute.called

    def test_real_save_onchain_snapshot_sql_params_contain_no_dicts_or_dataclasses(self, instruments):
        """
        psycopg2 cannot adapt a dict or a dataclass instance -- this is
        exactly the regression test_repository_onchain_snapshot.py already
        pins at the unit level, replicated here end to end (real
        _run_onchain_analysis -> real save_onchain_snapshot -> real SQL
        param tuple).
        """
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

        assert mock_cursor.execute.call_count >= 1
        _, params = mock_cursor.execute.call_args[0]
        for value in params:
            assert not isinstance(value, dict), f"dict leaked into SQL params: {value}"
            assert not hasattr(value, "__dataclass_fields__"), (
                f"dataclass instance leaked into SQL params: {value!r}"
            )


class TestShapeMismatchIsNotSilentlySwallowed:
    """
    Review fix (task A6, Important #3 -- the A4 lesson applied): before
    this fix, ANY exception from the per-expiration save (including a
    producer/consumer shape mismatch -- exactly the class of bug A4 was)
    was caught by a bare `except Exception`, logged at WARNING, and the
    loop moved on -- leaving only an INFO-level "Saved N snapshots" line
    as the only trace, easy to miss in a healthy-looking log stream.
    AttributeError/TypeError now propagate instead (re-raised past this
    method's own outer handler, which logs at ERROR with a full
    traceback); genuine per-expiration data problems are still gracefully
    skipped so one bad expiration doesn't take down the others.
    """

    def test_attribute_error_from_save_onchain_snapshot_propagates(self, instruments):
        collector = _make_collector()
        collector.repo.save_onchain_snapshot.side_effect = AttributeError(
            "'dict' object has no attribute 'max_pain'"
        )

        with pytest.raises(AttributeError):
            collector._run_onchain_analysis(
                currency="BTC",
                hour=datetime(2026, 7, 25, 12, 0, 0),
                instruments=instruments,
            )

    def test_type_error_from_save_onchain_snapshot_propagates(self, instruments):
        collector = _make_collector()
        collector.repo.save_onchain_snapshot.side_effect = TypeError(
            "argument of type 'GexDexResult' is not iterable"
        )

        with pytest.raises(TypeError):
            collector._run_onchain_analysis(
                currency="BTC",
                hour=datetime(2026, 7, 25, 12, 0, 0),
                instruments=instruments,
            )

    def test_propagated_shape_error_is_logged_at_error_level_with_traceback(self, instruments, caplog):
        import logging

        collector = _make_collector()
        collector.repo.save_onchain_snapshot.side_effect = AttributeError("boom")

        with caplog.at_level(logging.ERROR):
            with pytest.raises(AttributeError):
                collector._run_onchain_analysis(
                    currency="BTC",
                    hour=datetime(2026, 7, 25, 12, 0, 0),
                    instruments=instruments,
                )

        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert error_records, "Expected an ERROR-level log record, got none"

    def test_key_error_still_gracefully_skipped_not_propagated(self, instruments):
        """A genuine per-expiration data problem (not a shape/programming
        error) must still be caught and logged, not propagated -- the
        broad except still does its job for real bad-data cases."""
        collector = _make_collector()
        collector.repo.save_onchain_snapshot.side_effect = KeyError("missing_field")

        # Must not raise -- KeyError is not in the re-raised set.
        collector._run_onchain_analysis(
            currency="BTC",
            hour=datetime(2026, 7, 25, 12, 0, 0),
            instruments=instruments,
        )
