"""Tests for save_onchain_snapshot parameter mapping.

Regression: GexDexCalculator._detect_key_levels() returns call_resistance and
put_support as dicts ({"strike": ..., "net_gex": ...}). save_onchain_snapshot
must extract the strike scalar — passing the dict raw makes psycopg2 fail with
"can't adapt type 'dict'" (observed on the VPS on 2026-07-13, saving 0 rows).
"""
from unittest.mock import MagicMock, patch

from coding.core.database.repository import DatabaseRepository


def _make_repo():
    return DatabaseRepository.__new__(DatabaseRepository)


def _capture_params(repo, analysis_data, gex_dex_data):
    mock_cursor = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_cursor)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    with patch.object(repo, "_db_cursor", return_value=mock_ctx):
        repo.save_onchain_snapshot(
            snapshot_hour="2026-07-13 17:00:00",
            currency="BTC",
            expiration="25SEP26",
            analysis_data=analysis_data,
            gex_dex_data=gex_dex_data,
            underlying_price=62000.0,
        )

    return mock_cursor.execute.call_args[0][1]


def test_key_level_dicts_are_flattened_to_strike_scalars():
    """call_resistance/put_support dicts from GexDexCalculator must not reach SQL."""
    repo = _make_repo()

    # Shape exactly as GexDexCalculator.calculate() produces with real greeks
    gex_dex_data = {
        "total_net_gex": 1_234_567.89,
        "total_net_dex": -42.5,
        "key_levels": {
            "call_resistance": {"strike": 70000.0, "net_gex": 900_000.0},
            "put_support": {"strike": 55000.0, "net_gex": -600_000.0},
            "hvl": 62000.0,
            "gamma_flip": 61000.0,
        },
    }

    params = _capture_params(repo, analysis_data={}, gex_dex_data=gex_dex_data)

    for value in params:
        assert not isinstance(value, dict), f"dict leaked into SQL params: {value}"

    # call_resistance_strike and put_support_strike are params 12 and 13 (0-indexed 11, 12)
    assert params[11] == 70000.0
    assert params[12] == 55000.0
    assert params[13] == 62000.0  # hvl is already a scalar


def test_none_key_levels_stay_none():
    """Zero-greek data (all levels None) must keep saving NULLs as before."""
    repo = _make_repo()

    gex_dex_data = {
        "total_net_gex": 0.0,
        "total_net_dex": 0.0,
        "key_levels": {
            "call_resistance": None,
            "put_support": None,
            "hvl": None,
            "gamma_flip": None,
        },
    }

    params = _capture_params(repo, analysis_data={}, gex_dex_data=gex_dex_data)

    assert params[11] is None
    assert params[12] is None
    assert params[13] is None
