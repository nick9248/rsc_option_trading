"""Tests for DatabaseRepository.save_health_check_run (migration 030), on a throw-away database."""
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg2
import pytest

from coding.core.database.config import ConnectionPool, DatabaseConfig
from coding.core.database.repository import DatabaseRepository

MIGRATION = Path(__file__).parents[2] / "migrations" / "030_add_health_check_runs.sql"
RUN_AT = datetime(2026, 10, 12, 20, 0, tzinfo=timezone.utc)


@pytest.fixture
def repo():
    base_connection = DatabaseConfig().get_connection_dict()
    if base_connection["host"] not in ("localhost", "127.0.0.1"):
        pytest.skip("scratch databases are only created on a local server")
    scratch_name = f"test_health_runs_{uuid.uuid4().hex[:8]}"
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


def _rows(repository):
    with psycopg2.connect(**repository._scratch_connection) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT run_at, total, passed, problems, results, tables FROM health_check_runs ORDER BY run_at")
            return cursor.fetchall()


def _save(repository, run_at, **overrides):
    run = dict(
        run_at=run_at, total=2, passed=1, problems=["not running"],
        results=[{"ok": True, "label": "API", "message": "ok"}, {"ok": False, "label": "Daemon", "message": "not running"}],
        tables={"historical_trades": {"rows": 42}},
    )
    run.update(overrides)
    repository.save_health_check_run(**run)


def test_a_run_is_stored_with_all_its_details(repo):
    _save(repo, RUN_AT)

    ((run_at, total, passed, problems, results, tables),) = _rows(repo)
    assert run_at == RUN_AT
    assert (total, passed, problems) == (2, 1, ["not running"])
    assert results[1] == {"ok": False, "label": "Daemon", "message": "not running"}
    assert tables == {"historical_trades": {"rows": 42}}


def test_runs_older_than_the_retention_are_removed_and_newer_ones_kept(repo):
    _save(repo, RUN_AT - timedelta(days=91))
    _save(repo, RUN_AT - timedelta(days=89))

    _save(repo, RUN_AT)

    assert [row[0] for row in _rows(repo)] == [RUN_AT - timedelta(days=89), RUN_AT]
