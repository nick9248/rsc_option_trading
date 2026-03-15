"""
VPS Health Check

Checks everything the collection daemon is supposed to capture.
Outputs: timestamp, N/M checks OK, and a list of problems if any.

Usage:
    python scripts/check_vps_health.py
"""

import json
import subprocess
import sys
import logging
from datetime import datetime
from pathlib import Path

from coding.core.database.config import DatabaseConfig
from coding.core.logging.logging_setup import init_logging

init_logging(level="WARNING")  # Suppress library noise
logger = logging.getLogger(__name__)

import psycopg2

# ─────────────────────────────────────────────
# Thresholds
# ─────────────────────────────────────────────
MAX_TRADE_AGE_MIN       = 35   # Daemon runs every 30min, allow 5min slack
MAX_SNAPSHOT_AGE_MIN    = 35
MAX_HOURLY_AGE_MIN      = 70   # Hourly aggregation happens after each collect
MAX_ONCHAIN_AGE_MIN     = 35
MAX_FUNDING_AGE_MIN     = 35
MAX_DVOL_AGE_MIN        = 35
MAX_OHLCV_AGE_HOURS     = 25   # Daily candle, refreshed once per day
MIN_ROWS_PER_CURRENCY   = 1    # At least 1 row per currency per check


def _connect():
    cfg = DatabaseConfig()
    return psycopg2.connect(**cfg.get_connection_dict())


def _minutes_ago(dt: datetime) -> float:
    return (datetime.now() - dt).total_seconds() / 60


def _hours_ago(dt: datetime) -> float:
    return (datetime.now() - dt).total_seconds() / 3600


# ─────────────────────────────────────────────
# Individual checks — each returns (ok, message)
# ─────────────────────────────────────────────

def check_daemon_service():
    """Check if option-trading systemd service is running."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "option-trading"],
            capture_output=True, text=True, timeout=5
        )
        active = result.stdout.strip() == "active"
        if active:
            # Get PID
            pid_result = subprocess.run(
                ["systemctl", "show", "option-trading", "--property=MainPID"],
                capture_output=True, text=True, timeout=5
            )
            pid = pid_result.stdout.strip().replace("MainPID=", "")
            return True, f"systemd service RUNNING (pid {pid})"
        else:
            return False, f"systemd service NOT RUNNING (state: {result.stdout.strip()})"
    except FileNotFoundError:
        return None, "systemctl not available (not Linux)"
    except Exception as e:
        return False, f"systemd check failed: {e}"


def check_api():
    """Check Deribit API connectivity."""
    try:
        from coding.service.deribit.deribit_api_service import DeribitApiService
        api = DeribitApiService()
        response = api.get_ticker("BTC-PERPETUAL")
        if response and "index_price" in response:
            price = response["index_price"]
            return True, f"Deribit API OK — BTC ${price:,.0f}"
        return False, "Deribit API returned invalid response"
    except Exception as e:
        return False, f"Deribit API unreachable: {e}"


def check_database():
    """Check PostgreSQL connection."""
    try:
        conn = _connect()
        cur = conn.cursor()
        cur.execute("SELECT version()")
        version = cur.fetchone()[0].split(",")[0]
        cur.close()
        conn.close()
        return True, f"Database connected ({version})"
    except Exception as e:
        return False, f"Database connection failed: {e}"


def check_historical_trades(conn):
    """Check historical_trades freshness and coverage."""
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                currency,
                COUNT(*) as rows,
                MAX(TO_TIMESTAMP(trade_timestamp / 1000.0)) as latest
            FROM historical_trades
            GROUP BY currency
            ORDER BY currency
        """)
        rows = cur.fetchall()
        cur.close()

        if not rows:
            return False, "historical_trades: NO DATA"

        parts = []
        problems = []
        for currency, count, latest in rows:
            ago = _minutes_ago(latest.replace(tzinfo=None))
            parts.append(f"{currency} {count:,} rows, latest {ago:.0f}min ago")
            if ago > MAX_TRADE_AGE_MIN:
                problems.append(f"{currency} last trade {ago:.0f}min ago (max {MAX_TRADE_AGE_MIN}min)")

        msg = "historical_trades: " + " | ".join(parts)
        if problems:
            return False, msg + " — STALE: " + "; ".join(problems)
        return True, msg
    except Exception as e:
        return False, f"historical_trades check failed: {e}"


def check_snapshots(conn):
    """Check snapshots (book summary) freshness."""
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT COUNT(*) as instruments, MAX(captured_at) as latest
            FROM snapshots
        """)
        row = cur.fetchone()
        cur.close()

        if not row or not row[1]:
            return False, "snapshots: NO DATA"

        count, latest = row
        ago = _minutes_ago(latest)
        msg = f"snapshots: {count:,} instruments, latest {ago:.0f}min ago"
        if ago > MAX_SNAPSHOT_AGE_MIN:
            return False, msg + f" — STALE (max {MAX_SNAPSHOT_AGE_MIN}min)"
        return True, msg
    except Exception as e:
        return False, f"snapshots check failed: {e}"


def check_hourly_snapshots(conn):
    """Check hourly_snapshots aggregation."""
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                currency,
                COUNT(DISTINCT snapshot_hour) as hours,
                MAX(snapshot_hour) as latest
            FROM hourly_snapshots
            GROUP BY currency
            ORDER BY currency
        """)
        rows = cur.fetchall()
        cur.close()

        if not rows:
            return False, "hourly_snapshots: NO DATA"

        parts = []
        problems = []
        for currency, hours, latest in rows:
            ago = _minutes_ago(latest)
            parts.append(f"{currency} {hours}h, latest {ago:.0f}min ago")
            if ago > MAX_HOURLY_AGE_MIN:
                problems.append(f"{currency} last snapshot {ago:.0f}min ago (max {MAX_HOURLY_AGE_MIN}min)")

        msg = "hourly_snapshots: " + " | ".join(parts)
        if problems:
            return False, msg + " — STALE: " + "; ".join(problems)
        return True, msg
    except Exception as e:
        return False, f"hourly_snapshots check failed: {e}"


def check_onchain_snapshots(conn):
    """Check onchain_analysis_snapshots freshness."""
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                currency,
                COUNT(*) as rows,
                MAX(snapshot_hour) as latest
            FROM onchain_analysis_snapshots
            GROUP BY currency
            ORDER BY currency
        """)
        rows = cur.fetchall()
        cur.close()

        if not rows:
            return False, "onchain_analysis_snapshots: NO DATA"

        parts = []
        problems = []
        for currency, count, latest in rows:
            ago = _minutes_ago(latest)
            parts.append(f"{currency} {count} rows, latest {ago:.0f}min ago")
            if ago > MAX_ONCHAIN_AGE_MIN:
                problems.append(f"{currency} last onchain {ago:.0f}min ago")

        msg = "onchain_snapshots: " + " | ".join(parts)
        if problems:
            return False, msg + " — STALE: " + "; ".join(problems)
        return True, msg
    except Exception as e:
        return False, f"onchain_analysis_snapshots check failed: {e}"


def check_funding_rate(conn):
    """Check funding_rate_history freshness."""
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT currency, COUNT(*) as rows, MAX(date) as latest
            FROM funding_rate_history
            GROUP BY currency
            ORDER BY currency
        """)
        rows = cur.fetchall()
        cur.close()

        if not rows:
            return False, "funding_rate_history: NO DATA"

        parts = []
        problems = []
        for currency, count, latest in rows:
            ago = _minutes_ago(latest)
            parts.append(f"{currency} {count} rows, latest {ago:.0f}min ago")
            if ago > MAX_FUNDING_AGE_MIN:
                problems.append(f"{currency} last funding {ago:.0f}min ago")

        msg = "funding_rate_history: " + " | ".join(parts)
        if problems:
            return False, msg + " — STALE: " + "; ".join(problems)
        return True, msg
    except Exception as e:
        return False, f"funding_rate_history check failed: {e}"


def check_dvol(conn):
    """Check volatility_index_history (DVOL) freshness."""
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT currency, COUNT(*) as rows, MAX(date) as latest
            FROM volatility_index_history
            GROUP BY currency
            ORDER BY currency
        """)
        rows = cur.fetchall()
        cur.close()

        if not rows:
            return False, "volatility_index_history (DVOL): NO DATA"

        parts = []
        problems = []
        for currency, count, latest in rows:
            ago = _minutes_ago(latest)
            parts.append(f"{currency} {count} rows, latest {ago:.0f}min ago")
            if ago > MAX_DVOL_AGE_MIN:
                problems.append(f"{currency} last DVOL {ago:.0f}min ago")

        msg = "dvol_history: " + " | ".join(parts)
        if problems:
            return False, msg + " — STALE: " + "; ".join(problems)
        return True, msg
    except Exception as e:
        return False, f"volatility_index_history check failed: {e}"


def check_ohlcv(conn):
    """Check ohlcv_history (daily candles)."""
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT currency, COUNT(*) as candles, MAX(date) as latest
            FROM ohlcv_history
            GROUP BY currency
            ORDER BY currency
        """)
        rows = cur.fetchall()
        cur.close()

        if not rows:
            return False, "ohlcv_history: NO DATA"

        parts = []
        problems = []
        for currency, candles, latest in rows:
            ago_h = _hours_ago(latest)
            parts.append(f"{currency} {candles} candles, latest {ago_h:.1f}h ago")
            if ago_h > MAX_OHLCV_AGE_HOURS:
                problems.append(f"{currency} last OHLCV {ago_h:.1f}h ago")

        msg = "ohlcv_history: " + " | ".join(parts)
        if problems:
            return False, msg + f" — STALE (max {MAX_OHLCV_AGE_HOURS}h)"
        return True, msg
    except Exception as e:
        return False, f"ohlcv_history check failed: {e}"


# ─────────────────────────────────────────────
# Main runner
# ─────────────────────────────────────────────

def run():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*60}")
    print(f"VPS HEALTH CHECK — {now}")
    print(f"{'='*60}")

    results = []  # (ok, label, message)

    # 1. Daemon service
    ok, msg = check_daemon_service()
    if ok is not None:
        results.append((ok, "Daemon Service", msg))

    # 2. API
    ok, msg = check_api()
    results.append((ok, "API", msg))

    # 3. Database — needed for all further checks
    ok, msg = check_database()
    results.append((ok, "Database", msg))

    if not ok:
        _write_json(results, now)
        _print_results(results)
        return

    # 4-10. Data table checks
    try:
        conn = _connect()
        checks = [
            check_historical_trades,
            check_snapshots,
            check_hourly_snapshots,
            check_onchain_snapshots,
            check_funding_rate,
            check_dvol,
            check_ohlcv,
        ]
        labels = [
            "Historical Trades",
            "Snapshots",
            "Hourly Snapshots",
            "Onchain Snapshots",
            "Funding Rate",
            "DVOL",
            "OHLCV",
        ]
        for fn, label in zip(checks, labels):
            ok, msg = fn(conn)
            results.append((ok, label, msg))
        conn.close()
    except Exception as e:
        results.append((False, "Data Checks", f"Failed to connect for table checks: {e}"))

    _write_json(results, now)
    _print_results(results)


def _write_json(results, timestamp: str):
    """Write health check results to logs/vps_health.json."""
    total = len(results)
    passed = sum(1 for ok, _, _ in results if ok)
    problems = [msg for ok, label, msg in results if not ok]

    data = {
        "timestamp": timestamp,
        "total": total,
        "passed": passed,
        "results": [
            {"ok": ok, "label": label, "message": msg}
            for ok, label, msg in results
        ],
        "problems": problems,
    }

    log_dir = Path(__file__).parents[1] / "logs"
    log_dir.mkdir(exist_ok=True)
    out_path = log_dir / "vps_health.json"
    out_path.write_text(json.dumps(data, indent=2))


def _print_results(results):
    total = len(results)
    passed = sum(1 for ok, _, _ in results if ok)
    problems = [(label, msg) for ok, label, msg in results if not ok]

    print()
    for i, (ok, label, msg) in enumerate(results, 1):
        icon = "OK " if ok else "ERR"
        print(f"  [{icon}] {i:2d}. {msg}")

    print()
    print(f"RESULT: {passed}/{total} checks OK")

    if problems:
        print(f"\nPROBLEMS ({len(problems)}):")
        for label, msg in problems:
            print(f"  - {msg}")
    else:
        print("All checks passed.")

    print(f"{'='*60}\n")

    sys.exit(0 if not problems else 1)


if __name__ == "__main__":
    run()
