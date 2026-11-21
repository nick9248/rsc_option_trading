import re
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import psycopg2
import pytest

from coding.core.database.config import ConnectionPool, DatabaseConfig
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


def test_save_ohlcv_inserts_row_idempotent():
    """save_ohlcv inserts into ohlcv_history; a repeated candle is overwritten (DO UPDATE), never kept as the first nearly empty version."""
    repo = _make_repo()
    ts = 1700000000000
    dt = datetime(2023, 11, 15)
    mock_cursor = MagicMock()

    with patch.object(repo, '_db_cursor') as mock_ctx:
        mock_ctx.return_value.__enter__ = lambda s: mock_cursor
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        repo.save_ohlcv("BTC", "BTC-PERPETUAL", ts, dt, 37000.0, 38000.0, 36500.0, 37500.0, 1234.5)

    sql = mock_cursor.execute.call_args[0][0]
    params = mock_cursor.execute.call_args[0][1]
    assert "ohlcv_history" in sql
    assert "ON CONFLICT" in sql
    assert "DO UPDATE" in sql and "DO NOTHING" not in sql
    for column in ("open", "high", "low", "close", "volume"):
        assert f"{column} = EXCLUDED.{column}" in sql
    assert params == ("BTC", "BTC-PERPETUAL", ts, dt, 37000.0, 38000.0, 36500.0, 37500.0, 1234.5)


@pytest.fixture
def scratch_repository():
    """A repository on a throw-away database holding only ohlcv_history (the table definition is read from the base migration)."""
    base_connection = DatabaseConfig().get_connection_dict()
    if base_connection["host"] not in ("localhost", "127.0.0.1"):
        pytest.skip("scratch databases are only created on a local server")
    try:
        admin = psycopg2.connect(connect_timeout=3, **dict(base_connection, database="postgres"))
    except psycopg2.OperationalError as error:
        pytest.skip(f"local PostgreSQL not reachable: {error}")
    admin.autocommit = True
    name = f"test_ohlcv_{uuid.uuid4().hex[:8]}"
    with admin.cursor() as cursor:
        cursor.execute(f"CREATE DATABASE {name}")
    base_sql = (Path(__file__).parents[2] / "migrations" / "000_base_schema.sql").read_text(encoding="utf-8")
    create_table = re.search(r"CREATE TABLE IF NOT EXISTS ohlcv_history \(.*?\);", base_sql, re.DOTALL).group(0)
    scratch = dict(base_connection, database=name)
    with psycopg2.connect(**scratch) as connection:
        with connection.cursor() as cursor:
            cursor.execute(create_table)
    ConnectionPool().close_all()
    repository = DatabaseRepository(config=DatabaseConfig(host=scratch["host"], port=scratch["port"], database=name, user=scratch["user"],
                                                          password=scratch["password"]))
    try:
        yield repository
    finally:
        ConnectionPool().close_all()
        with admin.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        admin.close()


def test_a_candle_saved_again_replaces_the_early_version_in_a_real_database(scratch_repository):
    """The bug of 2026-03-15 to 2026-11-21: the first, nearly empty version of the running day stayed forever."""
    day = datetime(2026, 11, 20, 8)
    timestamp = 1795075200000
    scratch_repository.save_ohlcv("BTC", "BTC-PERPETUAL", timestamp, day, 61772.5, 61780.0, 61770.0, 61775.0, 12.0)          # 5 minutes into the day
    scratch_repository.save_ohlcv("BTC", "BTC-PERPETUAL", timestamp, day, 61772.5, 62000.0, 57800.0, 57882.0, 42706.0)       # the finished day
    candles = scratch_repository.get_ohlcv_by_date_range("BTC", datetime(2026, 11, 19), datetime(2026, 11, 21))
    assert candles == [{"date": day, "close": 57882.0}]
    with scratch_repository._db_cursor() as cursor:
        cursor.execute("SELECT count(*), max(high), min(low), max(volume) FROM ohlcv_history")
        assert cursor.fetchone() == (1, 62000.0, 57800.0, 42706.0)
