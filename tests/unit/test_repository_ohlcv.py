from unittest.mock import MagicMock, patch
from datetime import datetime
from coding.core.database.repository import DatabaseRepository


def _make_repo():
    repo = DatabaseRepository.__new__(DatabaseRepository)
    repo.logger = MagicMock()
    return repo


def test_get_ohlcv_by_date_range_returns_list_of_dicts():
    repo = _make_repo()
    start = datetime(2026, 1, 1)
    end = datetime(2026, 1, 8)

    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = [
        (datetime(2026, 1, 5), 95000.0),
        (datetime(2026, 1, 6), 96000.0),
    ]

    with patch.object(repo, '_db_cursor') as mock_ctx:
        mock_ctx.return_value.__enter__ = lambda s: mock_cursor
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        result = repo.get_ohlcv_by_date_range("BTC", start, end)

    assert len(result) == 2
    assert result[0] == {"date": datetime(2026, 1, 5), "close": 95000.0}
    assert result[1] == {"date": datetime(2026, 1, 6), "close": 96000.0}


def test_get_ohlcv_by_date_range_uses_perpetual_instrument():
    repo = _make_repo()
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = []

    with patch.object(repo, '_db_cursor') as mock_ctx:
        mock_ctx.return_value.__enter__ = lambda s: mock_cursor
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        repo.get_ohlcv_by_date_range("ETH", datetime(2026, 1, 1), datetime(2026, 1, 8))

    call_args = mock_cursor.execute.call_args
    sql = call_args[0][0]
    params = call_args[0][1]
    assert "ETH-PERPETUAL" in params
    assert "ohlcv_history" in sql


def test_get_ohlcv_by_date_range_empty_result():
    repo = _make_repo()
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = []

    with patch.object(repo, '_db_cursor') as mock_ctx:
        mock_ctx.return_value.__enter__ = lambda s: mock_cursor
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        result = repo.get_ohlcv_by_date_range("BTC", datetime(2026, 1, 1), datetime(2026, 1, 8))

    assert result == []


def test_save_ohlcv_inserts_row():
    """save_ohlcv executes INSERT with correct columns."""
    from unittest.mock import MagicMock, patch, call
    from datetime import datetime
    from coding.core.database.repository import DatabaseRepository

    repo = DatabaseRepository.__new__(DatabaseRepository)
    mock_cursor = MagicMock()
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cm.__exit__ = MagicMock(return_value=False)
    repo._db_cursor = MagicMock(return_value=mock_cm)

    ts = 1700000000000
    dt = datetime(2023, 11, 15)
    repo.save_ohlcv("BTC", "BTC-PERPETUAL", ts, dt, 37000.0, 38000.0, 36500.0, 37500.0, 1234.5)

    sql = mock_cursor.execute.call_args[0][0]
    assert "ohlcv_history" in sql
    assert "ON CONFLICT" in sql
    args = mock_cursor.execute.call_args[0][1]
    assert args == ("BTC", "BTC-PERPETUAL", ts, dt, 37000.0, 38000.0, 36500.0, 37500.0, 1234.5)


def test_save_ohlcv_conflict_do_nothing():
    """save_ohlcv uses DO NOTHING on conflict (idempotent)."""
    from unittest.mock import MagicMock
    from datetime import datetime
    from coding.core.database.repository import DatabaseRepository

    repo = DatabaseRepository.__new__(DatabaseRepository)
    mock_cursor = MagicMock()
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cm.__exit__ = MagicMock(return_value=False)
    repo._db_cursor = MagicMock(return_value=mock_cm)

    dt = datetime(2023, 11, 15)
    repo.save_ohlcv("BTC", "BTC-PERPETUAL", 1700000000000, dt, 37000.0, 38000.0, 36500.0, 37500.0, 1234.5)

    sql = mock_cursor.execute.call_args[0][0]
    assert "DO NOTHING" in sql
