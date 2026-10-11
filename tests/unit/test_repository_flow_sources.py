"""Tests for the repository reads behind the Flow Charts and the futures-basis write.

They run on a throw-away database created for each test (see
test_repository_defined_risk_scan_history.py) and are skipped when the local
PostgreSQL server is not reachable. Only the columns these methods use are created.
"""
import uuid
from datetime import datetime, timedelta, timezone

import psycopg2
import pytest

from coding.core.database.config import ConnectionPool, DatabaseConfig
from coding.core.database.repository import DatabaseRepository

SCHEMA = """
CREATE TABLE historical_trades (
    id serial PRIMARY KEY, currency text, expiration text, strike numeric,
    direction text, trade_timestamp bigint, index_price numeric
);
CREATE TABLE onchain_analysis_snapshots (
    id serial PRIMARY KEY, currency text, expiration text, snapshot_hour timestamp,
    total_call_oi numeric, total_put_oi numeric, index_price numeric
);
CREATE TABLE futures_basis (
    id serial PRIMARY KEY, timestamp timestamp NOT NULL, currency varchar(10) NOT NULL,
    futures_instrument varchar(50) NOT NULL, futures_price numeric NOT NULL,
    spot_price numeric NOT NULL, basis_absolute numeric, basis_percentage numeric,
    implied_repo_rate numeric, days_to_expiry numeric,
    CONSTRAINT unique_futures_basis UNIQUE (futures_instrument, timestamp)
);
"""


@pytest.fixture
def repo():
    base_connection = DatabaseConfig().get_connection_dict()
    if base_connection["host"] not in ("localhost", "127.0.0.1"):
        pytest.skip("scratch databases are only created on a local server")
    scratch_name = f"test_flow_sources_{uuid.uuid4().hex[:8]}"
    try:
        admin = psycopg2.connect(connect_timeout=3, **dict(base_connection, database="postgres"))
    except psycopg2.OperationalError as error:
        pytest.skip(f"local PostgreSQL not reachable: {error}")
    admin.autocommit = True
    with admin.cursor() as cursor:
        cursor.execute(f"CREATE DATABASE {scratch_name}")
    scratch_connection = dict(base_connection, database=scratch_name)
    with psycopg2.connect(**scratch_connection) as connection:
        with connection.cursor() as cursor:
            cursor.execute(SCHEMA)

    ConnectionPool().close_all()
    repository = DatabaseRepository(config=DatabaseConfig(
        host=scratch_connection["host"], port=scratch_connection["port"],
        database=scratch_name, user=scratch_connection["user"],
        password=scratch_connection["password"],
    ))
    repository._scratch_connection = scratch_connection
    try:
        yield repository
    finally:
        ConnectionPool().close_all()
        with admin.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS {scratch_name} WITH (FORCE)")
        admin.close()


def _execute(repository, sql, params=()):
    with psycopg2.connect(**repository._scratch_connection) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, params)


def _query(repository, sql):
    with psycopg2.connect(**repository._scratch_connection) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql)
            return cursor.fetchall()


def _trade(repository, expiration, timestamp_ms, currency="BTC", direction="buy", index_price=None):
    _execute(
        repository,
        "INSERT INTO historical_trades (currency, expiration, strike, direction, trade_timestamp, index_price) "
        "VALUES (%s, %s, 60000, %s, %s, %s)",
        (currency, expiration, direction, timestamp_ms, index_price),
    )


def _snapshot(repository, expiration, hour, call_oi, put_oi, index_price, currency="BTC"):
    _execute(
        repository,
        "INSERT INTO onchain_analysis_snapshots "
        "(currency, expiration, snapshot_hour, total_call_oi, total_put_oi, index_price) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (currency, expiration, hour, call_oi, put_oi, index_price),
    )


def _ms(moment):
    return int(moment.timestamp() * 1000)


def _expiration_in(days):
    """Expiration label in Deribit style, e.g. 5NOV26, for a date `days` from now."""
    moment = datetime.now(timezone.utc) + timedelta(days=days)
    return f"{moment.day}{moment.strftime('%b%y').upper()}"


class TestActiveExpirationsWithRecentTrades:
    def test_lists_unexpired_expirations_traded_since_the_cutoff_ordered_by_open_interest(self, repo):
        now = datetime.now(timezone.utc)
        near, far = _expiration_in(30), _expiration_in(60)
        for expiration in (near, far):
            _trade(repo, expiration, _ms(now - timedelta(hours=1)))
        _snapshot(repo, near, now.replace(tzinfo=None), 10, 5, 65000)    # total OI 15
        _snapshot(repo, far, now.replace(tzinfo=None), 100, 50, 65000)   # total OI 150

        result = repo.get_active_expirations_with_recent_trades("BTC", _ms(now - timedelta(hours=24)))

        assert result == [{"expiration": far, "total_oi": 150.0}, {"expiration": near, "total_oi": 15.0}]

    def test_excludes_expired_old_trades_other_currencies_and_trades_without_direction(self, repo):
        now = datetime.now(timezone.utc)
        future = _expiration_in(30)
        recent = _ms(now - timedelta(hours=1))
        _trade(repo, "1JAN20", recent)                                # already expired
        _trade(repo, future, _ms(now - timedelta(hours=48)))          # older than the cutoff
        _trade(repo, future, recent, currency="ETH")                  # other currency
        _trade(repo, future, recent, direction=None)                  # taker side unknown

        assert repo.get_active_expirations_with_recent_trades("BTC", _ms(now - timedelta(hours=24))) == []

    def test_unknown_open_interest_is_zero_and_expirations_are_not_duplicated(self, repo):
        now = datetime.now(timezone.utc)
        future = _expiration_in(30)
        for offset in (1, 2, 3):
            _trade(repo, future, _ms(now - timedelta(hours=offset)))

        assert repo.get_active_expirations_with_recent_trades("BTC", _ms(now - timedelta(hours=24))) == [
            {"expiration": future, "total_oi": 0.0}
        ]


class TestLatestIndexPrice:
    def test_prefers_the_newest_snapshot(self, repo):
        now = datetime(2026, 10, 1, 12, 0)
        _snapshot(repo, "31DEC26", now - timedelta(hours=1), 1, 1, 64000)
        _snapshot(repo, "31DEC26", now, 1, 1, 65000)
        _trade(repo, "31DEC26", 1, index_price=1)

        assert repo.get_latest_index_price("BTC") == 65000.0

    def test_falls_back_to_the_newest_trade_then_to_zero(self, repo):
        assert repo.get_latest_index_price("BTC") == 0.0
        _trade(repo, "31DEC26", 1000, index_price=61000)
        _trade(repo, "31DEC26", 2000, index_price=62000)

        assert repo.get_latest_index_price("BTC") == 62000.0
        assert repo.get_latest_index_price("ETH") == 0.0


class TestSaveFuturesBasis:
    def _row(self, **overrides):
        row = {
            "timestamp": datetime(2026, 10, 1, 0, 0), "currency": "BTC",
            "futures_instrument": "BTC-31DEC26", "futures_price": 70000.0, "spot_price": 68000.0,
            "basis_absolute": 2000.0, "basis_percentage": 2.9412, "implied_repo_rate": 11.7547,
            "days_to_expiry": 91.33,
        }
        row.update(overrides)
        return row

    def test_rows_are_stored_and_an_hour_already_stored_is_left_untouched(self, repo):
        assert repo.save_futures_basis([self._row()]) == 1
        assert repo.save_futures_basis([self._row(futures_price=99999.0)]) == 0

        stored = _query(repo, "SELECT futures_price::float, implied_repo_rate::float FROM futures_basis")
        assert stored == [(70000.0, 11.7547)]

    def test_short_tenor_is_stored_with_a_null_annualized_rate(self, repo):
        assert repo.save_futures_basis([self._row(implied_repo_rate=None)]) == 1

        assert _query(repo, "SELECT implied_repo_rate FROM futures_basis") == [(None,)]

    def test_no_rows_means_nothing_stored(self, repo):
        assert repo.save_futures_basis([]) == 0
