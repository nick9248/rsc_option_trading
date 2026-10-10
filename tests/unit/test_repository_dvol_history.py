"""Tests for DatabaseRepository.save_dvol_candles.

dvol_history is the single DVOL table (one row per asset and hour, final close of
the hourly candle). The SQL is declared in the Core-layer repository on purpose:
Core must not import the Service-layer DVOLFetcher.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from coding.core.database.repository import DatabaseRepository


def _make_repo():
    repo = DatabaseRepository.__new__(DatabaseRepository)
    repo.logger = MagicMock()
    return repo


def _candles():
    return [
        (datetime(2026, 10, 10, 20, 0, tzinfo=timezone.utc), 36.9),
        (datetime(2026, 10, 10, 21, 0, tzinfo=timezone.utc), 36.76),
    ]


def _save(repo, asset, candles, returned_rows):
    mock_cursor = MagicMock()
    with patch.object(repo, "_db_cursor") as mock_ctx, \
            patch("coding.core.database.repository.execute_values", return_value=returned_rows) as mock_execute_values:
        mock_ctx.return_value.__enter__ = lambda s: mock_cursor
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        result = repo.save_dvol_candles(asset, candles)
    return result, mock_execute_values


def test_candles_are_upserted_and_only_changed_rows_are_counted():
    repo = _make_repo()

    result, mock_execute_values = _save(repo, "BTC", _candles(), returned_rows=[(1,)])

    assert result == 1
    sql = mock_execute_values.call_args.args[1]
    rows = mock_execute_values.call_args.args[2]
    assert "INSERT INTO dvol_history AS t (asset, timestamp, dvol_value)" in sql
    assert "ON CONFLICT (asset, timestamp) DO UPDATE SET dvol_value = EXCLUDED.dvol_value" in sql
    assert "WHERE t.dvol_value IS DISTINCT FROM EXCLUDED.dvol_value" in sql
    assert sql.strip().endswith("RETURNING 1")
    assert rows == [("BTC", ts, value) for ts, value in _candles()]
    assert mock_execute_values.call_args.kwargs == {"fetch": True}


def test_unchanged_candles_return_zero():
    repo = _make_repo()

    result, _ = _save(repo, "ETH", _candles(), returned_rows=[])

    assert result == 0


def test_no_candles_means_no_database_call():
    repo = _make_repo()

    result, mock_execute_values = _save(repo, "BTC", [], returned_rows=[])

    assert result == 0
    mock_execute_values.assert_not_called()


def test_the_candle_still_forming_is_never_stored():
    repo = _make_repo()
    current_hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    completed = (datetime(2026, 10, 10, 21, 0, tzinfo=timezone.utc), 36.76)
    forming = (current_hour, 40.0)

    result, mock_execute_values = _save(repo, "BTC", [completed, forming], returned_rows=[(1,)])

    stored_rows = mock_execute_values.call_args.args[2]
    assert stored_rows == [("BTC", completed[0], completed[1])]
    assert result == 1


def test_only_a_forming_candle_means_no_database_call():
    repo = _make_repo()
    current_hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

    result, mock_execute_values = _save(repo, "BTC", [(current_hour, 40.0)], returned_rows=[])

    assert result == 0
    mock_execute_values.assert_not_called()
