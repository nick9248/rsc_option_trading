"""
DatabaseRepository methods of the decision snapshots against the real migration
(migrations/036_add_decision_snapshots.sql) on a THROW-AWAY database (never option_trading).
Skipped when the local PostgreSQL server is not reachable.
"""

import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict

import psycopg2
import pytest

from coding.core.analytics.decision.decision_report import DecisionReport
from coding.core.analytics.decision.evidence_report import build_decision_report
from coding.core.database.config import ConnectionPool, DatabaseConfig
from coding.core.database.repository import DatabaseRepository
from tests.unit.decision.evidence_builders import NOW, SPOT, directional, level

MIGRATION = Path(__file__).parents[3] / "migrations" / "036_add_decision_snapshots.sql"


@pytest.fixture
def repo():
    base_connection = DatabaseConfig().get_connection_dict()
    if base_connection["host"] not in ("localhost", "127.0.0.1"):
        pytest.skip("scratch databases are only created on a local server")
    scratch_name = f"test_decision_{uuid.uuid4().hex[:8]}"
    assert scratch_name != "option_trading"
    admin_connection: Dict[str, Any] = {**base_connection, "database": "postgres"}
    try:
        admin = psycopg2.connect(connect_timeout=3, **admin_connection)
    except psycopg2.OperationalError as error:
        pytest.skip(f"local PostgreSQL not reachable: {error}")
    admin.autocommit = True
    with admin.cursor() as cursor:
        cursor.execute(f"CREATE DATABASE {scratch_name}")

    scratch_connection: Dict[str, Any] = {**base_connection, "database": scratch_name}
    with psycopg2.connect(**scratch_connection) as connection:
        with connection.cursor() as cursor:
            cursor.execute(MIGRATION.read_text(encoding="utf-8"))
            cursor.execute(MIGRATION.read_text(encoding="utf-8"))   # idempotent

    ConnectionPool().close_all()
    repository = DatabaseRepository(config=DatabaseConfig(
        host=scratch_connection["host"], port=int(scratch_connection["port"]), database=scratch_name,
        user=scratch_connection["user"], password=scratch_connection["password"],
    ))
    try:
        yield repository
    finally:
        ConnectionPool().close_all()
        with admin.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS {scratch_name} WITH (FORCE)")
        admin.close()


def report(as_of=NOW):
    items = [level("support", "g1", 95_000, age_hours=1), level("resistance", "g2", 105_000, age_hours=1),
             directional("lean", "g3", "bullish", age_hours=1)]
    return build_decision_report("BTC", as_of, SPOT, items)


def test_a_report_round_trips_exactly_and_is_never_rewritten(repo):
    original = report()
    assert repo.save_decision_snapshot("BTC", NOW, SPOT, original.to_dict()) is True
    changed = report().to_dict() | {"top_scenario": "rewritten"}
    assert repo.save_decision_snapshot("BTC", NOW, 1.0, changed) is False          # same key: kept unchanged
    stored = repo.get_decision_snapshot_at_or_before("BTC", NOW)
    assert stored.computed_at == NOW and stored.computed_at.tzinfo is not None and stored.reference_price == SPOT
    assert DecisionReport.from_dict(stored.report) == original


def test_reads_are_point_in_time_and_per_currency(repo):
    for hours in (0, 1, 2):
        repo.save_decision_snapshot("BTC", NOW + timedelta(hours=hours), SPOT, report(NOW + timedelta(hours=hours)).to_dict())
    repo.save_decision_snapshot("ETH", NOW, 2_000.0, report().to_dict())
    assert repo.get_decision_snapshot_at_or_before("BTC", NOW - timedelta(seconds=1)) is None
    assert repo.get_decision_snapshot_at_or_before("BTC", NOW + timedelta(minutes=90)).computed_at == NOW + timedelta(hours=1)
    between = repo.get_decision_snapshots_between("BTC", NOW, NOW + timedelta(hours=2))
    assert [item.computed_at for item in between] == [NOW, NOW + timedelta(hours=1)]       # end is exclusive
    assert [item.currency for item in repo.get_decision_snapshots_between("ETH", NOW, NOW + timedelta(days=1))] == ["ETH"]


def test_naive_times_and_nan_are_refused_before_writing(repo):
    with pytest.raises(ValueError):
        repo.save_decision_snapshot("BTC", NOW.replace(tzinfo=None), SPOT, {})
    with pytest.raises(ValueError):
        repo.save_decision_snapshot("BTC", NOW, SPOT, {"score": float("nan")})
    assert repo.get_decision_snapshot_at_or_before("BTC", NOW) is None
