"""
Regression test for a daemon-breaking bug found in independent review of
Task A4: ProspectiveCollector._run_onchain_analysis fed GexDexCalculator's
new typed GexDexResult (T4) straight into
DatabaseRepository.save_onchain_snapshot(gex_dex_data=...), which still
does dict-style ``gex_dex_data.get("key_levels", {})`` /
``gex_dex_data.get("total_net_gex")`` (compatibility-map row #9, T10-scoped
— it was never supposed to see anything but a legacy dict until T10).
A frozen dataclass has no ``.get()``, so every hourly write to
onchain_analysis_snapshots was raising AttributeError, silently swallowed
by the broad ``except Exception`` in the per-expiration loop.

This test asserts exactly what save_onchain_snapshot receives from
_run_onchain_analysis: a plain dict, with ``key_levels`` and
``total_net_gex`` present in the shapes repository.py's
save_onchain_snapshot expects.
"""

from datetime import datetime
from unittest.mock import MagicMock

import pytest


def _make_collector():
    """Build a ProspectiveCollector with mocked I/O dependencies (no real
    API/DB calls) — mirrors the pattern in test_prospective_collector_ohlcv.py."""
    from coding.service.data_collection.prospective_collector import ProspectiveCollector

    collector = ProspectiveCollector.__new__(ProspectiveCollector)
    collector.api = MagicMock()
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


class TestRunOnchainAnalysisSavesPlainDictGexDexData:
    """C1 regression: save_onchain_snapshot must receive a dict, not a
    frozen GexDexResult, for gex_dex_data."""

    def test_gex_dex_data_passed_to_save_is_a_plain_dict(self, instruments):
        collector = _make_collector()

        collector._run_onchain_analysis(
            currency="BTC",
            hour=datetime(2026, 7, 25, 12, 0, 0),
            instruments=instruments,
        )

        assert collector.repo.save_onchain_snapshot.call_count == 1
        _, kwargs = collector.repo.save_onchain_snapshot.call_args
        gex_dex_data = kwargs["gex_dex_data"]

        # The bug: a frozen dataclass has no .get() at all.
        assert isinstance(gex_dex_data, dict), (
            f"gex_dex_data must be a plain dict for save_onchain_snapshot's "
            f".get() calls to work; got {type(gex_dex_data)!r}"
        )
        assert hasattr(gex_dex_data, "get")

    def test_gex_dex_data_has_shapes_save_onchain_snapshot_expects(self, instruments):
        """repository.py:1084 does gex_dex_data.get("key_levels", {}) then
        .get("call_resistance")/.get("put_support")/.get("hvl"); :1165 does
        gex_dex_data.get("total_net_gex")/.get("total_net_dex"). Assert the
        exact producer output satisfies the exact consumer read pattern —
        the wiring the 717-green-test suite had zero coverage of."""
        collector = _make_collector()

        collector._run_onchain_analysis(
            currency="BTC",
            hour=datetime(2026, 7, 25, 12, 0, 0),
            instruments=instruments,
        )

        _, kwargs = collector.repo.save_onchain_snapshot.call_args
        gex_dex_data = kwargs["gex_dex_data"]

        assert "total_net_gex" in gex_dex_data
        assert "total_net_dex" in gex_dex_data
        assert isinstance(gex_dex_data["total_net_gex"], float)

        key_levels = gex_dex_data.get("key_levels", {})
        assert isinstance(key_levels, dict)
        assert "hvl" in key_levels
        # call_resistance/put_support are either None or {"strike", "net_gex"} dicts
        for level_name in ("call_resistance", "put_support"):
            level = key_levels.get(level_name)
            assert level is None or ("strike" in level and "net_gex" in level)

    def test_no_exception_swallowed_by_broad_except(self, instruments, caplog):
        """Before the fix, the AttributeError was caught by the per-expiration
        `except Exception` and only surfaced as a warning log + snapshots_saved
        staying effectively at 0 useful rows. After the fix, save_onchain_snapshot
        is actually called (no warning about a failed expiration)."""
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
