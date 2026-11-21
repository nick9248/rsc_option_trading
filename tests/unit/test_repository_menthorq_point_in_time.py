"""Point-in-time tests for the two read methods the MenthorQ replay uses:
DatabaseRepository.get_index_price_at_or_before and
DatabaseRepository.get_option_chain_snapshot_at_or_before.

Both must return the newest row at or before ``as_of`` and ignore any row
after it (no lookahead). These tests write rows, so they run on a throw-away
database (never option_trading). They are skipped when the local PostgreSQL
server is not reachable.
"""
import uuid
from datetime import datetime, timedelta, timezone

import psycopg2
import pytest

from coding.core.database.config import ConnectionPool, DatabaseConfig
from coding.core.database.repository import DatabaseRepository

# The columns the two queries read, with the types of the real tables
# (migrations/000_base_schema.sql and later).
CREATE_TABLES = """
CREATE TABLE snapshots (
    id               SERIAL PRIMARY KEY,
    captured_at      TIMESTAMP      NOT NULL,
    currency         VARCHAR(10)    NOT NULL,
    instrument_name  VARCHAR(100)   NOT NULL,
    expiration       VARCHAR(20),
    strike           DECIMAL(12,2),
    option_type      CHAR(1),
    open_interest    DECIMAL(18,8),
    underlying_price DECIMAL(18,8),
    mark_price       DECIMAL(18,8),
    mark_iv          DECIMAL(10,4),
    volume           DECIMAL(18,8),
    volume_usd       DECIMAL(18,2),
    bid_price        DECIMAL(18,8),
    ask_price        DECIMAL(18,8)
);
CREATE TABLE onchain_analysis_snapshots (
    id serial PRIMARY KEY, snapshot_hour timestamp NOT NULL, currency varchar(10) NOT NULL, expiration varchar(20) NOT NULL,
    max_pain_strike numeric, max_pain_distance_pct numeric, put_call_ratio_oi numeric, put_call_ratio_volume numeric, total_call_oi numeric,
    total_put_oi numeric, total_net_gex numeric, total_net_dex numeric, call_resistance_strike numeric, put_support_strike numeric,
    hvl_level numeric, resistance_1_strike numeric, resistance_1_call_oi numeric, support_1_strike numeric, support_1_put_oi numeric,
    total_volume numeric, itm_call_oi_pct numeric, otm_call_oi_pct numeric, itm_put_oi_pct numeric, otm_put_oi_pct numeric,
    underlying_price numeric, created_at timestamp, index_price numeric, forward_price numeric);
CREATE TABLE onchain_volatility_snapshots (
    id serial PRIMARY KEY, snapshot_hour timestamp NOT NULL, currency varchar(10) NOT NULL, expiration varchar(20) NOT NULL, atm_iv numeric,
    skew_25d numeric, put_25d_iv numeric, call_25d_iv numeric, net_vanna numeric, net_charm numeric, vwap_iv numeric, vrp_absolute numeric,
    vrp_percentage numeric, realized_vol numeric, iv_percentile_expiry numeric, iv_percentile_365d numeric, iv_rank_365d numeric,
    expected_daily_move numeric, expected_weekly_move numeric, expected_monthly_move numeric, pc_atm_ratio numeric, pc_near_otm_ratio numeric,
    pc_far_otm_ratio numeric, underlying_price numeric);
CREATE TABLE flow_delta_hourly (
    id serial PRIMARY KEY, snapshot_hour timestamp NOT NULL, currency varchar(10) NOT NULL, expiration varchar(20) NOT NULL, hiro_usd numeric,
    premium_usd numeric, gross_delta_usd numeric, net_contracts numeric, gross_contracts numeric, trade_count integer);
CREATE TABLE dvol_history (id serial PRIMARY KEY, asset varchar(10) NOT NULL, timestamp timestamptz NOT NULL, dvol_value numeric);   -- timestamptz, as in the real table
CREATE TABLE historical_trades (
    id               SERIAL PRIMARY KEY,
    trade_id         VARCHAR(50)    NOT NULL,
    trade_timestamp  BIGINT         NOT NULL,
    currency         VARCHAR(10)    NOT NULL,
    index_price      DECIMAL(18,8)
);
"""

AS_OF = datetime(2026, 11, 6, 20, 59, tzinfo=timezone.utc)


@pytest.fixture
def repo():
    base_connection = DatabaseConfig().get_connection_dict()
    if base_connection["host"] not in ("localhost", "127.0.0.1"):
        pytest.skip("scratch databases are only created on a local server")
    scratch_name = f"test_menthorq_pit_{uuid.uuid4().hex[:8]}"

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
            cursor.execute(CREATE_TABLES)

    ConnectionPool().close_all()
    repository = DatabaseRepository(config=DatabaseConfig(
        host=scratch_connection["host"], port=scratch_connection["port"],
        database=scratch_name, user=scratch_connection["user"],
        password=scratch_connection["password"],
    ))
    try:
        yield repository, scratch_connection
    finally:
        ConnectionPool().close_all()
        with admin.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS {scratch_name} WITH (FORCE)")
        admin.close()


def _execute(scratch_connection, sql, rows):
    with psycopg2.connect(**scratch_connection) as connection:
        with connection.cursor() as cursor:
            cursor.executemany(sql, rows)


def _insert_trades(scratch_connection, rows):
    """rows: (trade_id, aware UTC trade time, currency, index_price)."""
    _execute(
        scratch_connection,
        "INSERT INTO historical_trades (trade_id, trade_timestamp, currency, index_price) "
        "VALUES (%s, %s, %s, %s)",
        [(trade_id, int(time.timestamp() * 1000), currency, price)
         for trade_id, time, currency, price in rows],
    )


def _insert_snapshot_rows(scratch_connection, rows):
    """rows: (aware UTC capture time, instrument, open_interest, mark_iv, underlying_price)."""
    _execute(
        scratch_connection,
        "INSERT INTO snapshots (captured_at, currency, instrument_name, expiration, strike, "
        "option_type, open_interest, mark_iv, underlying_price) "
        "VALUES (%s, 'BTC', %s, '27NOV26', 80000, 'C', %s, %s, %s)",
        [(captured.astimezone(timezone.utc).replace(tzinfo=None), instrument, open_interest,
          mark_iv, underlying)
         for captured, instrument, open_interest, mark_iv, underlying in rows],
    )


def test_index_price_ignores_a_trade_after_as_of(repo):
    repository, scratch_connection = repo
    _insert_trades(scratch_connection, [
        ("older", AS_OF - timedelta(minutes=5), "BTC", 75000.0),
        ("newest_before", AS_OF - timedelta(seconds=7), "BTC", 75822.72),
        ("after", AS_OF + timedelta(seconds=1), "BTC", 99999.0),
        ("other_currency", AS_OF - timedelta(seconds=1), "ETH", 2500.0),
    ])

    price, trade_time = repository.get_index_price_at_or_before("BTC", AS_OF)

    assert price == 75822.72
    assert trade_time == AS_OF - timedelta(seconds=7)
    assert trade_time.tzinfo is not None


def test_index_price_includes_a_trade_exactly_at_as_of(repo):
    repository, scratch_connection = repo
    _insert_trades(scratch_connection, [("at", AS_OF, "BTC", 76000.0)])

    assert repository.get_index_price_at_or_before("BTC", AS_OF) == (76000.0, AS_OF)


def test_index_price_is_none_when_only_later_trades_exist(repo):
    repository, scratch_connection = repo
    _insert_trades(scratch_connection, [("after", AS_OF + timedelta(minutes=1), "BTC", 99999.0)])

    assert repository.get_index_price_at_or_before("BTC", AS_OF) is None


def test_earliest_capture_is_the_oldest_of_the_currency_or_none(repo):
    repository, scratch_connection = repo
    assert repository.get_earliest_option_chain_capture("BTC") is None
    _insert_snapshot_rows(scratch_connection, [
        (AS_OF - timedelta(minutes=10), "BTC-27NOV26-80000-C", 200.0, 46.5, 76100.0),
        (AS_OF - timedelta(minutes=40), "BTC-27NOV26-80000-C", 100.0, 45.0, 76000.0),
    ])
    earliest = repository.get_earliest_option_chain_capture("BTC")
    assert earliest == AS_OF - timedelta(minutes=40) and earliest is not None and earliest.tzinfo is not None
    assert repository.get_earliest_option_chain_capture("ETH") is None


def test_snapshot_ignores_a_capture_after_as_of(repo):
    repository, scratch_connection = repo
    before = AS_OF - timedelta(minutes=10)
    _insert_snapshot_rows(scratch_connection, [
        (AS_OF - timedelta(minutes=40), "BTC-27NOV26-80000-C", 100.0, 45.0, 76000.0),
        (before, "BTC-27NOV26-80000-C", 200.0, 46.5, 76100.0),
        (AS_OF + timedelta(minutes=20), "BTC-27NOV26-80000-C", 999.0, 99.0, 99999.0),
    ])

    captured_at, rows = repository.get_option_chain_snapshot_at_or_before("BTC", AS_OF)

    assert captured_at == before
    assert captured_at.tzinfo is not None
    assert len(rows) == 1
    assert rows[0]["open_interest"] == 200.0
    assert rows[0]["mark_iv"] == 46.5
    assert rows[0]["underlying_price"] == 76100.0


def test_snapshot_keeps_missing_numbers_as_none(repo):
    repository, scratch_connection = repo
    _insert_snapshot_rows(scratch_connection, [
        (AS_OF - timedelta(minutes=1), "BTC-27NOV26-80000-C", None, None, None),
    ])

    _, rows = repository.get_option_chain_snapshot_at_or_before("BTC", AS_OF)

    assert rows[0]["open_interest"] is None
    assert rows[0]["mark_iv"] is None
    assert rows[0]["underlying_price"] is None
    assert rows[0]["mark_price"] is None


def test_snapshot_is_none_when_only_later_captures_exist(repo):
    repository, scratch_connection = repo
    _insert_snapshot_rows(scratch_connection, [
        (AS_OF + timedelta(seconds=1), "BTC-27NOV26-80000-C", 999.0, 99.0, 99999.0),
    ])

    assert repository.get_option_chain_snapshot_at_or_before("BTC", AS_OF) is None


def test_latest_capture_is_the_newest_at_or_before_as_of_without_loading_rows(repo):
    repository, scratch_connection = repo
    assert repository.get_latest_option_chain_capture_at_or_before("BTC", AS_OF) is None
    _insert_snapshot_rows(scratch_connection, [
        (AS_OF - timedelta(minutes=40), "BTC-27NOV26-80000-C", 100.0, 45.0, 76000.0),
        (AS_OF, "BTC-27NOV26-80000-C", 200.0, 46.5, 76100.0),                # exactly at as_of: counts
        (AS_OF + timedelta(minutes=20), "BTC-27NOV26-80000-C", 999.0, 99.0, 99999.0),
    ])

    latest = repository.get_latest_option_chain_capture_at_or_before("BTC", AS_OF)

    assert latest == AS_OF and latest.tzinfo is not None
    assert repository.get_latest_option_chain_capture_at_or_before("BTC", AS_OF - timedelta(minutes=1)) == \
        AS_OF - timedelta(minutes=40)
    assert repository.get_latest_option_chain_capture_at_or_before("ETH", AS_OF) is None


def test_settlement_price_is_the_mean_index_of_the_trades_in_the_last_30_minutes(repo):
    repository, scratch_connection = repo
    settlement = datetime(2026, 11, 6, 8, tzinfo=timezone.utc)
    _insert_trades(scratch_connection, [
        ("a", settlement - timedelta(minutes=31), "BTC", 1000.0),          # before the window
        ("b", settlement - timedelta(minutes=20), "BTC", 100.0),
        ("c", settlement - timedelta(minutes=1), "BTC", 200.0),
        ("d", settlement, "BTC", 300.0),                                  # the settlement instant itself is inside
        ("e", settlement + timedelta(seconds=1), "BTC", 9000.0),          # after: never looked at
        ("f", settlement - timedelta(minutes=5), "ETH", 5000.0),          # another currency
    ])
    assert repository.get_settlement_index_price("BTC", settlement) == (200.0, 3)
    assert repository.get_settlement_index_price("BTC", settlement - timedelta(days=3)) is None


def test_stored_hourly_analysis_is_read_by_write_time_and_never_from_the_future(repo):
    repository, scratch_connection = repo
    hour = datetime(2026, 11, 6, 8)
    _execute(scratch_connection,
             "INSERT INTO onchain_analysis_snapshots (snapshot_hour, currency, expiration, max_pain_strike, created_at) VALUES (%s, 'BTC', '27NOV26', %s, %s)",
             [(hour, 60000, hour + timedelta(minutes=22)), (hour + timedelta(hours=1), 61000, hour + timedelta(hours=1, minutes=22))])
    before_second_write = datetime(2026, 11, 6, 9, 10, tzinfo=timezone.utc)                  # the second row exists only from 09:22
    assert repository.get_onchain_analysis_snapshot_at_or_before("BTC", "27NOV26", before_second_write)["max_pain_strike"] == 60000.0
    assert repository.get_onchain_analysis_snapshot_at_or_before("BTC", "27NOV26", before_second_write + timedelta(minutes=15))["max_pain_strike"] == 61000.0
    assert repository.get_onchain_analysis_snapshot_at_or_before("BTC", "27NOV26", datetime(2026, 11, 6, 8, 10, tzinfo=timezone.utc)) is None
    assert repository.get_onchain_analysis_snapshot_at_or_before("ETH", "27NOV26", before_second_write) is None
    row = repository.get_onchain_analysis_snapshot_at_or_before("BTC", "27NOV26", before_second_write)
    assert row["snapshot_hour"].tzinfo is not None and isinstance(row["max_pain_strike"], float) and row["total_net_gex"] is None


def test_stored_hourly_volatility_is_known_one_hour_after_its_hour_starts(repo):
    repository, scratch_connection = repo
    _execute(scratch_connection, "INSERT INTO onchain_volatility_snapshots (snapshot_hour, currency, expiration, atm_iv) VALUES (%s, 'BTC', '27NOV26', %s)",
             [(datetime(2026, 11, 6, 8), 50.0), (datetime(2026, 11, 6, 9), 52.0)])
    assert repository.get_onchain_volatility_snapshot_at_or_before("BTC", "27NOV26", datetime(2026, 11, 6, 9, 30, tzinfo=timezone.utc))["atm_iv"] == 50.0
    assert repository.get_onchain_volatility_snapshot_at_or_before("BTC", "27NOV26", datetime(2026, 11, 6, 10, 0, tzinfo=timezone.utc))["atm_iv"] == 52.0
    assert repository.get_onchain_volatility_snapshot_at_or_before("BTC", "27NOV26", datetime(2026, 11, 6, 8, 59, tzinfo=timezone.utc)) is None


def test_flow_window_sums_only_the_24_hours_that_had_ended(repo):
    repository, scratch_connection = repo
    start = datetime(2026, 11, 5, 0)
    _execute(scratch_connection, "INSERT INTO flow_delta_hourly (snapshot_hour, currency, expiration, hiro_usd, premium_usd, gross_delta_usd, trade_count) "
             "VALUES (%s, 'BTC', '27NOV26', 10, 1, 100, 2)", [(start + timedelta(hours=hour),) for hour in range(0, 48)])
    window = repository.get_flow_delta_window("BTC", "27NOV26", datetime(2026, 11, 6, 12, tzinfo=timezone.utc))     # hours 11:00 back to 12:00 the day before
    assert window == {"hours_present": 24, "newest_hour": datetime(2026, 11, 6, 11, tzinfo=timezone.utc), "hiro_usd": 240.0, "premium_usd": 24.0, "gross_delta_usd": 2400.0, "trade_count": 48}
    empty = repository.get_flow_delta_window("ETH", "27NOV26", datetime(2026, 11, 6, 12, tzinfo=timezone.utc))
    assert empty["hours_present"] == 0 and empty["hiro_usd"] is None


def test_the_newest_dvol_candle_must_have_closed_by_the_instant(repo):
    repository, scratch_connection = repo
    hour = datetime(2026, 11, 6, 8, tzinfo=timezone.utc)
    _execute(scratch_connection, "INSERT INTO dvol_history (asset, timestamp, dvol_value) VALUES ('BTC', %s, 50)",
             [(hour + timedelta(hours=offset),) for offset in (0, 1, 2)])
    assert repository.get_latest_dvol_candle_start_before("BTC", hour + timedelta(hours=2)) == hour + timedelta(hours=1)    # the 10:00 candle has not closed
    assert repository.get_latest_dvol_candle_start_before("BTC", hour + timedelta(hours=3)) == hour + timedelta(hours=2)
    assert repository.get_latest_dvol_candle_start_before("BTC", hour + timedelta(minutes=30)) is None
    assert repository.get_latest_dvol_candle_start_before("ETH", hour + timedelta(hours=3)) is None
