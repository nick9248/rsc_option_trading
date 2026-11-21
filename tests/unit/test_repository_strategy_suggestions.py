"""strategy_suggestions: the migration file creates the table, rows are added with their cost summed. Throw-away database (never option_trading)."""

import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import pytest

from coding.core.database.config import ConnectionPool, DatabaseConfig
from coding.core.database.repository import DatabaseRepository

MIGRATIONS = [Path(__file__).parents[2] / "migrations" / name for name in ("038_add_strategy_suggestions.sql", "039_strategy_suggestions_facts_as_of.sql")]


@pytest.fixture
def repo():
    base_connection = DatabaseConfig().get_connection_dict()
    if base_connection["host"] not in ("localhost", "127.0.0.1"):
        pytest.skip("scratch databases are only created on a local server")
    scratch_name = f"test_strategy_{uuid.uuid4().hex[:8]}"
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
            for migration in MIGRATIONS:
                cursor.execute(migration.read_text(encoding="utf-8"))
    ConnectionPool().close_all()
    repository = DatabaseRepository(config=DatabaseConfig(
        host=scratch_connection["host"], port=scratch_connection["port"], database=scratch_name,
        user=scratch_connection["user"], password=scratch_connection["password"]))
    try:
        yield repository
    finally:
        ConnectionPool().close_all()
        with admin.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS {scratch_name} WITH (FORCE)")
        admin.close()


def row(**overrides):
    return {"currency": "BTC", "expiration": "27NOV26", "computed_at": datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc),
            "facts_as_of": datetime(2026, 11, 21, 11, 30, tzinfo=timezone.utc),
            "reference_price": 58000.0, "model": "claude-opus-5-5", "status": "ok", "facts": "facts", "response": "{}",
            "net_premium_usd": -581.0, "input_tokens": 3000, "output_tokens": 3000, "cache_read_tokens": 0,
            "cache_write_tokens": 0, "cost_usd": 0.06, "request_id": "req_1", **overrides}


def test_rows_are_added_and_their_cost_is_summed(repo):
    assert repo.get_strategy_suggestion_total_cost() == 0.0
    first = repo.save_strategy_suggestion(row())
    second = repo.save_strategy_suggestion(row(cost_usd=0.04, net_premium_usd=None))
    assert second > first
    assert repo.get_strategy_suggestion_total_cost() == pytest.approx(0.10)


def test_a_naive_time_is_refused(repo):
    with pytest.raises(ValueError):
        repo.save_strategy_suggestion(row(computed_at=datetime(2026, 11, 21, 12, 0)))
    with pytest.raises(ValueError):
        repo.save_strategy_suggestion(row(facts_as_of=datetime(2026, 11, 21, 11, 30)))
