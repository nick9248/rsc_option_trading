"""
VPS → Local Sync Script

Opens an SSH tunnel to the VPS and pulls all new rows from each
collection table into the local PostgreSQL database.

Usage:
    python scripts/sync_from_vps.py

Runs on Windows local PC. Requires:
- SSH config with 'option-server' alias (C:\\Users\\Nick\\.ssh\\config)
- Local PostgreSQL on port 5433 (postgres user)
- VPS PostgreSQL on port 5432 (nick user, accessed via tunnel on port 5434)
"""

import os
import sys
import time
import logging
import subprocess
from datetime import datetime

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=Path(__file__).parents[1] / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Connection settings
# ─────────────────────────────────────────────
LOCAL_CONN = {
    "host": "localhost",
    "port": 5433,
    "database": "option_trading",
    "user": "postgres",
    "password": os.getenv("DB_PASSWORD"),
}

VPS_TUNNEL_PORT = 5434  # Local port forwarded to VPS:5432
VPS_TUNNEL_CONN = {
    "host": "localhost",
    "port": VPS_TUNNEL_PORT,
    "database": "option_trading",
    "user": "nick",
    "password": os.getenv("DB_PASSWORD"),
}

FETCH_BATCH_SIZE = 50_000    # Rows read from the VPS per batch (bounds memory use)
LOG_EVERY_BATCHES = 10       # Progress line every 10 committed batches (500k rows)
MAX_WATERMARK_GROUP_ROWS = 1_000_000  # Safety cap for rows sharing one watermark value
SSH_HOST = "option-server"   # Matches C:\Users\Nick\.ssh\config
TUNNEL_WAIT_SEC = 3          # Seconds to wait for tunnel to establish

# ─────────────────────────────────────────────
# Table sync configuration
#
# watermark_col  : column used to find "what's new" (timestamp/bigint)
# watermark_type : "timestamp" or "bigint" (affects comparison query)
# conflict_target: ON CONFLICT clause suffix, or None for plain INSERT
# upsert_key     : optional. For tables whose rows are UPDATED on the VPS after
#                  insertion: the unique key columns. The whole table is re-read
#                  each sync and local rows that differ from the VPS are updated.
#                  conflict_target is not used for these tables.
# ─────────────────────────────────────────────
SYNC_TABLES = [
    {
        "name": "historical_trades",
        "watermark_col": "captured_at",
        "watermark_type": "timestamp",
        "conflict_target": "(trade_id, trade_timestamp) DO NOTHING",
    },
    {
        "name": "snapshots",
        "watermark_col": "captured_at",
        "watermark_type": "timestamp",
        "conflict_target": None,
    },
    {
        "name": "hourly_snapshots",
        "watermark_col": "snapshot_hour",
        "watermark_type": "timestamp",
        "conflict_target": "(instrument_name, snapshot_hour) DO NOTHING",
    },
    {
        "name": "onchain_analysis_snapshots",
        "watermark_col": "snapshot_hour",
        "watermark_type": "timestamp",
        "conflict_target": "(snapshot_hour, currency, expiration) DO NOTHING",
    },
    {
        # institutional_metrics_spec.md section 6 / infra_spec.md section 2
        # -- task C7 review fix, Important #3. The daemon writes this table
        # on the VPS only (ProspectiveCollector._persist_delta_flow); the
        # GUI and report read from the LOCAL DB. Without a sync entry here,
        # local queries return empty indefinitely -- never "until 24h of
        # daemon runs accumulate" as originally (incorrectly) characterized
        # in task-C7-report.md, but "forever," because the data never
        # reaches the machine that renders the report.
        "name": "flow_delta_hourly",
        "watermark_col": "snapshot_hour",
        "watermark_type": "timestamp",
        "conflict_target": "(snapshot_hour, currency, expiration) DO NOTHING",
    },
    {
        "name": "funding_rate_history",
        "watermark_col": "date",
        "watermark_type": "timestamp",
        "conflict_target": "(instrument_name, timestamp) DO NOTHING",
    },
    {
        "name": "volatility_index_history",
        "watermark_col": "date",
        "watermark_type": "timestamp",
        "conflict_target": "(index_name, timestamp) DO NOTHING",
    },
    {
        "name": "ohlcv_history",
        "watermark_col": "date",
        "watermark_type": "timestamp",
        "conflict_target": "(instrument_name, timestamp) DO NOTHING",
    },
    {
        "name": "max_pain",
        "watermark_col": "captured_at",
        "watermark_type": "timestamp",
        "conflict_target": None,
    },
    {
        "name": "open_interest",
        "watermark_col": "captured_at",
        "watermark_type": "timestamp",
        "conflict_target": None,
    },
    {
        "name": "volume",
        "watermark_col": "captured_at",
        "watermark_type": "timestamp",
        "conflict_target": None,
    },
    {
        "name": "onchain_volatility_snapshots",
        "watermark_col": "reconstructed_at",
        "watermark_type": "timestamp",
        "conflict_target": "(snapshot_hour, currency, expiration) DO NOTHING",
    },
    {
        # Rows are updated on the VPS after insertion (resolved_at and the outcome
        # columns are filled in when the forecast horizon passes), so the whole
        # table is re-read every sync and local rows that differ are updated.
        "name": "forward_test_predictions",
        "watermark_col": "created_at",
        "watermark_type": "timestamp",
        "conflict_target": None,
        "upsert_key": ["currency", "snapshot_hour"],
    },
    {
        "name": "daily_oi_snapshots",
        "watermark_col": "created_at",
        "watermark_type": "timestamp",
        "conflict_target": "(snapshot_date, snapshot_hour_utc, currency, expiration, strike, option_type) DO NOTHING",
    },
    {
        "name": "dvol_history",
        "watermark_col": "timestamp",
        "watermark_type": "timestamp",
        "conflict_target": '(asset, "timestamp") DO NOTHING',
    },
    {
        "name": "volatility_skew_history",
        "watermark_col": "snapshot_hour",
        "watermark_type": "timestamp",
        "conflict_target": "(snapshot_hour, currency, expiration) DO NOTHING",
    },
    {
        # Alert flags and expiry settlement results are written after insertion.
        "name": "straddle_scan_history",
        "watermark_col": "scan_time",
        "watermark_type": "timestamp",
        "conflict_target": None,
        "upsert_key": ["currency", "expiration", "scan_time"],
    },
    {
        "name": "defined_risk_scan_history",
        "watermark_col": "scan_time",
        "watermark_type": "timestamp",
        "conflict_target": None,
        "upsert_key": ["currency", "expiration", "structure_type", "scan_time"],
    },
]


def get_columns(cur, table_name: str) -> list[str]:
    """Get all columns for a table, excluding 'id' (auto-generated locally)."""
    cur.execute("""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
    """, (table_name,))
    return [row[0] for row in cur.fetchall() if row[0] != "id"]


def get_local_watermark(cur, table: dict):
    """Get the max watermark value on the local DB (None if table is empty)."""
    col = table["watermark_col"]
    cur.execute(f"SELECT MAX({col}) FROM {table['name']}")
    result = cur.fetchone()[0]
    return result


def _best_effort(action) -> None:
    """Run a cleanup step. A broken connection must not mask the original error."""
    try:
        action()
    except psycopg2.Error as error:
        logger.warning(f"Cleanup step failed (ignored so the original error is kept): {error}")


def _fetch_rows(vps_conn, query: str, params: tuple) -> list:
    """Run one short read on the VPS and return all its rows."""
    try:
        with vps_conn.cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()
        vps_conn.commit()  # no-op in autocommit mode; otherwise ends the read transaction
        return rows
    except Exception:
        _best_effort(vps_conn.rollback)  # never leave an aborted transaction behind
        raise


def iter_new_row_batches(vps_conn, table: dict, columns: list, local_max, batch_size: int):
    """
    Yield lists of rows from the VPS newer than local_max, in watermark order.

    The VPS is read in short pages (ORDER BY watermark LIMIT n), one query each,
    so no long transaction is held open on the production database and memory
    use stays bounded (a 16M-row table would need ~19 GB with fetchall()).

    Rows that share a watermark value (e.g. every row of one capture) are never
    split across batches: the last, possibly unfinished, group of a full page is
    dropped and re-read at the start of the next page, and a group bigger than a
    whole page is read in one go. Every batch therefore ends on a complete
    group, so committing batch by batch and resuming from the local MAX()
    watermark neither skips nor duplicates rows.

    The VPS connection is expected to be in autocommit mode (both callers set it).

    Assumptions (checked against the VPS on 2026-10-11):
      * every table has an index starting with its watermark column, so each page
        is an index range scan (the five small tables without one hold <32k rows);
      * a single watermark value holds few rows (largest recent group: 960); a group
        that fills a whole page is read in one go, capped at
        MAX_WATERMARK_GROUP_ROWS (the sync stops with an error above that);
      * no table has NULL watermarks; such rows are never synced.
    Like every watermark sync, rows written late with an older watermark, or into
    a group that is still being written when it is copied, are not picked up.
    """
    col_list = ", ".join(columns)
    watermark_col = table["watermark_col"]
    watermark_index = columns.index(watermark_col)
    select = f"SELECT {col_list} FROM {table['name']}"

    last_seen = local_max
    while True:
        if last_seen is None:
            query = f"{select} WHERE {watermark_col} IS NOT NULL ORDER BY {watermark_col} LIMIT %s"
            params = (batch_size,)
        else:
            query = f"{select} WHERE {watermark_col} > %s ORDER BY {watermark_col} LIMIT %s"
            params = (last_seen, batch_size)
        page = _fetch_rows(vps_conn, query, params)

        if not page:
            return
        if len(page) < batch_size:
            yield page  # last page: nothing follows, so every group in it is complete
            return

        newest = page[-1][watermark_index]
        complete = [row for row in page if row[watermark_index] < newest]
        if complete:
            last_seen = complete[-1][watermark_index]
        else:
            # One watermark value fills the whole page: read that group in full.
            complete = _fetch_rows(
                vps_conn,
                f"{select} WHERE {watermark_col} = %s LIMIT %s",
                (newest, MAX_WATERMARK_GROUP_ROWS + 1),
            )
            if len(complete) > MAX_WATERMARK_GROUP_ROWS:
                raise RuntimeError(
                    f"{table['name']}: more than {MAX_WATERMARK_GROUP_ROWS:,} rows share "
                    f"{watermark_col} = {newest}; refusing to load them into memory at once"
                )
            last_seen = newest
        yield complete


def insert_rows(local_cur, table: dict, columns: list, rows: list) -> int:
    """Insert rows into local DB. Returns the number of rows actually inserted
    (rows skipped by ON CONFLICT DO NOTHING are not counted)."""
    if not rows:
        return 0

    col_list = ", ".join(columns)

    if table["conflict_target"]:
        sql = f"INSERT INTO {table['name']} ({col_list}) VALUES %s ON CONFLICT {table['conflict_target']} RETURNING 1"
    else:
        sql = f"INSERT INTO {table['name']} ({col_list}) VALUES %s RETURNING 1"

    inserted = execute_values(local_cur, sql, rows, fetch=True)
    return len(inserted)


def upsert_rows(local_cur, table: dict, columns: list, rows: list) -> int:
    """Insert new rows and update local rows that differ from the VPS.
    Returns the number of rows inserted or changed (unchanged rows are not counted)."""
    if not rows:
        return 0

    key = table["upsert_key"]
    value_columns = [column for column in columns if column not in key]
    col_list = ", ".join(columns)
    assignments = ", ".join(f"{column} = EXCLUDED.{column}" for column in value_columns)
    local_values = ", ".join(f"t.{column}" for column in value_columns)
    vps_values = ", ".join(f"EXCLUDED.{column}" for column in value_columns)

    sql = (
        f"INSERT INTO {table['name']} AS t ({col_list}) VALUES %s "
        f"ON CONFLICT ({', '.join(key)}) DO UPDATE SET {assignments} "
        f"WHERE ({local_values}) IS DISTINCT FROM ({vps_values}) RETURNING 1"
    )
    changed = execute_values(local_cur, sql, rows, fetch=True)
    return len(changed)


def sync_table(vps_conn, local_conn, table: dict) -> tuple[int, str]:
    """
    Sync one table from VPS to local.
    Returns (rows_synced, status_message).

    The VPS is read in batches that never split a group of rows sharing a
    watermark value, and each batch is committed locally as soon as it is
    inserted. Tables with an "upsert_key" (rows updated on the VPS after
    insertion) are re-read in full each time and only changed rows are written. If a run is interrupted, everything committed so far is a complete
    prefix of the table, and the next run resumes after it.
    """
    vps_cur = None
    local_cur = None
    fetched = 0
    inserted = 0
    try:
        vps_cur = vps_conn.cursor()
        local_cur = local_conn.cursor()

        # Get columns from VPS (source of truth — avoids local schema drift)
        columns = get_columns(vps_cur, table["name"])
        vps_cur.close()  # nothing may stay open on the VPS connection while paging

        # Tables whose rows change after insertion are re-read in full and upserted;
        # all others are read incrementally from the local watermark.
        is_upsert_table = bool(table.get("upsert_key"))
        if is_upsert_table:
            local_max = None
            write_rows = upsert_rows
        else:
            local_max = get_local_watermark(local_cur, table)
            write_rows = insert_rows

        # Copy rows from VPS, committing batch by batch
        batch_number = 0
        for rows in iter_new_row_batches(vps_conn, table, columns, local_max, FETCH_BATCH_SIZE):
            fetched += len(rows)
            inserted += write_rows(local_cur, table, columns, rows)
            local_conn.commit()
            batch_number += 1
            if batch_number % LOG_EVERY_BATCHES == 0:
                logger.info(f"      {table['name']}: {inserted:,} rows committed")

        if fetched == 0:
            # End the read-only transaction opened by the watermark query.
            local_conn.rollback()
            return 0, "up to date"

        if is_upsert_table:
            if inserted == 0:
                return 0, f"up to date ({fetched:,} rows checked)"
            return inserted, f"{inserted:,} new or updated ({fetched:,} rows checked)"

        already_present = fetched - inserted
        note = f" ({already_present:,} already present)" if already_present else ""
        return inserted, f"+{inserted} rows{note}"

    except Exception as e:
        logger.exception(f"Sync of {table['name']} failed")
        _best_effort(local_conn.rollback)
        resume_hint = f" ({inserted:,} rows already committed; the next run resumes after them)" if inserted else ""
        return -1, f"ERROR: {e}{resume_hint}"

    finally:
        for cursor in (vps_cur, local_cur):
            if cursor is not None:
                _best_effort(cursor.close)


def _pull_health_json():
    """Copy vps_health.json from VPS to local logs/ directory."""
    try:
        local_logs = Path(__file__).parents[1] / "logs"
        local_logs.mkdir(exist_ok=True)
        result = subprocess.run(
            ["ssh", SSH_HOST, "cat /home/nick/option_trading/logs/vps_health.json"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            (local_logs / "vps_health.json").write_text(result.stdout)
            logger.info("  [OK ] vps_health.json                      pulled")
        else:
            logger.warning("  [WARN] vps_health.json not found on VPS yet")
    except Exception as e:
        logger.warning(f"  [WARN] Could not pull vps_health.json: {e}")


def open_ssh_tunnel() -> subprocess.Popen:
    """Open SSH tunnel: local 5434 → VPS 5432."""
    logger.info(f"Opening SSH tunnel: localhost:{VPS_TUNNEL_PORT} → {SSH_HOST}:5432 ...")
    proc = subprocess.Popen(
        ["ssh", "-L", f"{VPS_TUNNEL_PORT}:localhost:5432", "-N",
         "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no",
         SSH_HOST],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    time.sleep(TUNNEL_WAIT_SEC)

    if proc.poll() is not None:
        raise RuntimeError(f"SSH tunnel failed to start (exit code {proc.returncode})")

    logger.info(f"Tunnel established (pid {proc.pid})")
    return proc


def run():
    start = datetime.now()
    logger.info("=" * 60)
    logger.info(f"VPS SYNC STARTED — {start.strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    tunnel = None
    vps_conn = None
    local_conn = None

    try:
        # 1. Open tunnel
        tunnel = open_ssh_tunnel()

        # 2. Connect to VPS via tunnel
        logger.info("Connecting to VPS database via tunnel...")
        vps_conn = psycopg2.connect(**VPS_TUNNEL_CONN)
        vps_conn.autocommit = True

        # 3. Connect to local DB
        logger.info("Connecting to local database...")
        local_conn = psycopg2.connect(**LOCAL_CONN)

        # 4. Sync each table
        logger.info(f"\nSyncing {len(SYNC_TABLES)} tables...")
        logger.info("-" * 60)

        total_rows = 0
        errors = []

        for table in SYNC_TABLES:
            count, msg = sync_table(vps_conn, local_conn, table)
            status = "OK " if count >= 0 else "ERR"
            logger.info(f"  [{status}] {table['name']:<35} {msg}")
            if count > 0:
                total_rows += count
            if count < 0:
                errors.append(f"{table['name']}: {msg}")

        # 5. Pull VPS health JSON
        _pull_health_json()

        # 6. Summary
        duration = (datetime.now() - start).total_seconds()
        logger.info("-" * 60)
        logger.info(f"\nSYNC COMPLETE in {duration:.1f}s")
        logger.info(f"  Total rows synced: {total_rows:,}")
        logger.info(f"  Tables synced: {len(SYNC_TABLES)}")

        if errors:
            logger.error(f"\nERRORS ({len(errors)}):")
            for err in errors:
                logger.error(f"  - {err}")
        else:
            logger.info("  All tables synced successfully.")

        logger.info("=" * 60)
        return len(errors) == 0

    except Exception as e:
        logger.error(f"Sync failed: {e}")
        return False

    finally:
        if vps_conn:
            vps_conn.close()
        if local_conn:
            local_conn.close()
        if tunnel:
            tunnel.terminate()
            logger.info("SSH tunnel closed.")


if __name__ == "__main__":
    success = run()
    sys.exit(0 if success else 1)
