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
LOG_EVERY_BATCHES = 10       # Progress line every 10 batches (500k rows)
SSH_HOST = "option-server"   # Matches C:\Users\Nick\.ssh\config
TUNNEL_WAIT_SEC = 3          # Seconds to wait for tunnel to establish

# ─────────────────────────────────────────────
# Table sync configuration
#
# watermark_col  : column used to find "what's new" (timestamp/bigint)
# watermark_type : "timestamp" or "bigint" (affects comparison query)
# conflict_target: ON CONFLICT clause suffix, or None for plain INSERT
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
        "name": "forward_test_predictions",
        "watermark_col": "created_at",
        "watermark_type": "timestamp",
        "conflict_target": "(currency, snapshot_hour) DO NOTHING",
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
    except psycopg2.Error:
        pass


def iter_new_row_batches(vps_conn, table: dict, columns: list, local_max, batch_size: int):
    """
    Yield lists of rows from the VPS newer than local_max, batch_size at a time.

    Reads through a server-side (named) cursor so the whole result set is never
    held in memory: a 16M-row table would need roughly 19 GB as Python objects.
    A named cursor only works inside a transaction, so autocommit is switched
    off for the read and restored afterwards.
    """
    col_list = ", ".join(columns)
    watermark_col = table["watermark_col"]

    if local_max is None:
        query = f"SELECT {col_list} FROM {table['name']} ORDER BY {watermark_col}"
        params = None
    else:
        query = (
            f"SELECT {col_list} FROM {table['name']} "
            f"WHERE {watermark_col} > %s ORDER BY {watermark_col}"
        )
        params = (local_max,)

    was_autocommit = vps_conn.autocommit
    vps_conn.autocommit = False
    cursor = None
    try:
        cursor = vps_conn.cursor(name=f"sync_{table['name']}")
        cursor.execute(query, params)
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            yield rows
    finally:
        if cursor is not None:
            _best_effort(cursor.close)
        _best_effort(vps_conn.rollback)
        _best_effort(lambda: setattr(vps_conn, "autocommit", was_autocommit))


def insert_rows(local_cur, table: dict, columns: list, rows: list) -> int:
    """Insert rows into local DB. Returns number of rows inserted."""
    if not rows:
        return 0

    col_list = ", ".join(columns)
    placeholders = ", ".join(["%s"] * len(columns))

    if table["conflict_target"]:
        sql = f"INSERT INTO {table['name']} ({col_list}) VALUES %s ON CONFLICT {table['conflict_target']}"
    else:
        sql = f"INSERT INTO {table['name']} ({col_list}) VALUES %s"

    execute_values(local_cur, sql, rows)
    return len(rows)


def sync_table(vps_conn, local_conn, table: dict) -> tuple[int, str]:
    """
    Sync one table from VPS to local.
    Returns (rows_synced, status_message).

    Rows are streamed in batches, but the table is still written in a single
    local transaction: it is committed once at the end, or rolled back on any
    error. Committing per batch could skip rows on resume, because rows from
    one capture share the same watermark value.
    """
    vps_cur = None
    local_cur = None
    try:
        vps_cur = vps_conn.cursor()
        local_cur = local_conn.cursor()

        # Get columns from VPS (source of truth — avoids local schema drift)
        columns = get_columns(vps_cur, table["name"])

        # Find local watermark
        local_max = get_local_watermark(local_cur, table)

        # Stream new rows from VPS into the local transaction
        count = 0
        batches = iter_new_row_batches(vps_conn, table, columns, local_max, FETCH_BATCH_SIZE)
        try:
            for batch_number, rows in enumerate(batches, start=1):
                count += insert_rows(local_cur, table, columns, rows)
                if batch_number % LOG_EVERY_BATCHES == 0:
                    logger.info(f"      {table['name']}: {count:,} rows staged (commit at end of table)")
        finally:
            batches.close()

        if count == 0:
            # End the read-only transaction opened by the watermark query.
            local_conn.rollback()
            return 0, "up to date"

        local_conn.commit()
        return count, f"+{count} rows"

    except Exception as e:
        _best_effort(local_conn.rollback)
        return -1, f"ERROR: {e}"

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
