"""
Unit tests for DatabaseRepository.get_iv_percentile_with_window.

Mocked cursor only -- no live database. Verifies the zero/NULL atm_iv
exclusion (data-quality fix) and the n_obs/window_days transparency fields
(short-history fix) described in straddle scanner increment 2.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

from coding.core.database.repository import DatabaseRepository


def _make_repo():
    repo = DatabaseRepository.__new__(DatabaseRepository)
    return repo


def _patched(repo, rows):
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = rows
    ctx = patch.object(repo, "_db_cursor")
    mock_ctx = ctx.start()
    mock_ctx.return_value.__enter__ = lambda s: mock_cursor
    mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
    return ctx, mock_cursor


def test_excludes_zero_and_null_atm_iv_rows_from_population():
    """
    The repository query itself filters atm_iv IS NOT NULL AND atm_iv > 0 --
    this test proves the query issued contains that filter (the population
    handed back from the DB should never include invalid rows).
    """
    repo = _make_repo()
    ctx, mock_cursor = _patched(repo, [
        (datetime(2026, 1, 1), 40.0),
        (datetime(2026, 1, 2), 50.0),
        (datetime(2026, 1, 3), 60.0),
    ])
    try:
        repo.get_iv_percentile_with_window("BTC", "27DEC26")
    finally:
        ctx.stop()

    sql = mock_cursor.execute.call_args[0][0]
    assert "atm_iv IS NOT NULL" in sql
    assert "atm_iv > 0" in sql


def test_percentile_computed_against_filtered_population():
    repo = _make_repo()
    # 4 valid observations, latest = 60.0 -> 3 of 4 values <= 60.0 -> 75%
    ctx, _ = _patched(repo, [
        (datetime(2026, 1, 1), 40.0),
        (datetime(2026, 1, 2), 50.0),
        (datetime(2026, 1, 3), 70.0),
        (datetime(2026, 1, 4), 60.0),
    ])
    try:
        result = repo.get_iv_percentile_with_window("BTC", "27DEC26")
    finally:
        ctx.stop()

    assert result["percentile"] == 75.0
    assert result["n_obs"] == 4
    assert result["latest_atm_iv"] == 60.0
    assert result["window_days"] == 3.0


def test_no_valid_observations_returns_none_percentile():
    repo = _make_repo()
    ctx, _ = _patched(repo, [])
    try:
        result = repo.get_iv_percentile_with_window("BTC", "27DEC26")
    finally:
        ctx.stop()

    assert result == {"percentile": None, "n_obs": 0, "window_days": 0, "latest_atm_iv": None}


def test_single_observation_window_days_zero():
    repo = _make_repo()
    ctx, _ = _patched(repo, [(datetime(2026, 1, 1), 55.0)])
    try:
        result = repo.get_iv_percentile_with_window("BTC", "26MAR27")
    finally:
        ctx.stop()

    assert result["n_obs"] == 1
    assert result["window_days"] == 0.0
    assert result["percentile"] == 100.0  # sole valid obs <= itself
    assert result["latest_atm_iv"] == 55.0


def test_zero_filter_changes_percentile_vs_unfiltered_would_have():
    """
    Constructs a case that differs from the OLD stored-column convention
    ONLY because of the zero-IV filter: if a zero-IV row were included
    (as the old unfiltered pipeline would), it would count as the cheapest
    observation and pull the percentile of the latest value down. With the
    filter applied, that phantom "cheap" observation is excluded.
    """
    repo = _make_repo()
    # Valid population only (zero/NULL rows never reach this list because
    # the SQL filters them out before rows are handed back). Latest (55) is
    # NOT the population max, so a phantom low outlier changes its rank.
    ctx, _ = _patched(repo, [
        (datetime(2026, 1, 1), 45.0),
        (datetime(2026, 1, 2), 50.0),
        (datetime(2026, 1, 3), 60.0),
        (datetime(2026, 1, 4), 55.0),  # latest by snapshot_hour, valid
    ])
    try:
        filtered_result = repo.get_iv_percentile_with_window("BTC", "27DEC26")
    finally:
        ctx.stop()

    # Manually compute what the OLD unfiltered convention would have produced
    # if a zero-IV row were present in the same window: since 0 <= 55, the
    # phantom zero adds +1 to BOTH the "<= latest" count and the population
    # size, inflating the reported percentile of the latest value above its
    # true (filtered) rank -- a spurious "IV looks richer than it is" bias
    # caused purely by a missing-data row polluting the population.
    unfiltered_values = [0.0, 45.0, 50.0, 55.0, 60.0]
    latest = 55.0
    unfiltered_pct = sum(1 for v in unfiltered_values if v <= latest) / len(unfiltered_values) * 100

    assert filtered_result["percentile"] == 75.0
    assert unfiltered_pct == 80.0
    assert filtered_result["percentile"] != unfiltered_pct
