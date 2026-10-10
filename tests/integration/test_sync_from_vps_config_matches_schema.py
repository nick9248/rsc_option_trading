"""
Integration test: every SYNC_TABLES key matches a real unique constraint.

An ON CONFLICT clause (the conflict_target of incremental tables, the upsert_key
of tables whose rows change) only works if it names exactly the columns of a
unique index on the local table. A wrong key would otherwise only fail at sync
time. Uses the local development database read-only; skipped when it is not
reachable or a table has not been created by the migrations yet.
"""

import re

import psycopg2
import pytest

from scripts.sync_from_vps import LOCAL_CONN, SYNC_TABLES


@pytest.fixture(scope="module")
def unique_index_columns():
    """{table: [set of column names, ...]} for every unique index in the local database."""
    try:
        connection = psycopg2.connect(connect_timeout=3, **LOCAL_CONN)
    except psycopg2.OperationalError as error:
        pytest.skip(f"local PostgreSQL not reachable: {error}")
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT t.relname, array_agg(a.attname::text)
                FROM pg_index i
                JOIN pg_class t ON t.oid = i.indrelid
                JOIN pg_namespace n ON n.oid = t.relnamespace
                JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY (i.indkey)
                WHERE i.indisunique AND n.nspname = 'public' AND i.indpred IS NULL
                GROUP BY t.relname, i.indexrelid
                """
            )
            result = {}
            for table_name, columns in cursor.fetchall():
                result.setdefault(table_name, []).append(set(columns))
            return result
    finally:
        connection.close()


def _key_columns(entry):
    if entry.get("upsert_key"):
        return set(entry["upsert_key"])
    if entry["conflict_target"]:
        inside = re.match(r"\((.*?)\)", entry["conflict_target"]).group(1)
        return {column.strip().strip('"') for column in inside.split(",")}
    return None  # plain INSERT: no key to check


@pytest.mark.parametrize("entry", SYNC_TABLES, ids=lambda entry: entry["name"])
def test_configured_key_is_a_unique_index_of_the_local_table(entry, unique_index_columns):
    key = _key_columns(entry)
    if key is None:
        pytest.skip("plain INSERT, no key configured")
    if entry["name"] not in unique_index_columns:
        pytest.skip(f"{entry['name']} does not exist locally (migrations not applied)")

    assert key in unique_index_columns[entry["name"]], (
        f"{entry['name']}: configured key {sorted(key)} matches no unique index; "
        f"unique indexes are {[sorted(c) for c in unique_index_columns[entry['name']]]}"
    )
