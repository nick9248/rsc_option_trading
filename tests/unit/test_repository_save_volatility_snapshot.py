"""
Unit tests for DatabaseRepository.save_volatility_snapshot's persistence of
the 6 VEX/CEX aggregate columns (Migration 019 / institutional_metrics_spec.md
section 4, Task C5).

Mocked cursor only -- no live database (matches test_repository_save_snapshot.py's
pattern). Schema was verified separately with a read-only SELECT against the
live local database (see task-C5-report.md).
"""
from unittest.mock import MagicMock, patch

from coding.core.database.repository import DatabaseRepository


def _make_repo():
    return DatabaseRepository.__new__(DatabaseRepository)


def _capture_execute(repo, metrics):
    mock_cursor = MagicMock()
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_cursor)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    with patch.object(repo, "_db_cursor", return_value=mock_ctx):
        repo.save_volatility_snapshot(
            snapshot_hour="2026-01-01 00:00:00",
            currency="BTC",
            expiration="02APR26",
            metrics=metrics,
            underlying_price=64000.0,
        )

    args, _ = mock_cursor.execute.call_args
    sql, params = args
    return sql, params


def test_new_vex_cex_columns_present_in_insert_and_update():
    repo = _make_repo()
    sql, _ = _capture_execute(repo, metrics={})
    for column in (
        "vex_holder", "cex_holder",
        "vex_assumed_dealer", "cex_assumed_dealer",
        "vex_peak_strike", "cex_peak_strike",
    ):
        assert column in sql, f"{column} missing from save_volatility_snapshot SQL"
        assert f"{column} = EXCLUDED.{column}" in sql, f"{column} missing from ON CONFLICT UPDATE"


def test_vex_cex_values_passed_through_to_params():
    repo = _make_repo()
    _, params = _capture_execute(repo, metrics={
        "vex_holder": 3420000.5,
        "cex_holder": -290000.25,
        "vex_assumed_dealer": 2844900.0,
        "cex_assumed_dealer": -308000.0,
        "vex_peak_strike": 70000.0,
        "cex_peak_strike": 70000.0,
    })
    assert params["vex_holder"] == 3420000.5
    assert params["cex_holder"] == -290000.25
    assert params["vex_assumed_dealer"] == 2844900.0
    assert params["cex_assumed_dealer"] == -308000.0
    assert params["vex_peak_strike"] == 70000.0
    assert params["cex_peak_strike"] == 70000.0


def test_missing_vex_cex_metrics_default_to_none_not_dropped():
    """A metrics dict without the new keys (e.g. exposure computation
    failed and returned all-None, or an older caller) must still produce a
    row with these params present as None, not a KeyError."""
    repo = _make_repo()
    _, params = _capture_execute(repo, metrics={"atm_iv": 55.0})
    assert params["vex_holder"] is None
    assert params["cex_peak_strike"] is None
    assert params["atm_iv"] == 55.0
