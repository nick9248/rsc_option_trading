"""strategy_suggestions: the migration file creates the table, rows are added with their cost summed. Throw-away database (never option_trading)."""

import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import pytest

from coding.core.database.config import ConnectionPool, DatabaseConfig
from coding.core.database.repository import DatabaseRepository

MIGRATIONS = [Path(__file__).parents[2] / "migrations" / name for name in ("038_add_strategy_suggestions.sql", "039_strategy_suggestions_facts_as_of.sql",
                                                                           "040_add_strategy_picks.sql", "041_strategy_picks_method_version.sql",
                                                                           "042_strategy_picks_method_version_no_default.sql")]


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


def test_the_newest_suggestion_of_each_expiry_is_read_with_its_times_and_types(repo):
    from datetime import timedelta
    older = row()["computed_at"]
    repo.save_strategy_suggestion(row(computed_at=older, response="old"))
    repo.save_strategy_suggestion(row(computed_at=older + timedelta(hours=1), response="new", net_premium_usd=None))
    repo.save_strategy_suggestion(row(expiration="25DEC26", response="other"))
    repo.save_strategy_suggestion(row(currency="ETH", response="eth"))

    rows = {r["expiration"]: r for r in repo.get_latest_strategy_suggestions("BTC")}

    assert set(rows) == {"27NOV26", "25DEC26"} and rows["27NOV26"]["response"] == "new"
    assert rows["27NOV26"]["net_premium_usd"] is None and rows["25DEC26"]["net_premium_usd"] == -581.0
    assert rows["27NOV26"]["computed_at"].tzinfo is not None and rows["27NOV26"]["facts_as_of"].tzinfo is not None
    assert isinstance(rows["27NOV26"]["reference_price"], float)


def pick_row(**overrides):
    return {"currency": "BTC", "expiration": "27NOV26", "mode": "replay", "method_version": "pipeline-2",
            "as_of": datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc), "computed_at": datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc), "spot": 58000.0, "model": "claude-opus-5-5", "status": "ok",
            "facts": "facts", "candidates": [{"id": "C1", "legs": [], "max_loss_usd": 100.0}], "choice": "C1", "ranking": ["C1"], "response": "{}",
            "quantity": 0.5, "max_loss_usd": 50.0, "risk_per_trade_usd": 500.0, "input_tokens": 3500, "output_tokens": 3000, "cache_read_tokens": 0,
            "cache_write_tokens": 0, "cost_usd": 0.08, "request_id": "req_1", "baseline_id": "C1", **overrides}


def test_a_pick_is_stored_once_per_instant_read_back_and_evaluated_once(repo):
    first = repo.save_strategy_pick(pick_row())
    assert first is not None
    assert repo.save_strategy_pick(pick_row(cost_usd=9.0)) is None                      # the same instant: nothing changes, nothing is paid for twice
    assert repo.save_strategy_pick(pick_row(mode="live")) is not None                   # another mode is another row
    assert repo.save_strategy_pick(pick_row(method_version="pipeline-3")) is not None  # another method version is another row
    assert len(repo.get_strategy_picks("BTC", method_version="pipeline-3")) == 1 and {row["method_version"] for row in repo.get_strategy_picks("BTC", mode="replay")} == {"pipeline-2", "pipeline-3"}
    later = pick_row(mode="live", as_of=datetime(2026, 11, 20, 9, 30, tzinfo=timezone.utc))
    assert repo.save_strategy_pick(later) is not None
    assert len(repo.get_strategy_picks("BTC", mode="live", as_of_since=datetime(2026, 11, 20, 9, 15, tzinfo=timezone.utc))) == 1

    rows = repo.get_strategy_picks("BTC", mode="replay", method_version="pipeline-2")
    assert len(rows) == 1 and rows[0]["candidates"][0]["id"] == "C1" and rows[0]["ranking"] == ["C1"]
    assert rows[0]["as_of"].tzinfo is not None and isinstance(rows[0]["spot"], float) and rows[0]["evaluated_at"] is None
    assert repo.get_strategy_picks("BTC", evaluated=True) == [] and len(repo.get_strategy_picks("BTC", evaluated=False)) == 4
    assert len(repo.get_strategy_picks("BTC", expiration="27NOV26", as_of=pick_row()["as_of"], mode="replay", method_version="pipeline-2")) == 1

    evaluated_at = datetime(2026, 11, 27, 9, tzinfo=timezone.utc)
    assert repo.save_strategy_pick_evaluation(first, evaluated_at, 59000.0, "C1", {"C1": {"pnl_usd": 10.0, "r": 0.1}}) is True
    assert repo.save_strategy_pick_evaluation(first, evaluated_at, 1.0, "C9", {"x": 1}) is False      # a second evaluation changes nothing
    done = repo.get_strategy_picks("BTC", mode="replay", evaluated=True, method_version="pipeline-2")[0]
    assert done["settlement_price"] == 59000.0 and done["outcomes"]["C1"]["r"] == 0.1 and done["baseline_id"] == "C1"


def test_the_total_model_spend_adds_suggestions_and_picks(repo):
    repo.save_strategy_suggestion(row(cost_usd=0.06))
    repo.save_strategy_pick(pick_row(cost_usd=0.08))
    assert repo.get_total_model_spend() == pytest.approx(0.14)


def test_a_pick_with_a_naive_time_or_nan_is_refused(repo):
    with pytest.raises(ValueError):
        repo.save_strategy_pick(pick_row(as_of=datetime(2026, 11, 20, 9)))
    with pytest.raises(ValueError):
        repo.save_strategy_pick(pick_row(candidates=[{"x": float("nan")}]))


def test_a_pick_without_a_method_version_is_refused_by_the_database(repo):
    row_without_version = {key: value for key, value in pick_row().items() if key != "method_version"}
    with pytest.raises(KeyError):                                                      # the writer needs the key
        repo.save_strategy_pick(row_without_version)
