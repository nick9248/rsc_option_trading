"""Tests for the repository methods that store our Deribit account data (migration 029).

They run on a throw-away database created for each test and are skipped when the
local PostgreSQL server is not reachable. The records below have the shape of real
Deribit responses.
"""
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import pytest

from coding.core.database.config import ConnectionPool, DatabaseConfig
from coding.core.database.repository import DatabaseRepository

MIGRATION = Path(__file__).parents[2] / "migrations" / "029_add_deribit_account_tables.sql"
HOUR = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def repo():
    base_connection = DatabaseConfig().get_connection_dict()
    if base_connection["host"] not in ("localhost", "127.0.0.1"):
        pytest.skip("scratch databases are only created on a local server")
    scratch_name = f"test_account_{uuid.uuid4().hex[:8]}"
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
            cursor.execute(MIGRATION.read_text(encoding="utf8"))

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


def _query(repository, sql):
    with psycopg2.connect(**repository._scratch_connection) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql)
            return cursor.fetchall()


TRADE = {
    "timestamp": 1733502627611, "state": "filled", "iv": 68.59, "price": 0.0275, "amount": 1.0,
    "direction": "buy", "index_price": 4011.28, "instrument_name": "ETH-27DEC24-3700-P",
    "mark_price": 0.027176, "order_id": "ETH-52896903623", "trade_id": "ETH-225063318",
    "fee_currency": "ETH", "fee": 0.0003, "order_type": "limit", "liquidity": "T", "profit_loss": 0.0,
}

ORDER = {
    "label": "bot-1", "price": 0.1025, "amount": 1.0, "direction": "buy", "time_in_force": "good_til_cancelled",
    "instrument_name": "ETH-26DEC25-3400-C", "order_id": "ETH-94994168351", "api": False,
    "creation_timestamp": 1762279558901, "last_update_timestamp": 1762279558901, "filled_amount": 0.0,
    "average_price": 0.0, "order_state": "open", "order_type": "limit", "post_only": False, "reduce_only": False,
}


class TestAccountSnapshot:
    SUMMARY = {
        "equity": 1.5, "balance": 1.4, "available_funds": 1.0, "margin_balance": 1.5, "initial_margin": 0.2,
        "maintenance_margin": 0.1, "session_upl": 0.01, "session_rpl": 0.02, "total_pl": 0.3,
        "options_delta": -0.5, "options_gamma": 0.001, "options_vega": 2.0, "options_theta": -0.4,
        "margin_model": "segregated_sm", "email": "private@example.com", "username": "someone",
    }

    def test_values_are_stored_once_per_hour_and_personal_data_is_not(self, repo):
        assert repo.save_account_snapshot("ETH", HOUR, self.SUMMARY) == 1
        assert repo.save_account_snapshot("ETH", HOUR, dict(self.SUMMARY, equity=9.9)) == 0

        assert _query(repo, "SELECT equity::float, options_vega::float, margin_model FROM account_snapshots") == [
            (1.5, 2.0, "segregated_sm")]
        columns = [row[0] for row in _query(
            repo, "SELECT column_name FROM information_schema.columns WHERE table_name = 'account_snapshots'")]
        assert "email" not in columns and "raw" not in columns

    def test_a_missing_field_is_stored_as_null_not_zero(self, repo):
        repo.save_account_snapshot("BTC", HOUR, {"equity": 0.5})

        assert _query(repo, "SELECT options_vega, delta_total FROM account_snapshots") == [(None, None)]


class TestPositionSnapshots:
    POSITION = {
        "instrument_name": "ETH-26DEC25-3400-C", "kind": "option", "direction": "buy", "size": 1.0,
        "average_price": 0.1025, "mark_price": 0.09, "index_price": 3000.0, "delta": 0.3, "gamma": 0.001,
        "vega": 1.2, "theta": -0.5, "floating_profit_loss": -0.0125,
    }

    def test_positions_are_stored_once_per_hour(self, repo):
        assert repo.save_position_snapshots("ETH", HOUR, [self.POSITION]) == 1
        assert repo.save_position_snapshots("ETH", HOUR, [self.POSITION]) == 0

        assert _query(repo, "SELECT instrument_name, size::float, delta::float FROM position_snapshots") == [
            ("ETH-26DEC25-3400-C", 1.0, 0.3)]

    def test_no_positions_means_nothing_stored(self, repo):
        assert repo.save_position_snapshots("ETH", HOUR, []) == 0


class TestUserTrades:
    def test_fill_is_stored_with_utc_time_and_never_stored_twice(self, repo):
        assert repo.get_latest_user_trade_ms("ETH") is None
        assert repo.save_user_trades("ETH", [TRADE]) == 1
        assert repo.save_user_trades("ETH", [TRADE]) == 0

        # 1733502627611 ms = 2024-12-06 16:30:27.611 UTC
        assert _query(repo, "SELECT trade_time AT TIME ZONE 'UTC', price::float, fee::float, label FROM user_trades") == [
            (datetime(2024, 12, 6, 16, 30, 27, 611000), 0.0275, 0.0003, None)]
        assert repo.get_latest_user_trade_ms("ETH") == 1733502627611
        assert repo.get_latest_user_trade_ms("BTC") is None

    def test_label_is_kept(self, repo):
        repo.save_user_trades("ETH", [dict(TRADE, label="bot-1")])

        assert _query(repo, "SELECT label FROM user_trades") == [("bot-1",)]


class TestOrders:
    def test_order_is_updated_when_deribit_reports_a_newer_state(self, repo):
        assert repo.save_orders("ETH", [ORDER]) == 1
        filled = dict(ORDER, order_state="filled", filled_amount=1.0, last_update_timestamp=1762279999999)
        assert repo.save_orders("ETH", [filled]) == 1

        assert _query(repo, "SELECT order_state, filled_amount::float, label FROM orders") == [("filled", 1.0, "bot-1")]

    def test_an_older_or_identical_report_changes_nothing(self, repo):
        repo.save_orders("ETH", [dict(ORDER, order_state="filled", last_update_timestamp=1762279999999)])

        assert repo.save_orders("ETH", [ORDER]) == 0
        assert repo.save_orders("ETH", [dict(ORDER, order_state="filled", last_update_timestamp=1762279999999)]) == 0
        assert _query(repo, "SELECT order_state FROM orders") == [("filled",)]

    def test_trigger_order_with_a_text_price_is_stored_with_a_null_price(self, repo):
        repo.save_orders("ETH", [dict(ORDER, price="market_price")])

        assert _query(repo, "SELECT price FROM orders") == [(None,)]


class TestSettlements:
    DELIVERY = {
        "position": 1.0, "timestamp": 1766736000041, "type": "delivery", "index_price": 2965.16,
        "instrument_name": "ETH-26DEC25-3400-C", "mark_price": 0.0, "profit_loss": -0.1025,
        "session_profit_loss": 1041.5,
    }

    def test_event_is_stored_once(self, repo):
        assert repo.save_settlements("ETH", [self.DELIVERY]) == 1
        assert repo.save_settlements("ETH", [self.DELIVERY]) == 0

        assert _query(repo, "SELECT instrument_name, profit_loss::float, type FROM settlements") == [
            ("ETH-26DEC25-3400-C", -0.1025, "delivery")]

    def test_event_without_an_instrument_is_stored(self, repo):
        bankruptcy = {"type": "bankruptcy", "timestamp": 1766736000041, "funded": 1.0}

        assert repo.save_settlements("ETH", [bankruptcy]) == 1
