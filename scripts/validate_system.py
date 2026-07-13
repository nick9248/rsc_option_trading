"""
System Health Validator

Reflects the current system: data collection (VPS daemon -> local sync) +
on-chain analysis validation. Checks:

- API connectivity
- Database connection
- Required tables/views exist
- Collection freshness (does the local DB show recent hourly data?)
- Collection gaps in the last 48 hours
- Local DB sync status (age of the last "Sync from VPS")
- Forward-testing harness track record (Phase 3)
- Historical trades data quality (direction/IV completeness - feeds
  buy/sell flow analysis)
- OHLCV history coverage (feeds regime detection)
- Backfill coverage (historical_trades date range)

Removed (obsolete after the foundation-cleanup sprint): strategy signal
checks, ML model/pipeline checks, old manual-capture table checks
(max_pain/open_interest/volume/gex_dex/levels), displacement checks.

Note on "daemon liveness": the collection daemon runs on a VPS
(coding/service/data_collection/collection_daemon.py, systemd-managed,
30-min interval), not on this machine, so its liveness cannot be checked
via local log files or SSH from here. Instead we check freshness of the
LOCAL onchain_analysis_snapshots/hourly_snapshots tables. A stale result
means either the VPS daemon stopped OR the local DB simply hasn't been
synced recently (use the Database tab's "Sync from VPS" button, or
scripts/sync_from_vps.py) - this script cannot tell those two apart from
the local DB alone; check logs/vps_health.json (pulled during sync) or
scripts/check_vps_health.py for a VPS-side view.

Run this before claiming "everything works".
"""

import json
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict

# Allow running directly (python scripts/validate_system.py) as well as
# as a module (python -m scripts.validate_system).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from coding.core.database.repository import DatabaseRepository
from coding.core.logging.logging_setup import init_logging

init_logging(level="INFO")
logger = logging.getLogger(__name__)

TOTAL_CHECKS = 10


class SystemValidator:
    """System health checker for the data-collection + on-chain-analysis system."""

    def __init__(self):
        """Initialize validator."""
        self.repo = DatabaseRepository()
        self.results = {
            "passed": [],
            "warnings": [],
            "failed": []
        }

    def validate_all(self) -> Dict:
        """
        Run all validation checks.

        Returns:
            Validation results dictionary
        """
        logger.info("=" * 80)
        logger.info("SYSTEM HEALTH VALIDATION")
        logger.info("=" * 80)
        logger.info(f"Started: {datetime.now()}\n")

        self._check_api_connectivity()
        self._check_database_connection()
        self._check_required_tables()
        self._check_collection_freshness()
        self._check_collection_gaps()
        self._check_last_sync_status()
        self._check_forward_testing_harness()
        self._check_historical_trades_quality()
        self._check_ohlcv_history()
        self._check_backfill_coverage()

        self._print_summary()

        return self.results

    def _check_api_connectivity(self):
        """Check Deribit API connectivity."""
        logger.info(f"\n[1/{TOTAL_CHECKS}] Checking API Connectivity...")
        logger.info("-" * 80)

        try:
            from coding.service.deribit.deribit_api_service import DeribitApiService
            api = DeribitApiService()

            response = api.get_ticker("BTC-PERPETUAL")

            if response and "index_price" in response:
                btc_price = response["index_price"]
                logger.info("  API Status: CONNECTED")
                logger.info(f"  BTC Price: ${btc_price:,.2f}")
                self.results["passed"].append("API Connectivity")
            else:
                raise Exception("Invalid API response")

        except Exception as e:
            logger.error(f"  API Status: FAILED - {e}")
            self.results["failed"].append(f"API Connectivity: {e}")

    def _check_database_connection(self):
        """Check database connection."""
        logger.info(f"\n[2/{TOTAL_CHECKS}] Checking Database Connection...")
        logger.info("-" * 80)

        try:
            conn = self.repo._get_connection()
            cursor = conn.cursor()

            cursor.execute("SELECT version()")
            version = cursor.fetchone()[0]

            logger.info("  Database: CONNECTED")
            logger.info(f"  PostgreSQL: {version.split(',')[0]}")

            cursor.close()
            self.repo._return_connection(conn)

            self.results["passed"].append("Database Connection")

        except Exception as e:
            logger.error(f"  Database: FAILED - {e}")
            self.results["failed"].append(f"Database Connection: {e}")

    def _check_required_tables(self):
        """Check the current system's required tables/views exist."""
        logger.info(f"\n[3/{TOTAL_CHECKS}] Checking Required Tables...")
        logger.info("-" * 80)

        required_tables = [
            "snapshots",                     # raw book-summary rows (daemon-written)
            "historical_trades",
            "hourly_snapshots",
            "latest_hourly_snapshots",        # view
            "onchain_analysis_snapshots",
            "onchain_volatility_snapshots",   # Phase 2/3 reconstructed metrics
            "forward_test_predictions",       # Phase 3 harness
            "regime_detections",              # regime detection (foundation, keep)
            "ohlcv_history",
            "dvol_history",
        ]

        conn = self.repo._get_connection()
        cursor = conn.cursor()

        try:
            missing = []
            for table in required_tables:
                cursor.execute("""
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables WHERE table_name = %s
                        UNION ALL
                        SELECT 1 FROM information_schema.views WHERE table_name = %s
                    )
                """, (table, table))
                exists = cursor.fetchone()[0]

                if exists:
                    logger.info(f"  {table}: EXISTS")
                else:
                    logger.error(f"  {table}: MISSING")
                    missing.append(table)

            if missing:
                self.results["failed"].append(f"Missing tables: {', '.join(missing)}")
            else:
                self.results["passed"].append("All Required Tables")

        finally:
            cursor.close()
            self.repo._return_connection(conn)

    def _check_collection_freshness(self):
        """
        Check freshness of the local DB's collection tables.

        Reflects the state of the LOCAL database (post-sync), not the VPS
        daemon directly - see module docstring.
        """
        logger.info(f"\n[4/{TOTAL_CHECKS}] Checking Collection Freshness (local DB)...")
        logger.info("-" * 80)

        tables_to_check = {
            "historical_trades": ("trade_timestamp", "ms_epoch"),
            "hourly_snapshots": ("snapshot_hour", "timestamp"),
            "onchain_analysis_snapshots": ("snapshot_hour", "timestamp"),
        }

        conn = self.repo._get_connection()
        cursor = conn.cursor()

        try:
            for table, (time_col, kind) in tables_to_check.items():
                cursor.execute(f"SELECT MAX({time_col}) FROM {table}")
                latest = cursor.fetchone()[0]

                if latest is None:
                    logger.error(f"  {table}: NO DATA")
                    self.results["failed"].append(f"{table} has no data")
                    continue

                if kind == "ms_epoch":
                    latest = datetime.fromtimestamp(latest / 1000)

                hours_ago = (datetime.now() - latest).total_seconds() / 3600

                if hours_ago < 2:
                    logger.info(f"  {table}: FRESH ({hours_ago * 60:.0f} min ago)")
                    self.results["passed"].append(f"{table} is fresh")
                elif hours_ago < 24:
                    logger.warning(f"  {table}: STALE ({hours_ago:.1f}h ago) - sync or check the VPS daemon")
                    self.results["warnings"].append(f"{table} is {hours_ago:.1f}h old")
                else:
                    logger.error(f"  {table}: VERY STALE ({hours_ago / 24:.1f} days ago)")
                    self.results["failed"].append(f"{table} is {hours_ago / 24:.1f} days old")

        finally:
            cursor.close()
            self.repo._return_connection(conn)

    def _check_collection_gaps(self):
        """Check for missing hours in onchain_analysis_snapshots over the last 48h."""
        logger.info(f"\n[5/{TOTAL_CHECKS}] Checking Collection Gaps (last 48h)...")
        logger.info("-" * 80)

        window_start = datetime.now() - timedelta(hours=48)

        conn = self.repo._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT currency, COUNT(DISTINCT snapshot_hour)
                FROM onchain_analysis_snapshots
                WHERE snapshot_hour >= %s
                GROUP BY currency
                ORDER BY currency
            """, (window_start,))
            rows = cursor.fetchall()

            if not rows:
                logger.error("  No onchain_analysis_snapshots in the last 48h")
                self.results["failed"].append("No collection activity in last 48h")
                return

            # Expect roughly 1 row per hour per currency over the window
            # (daemon runs every 30 min, but analysis is stored at hourly grain).
            expected_hours = 48
            for currency, hours in rows:
                coverage_pct = (hours / expected_hours) * 100
                logger.info(f"  {currency}: {hours}/{expected_hours} hours ({coverage_pct:.0f}%)")

                if coverage_pct >= 90:
                    self.results["passed"].append(f"{currency} collection coverage {coverage_pct:.0f}% (48h)")
                elif coverage_pct >= 60:
                    self.results["warnings"].append(
                        f"{currency} collection coverage only {coverage_pct:.0f}% in last 48h"
                    )
                else:
                    self.results["failed"].append(
                        f"{currency} collection coverage critically low ({coverage_pct:.0f}% in last 48h)"
                    )

        finally:
            cursor.close()
            self.repo._return_connection(conn)

    def _check_last_sync_status(self):
        """Check when the local DB was last synced from the VPS."""
        logger.info(f"\n[6/{TOTAL_CHECKS}] Checking Last Sync Status...")
        logger.info("-" * 80)

        health_path = Path(__file__).parent.parent / "logs" / "vps_health.json"

        if not health_path.exists():
            logger.warning("  No logs/vps_health.json found - never synced from VPS yet")
            self.results["warnings"].append("Never synced from VPS (no logs/vps_health.json)")
            return

        try:
            data = json.loads(health_path.read_text())
            timestamp_str = data.get("timestamp")
            problems = data.get("problems", [])

            if timestamp_str:
                synced_at = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                hours_ago = (datetime.now() - synced_at).total_seconds() / 3600
                logger.info(f"  Last sync: {timestamp_str} ({hours_ago:.1f}h ago)")

                if hours_ago < 2:
                    self.results["passed"].append(f"Last synced {hours_ago:.1f}h ago")
                elif hours_ago < 24:
                    self.results["warnings"].append(f"Last sync was {hours_ago:.1f}h ago - consider syncing")
                else:
                    self.results["failed"].append(f"Last sync was {hours_ago / 24:.1f} days ago")
            else:
                logger.warning("  vps_health.json has no timestamp field")
                self.results["warnings"].append("vps_health.json missing timestamp")

            if problems:
                logger.warning(f"  VPS-side problems reported: {len(problems)}")
                self.results["warnings"].append(f"VPS health reports {len(problems)} problem(s)")

        except Exception as e:
            logger.error(f"  Failed to read vps_health.json: {e}")
            self.results["warnings"].append(f"Could not read vps_health.json: {e}")

    def _check_forward_testing_harness(self):
        """Check the Phase 3 forward-testing harness is recording predictions."""
        logger.info(f"\n[7/{TOTAL_CHECKS}] Checking Forward Testing Harness...")
        logger.info("-" * 80)

        try:
            from coding.service.on_chain.forward_testing_harness import ForwardTestingHarness
            harness = ForwardTestingHarness(repository=self.repo)

            for currency in ["BTC", "ETH"]:
                record = harness.get_track_record(currency)
                n_total = record.get("n_total", 0)
                n_signals = record.get("n_signals", 0)
                hit_rate = record.get("hit_rate")
                ir = record.get("information_ratio")
                criteria_met = record.get("criteria_met", False)

                hit_rate_str = f"{hit_rate:.1%}" if hit_rate is not None else "n/a"
                ir_str = f"{ir:.3f}" if ir is not None else "n/a"

                logger.info(
                    f"  {currency}: {n_total} predictions total, {n_signals} directional, "
                    f"hit_rate={hit_rate_str}, IR={ir_str}, criteria_met={criteria_met}"
                )

                if n_total == 0:
                    self.results["warnings"].append(f"{currency}: no forward-test predictions recorded yet")
                elif criteria_met:
                    self.results["passed"].append(f"{currency}: forward-test criteria MET ({n_signals} signals)")
                else:
                    self.results["passed"].append(f"{currency}: forward-test harness recording ({n_total} predictions)")

        except Exception as e:
            logger.error(f"  Forward testing harness check failed: {e}")
            self.results["failed"].append(f"Forward testing harness check failed: {e}")

    def _check_historical_trades_quality(self):
        """Check historical_trades data quality (direction/IV - feeds flow analysis)."""
        logger.info(f"\n[8/{TOTAL_CHECKS}] Checking Historical Trades Data Quality...")
        logger.info("-" * 80)

        conn = self.repo._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("SELECT COUNT(*) FROM historical_trades")
            total = cursor.fetchone()[0]

            if total == 0:
                logger.error("  No historical trades")
                self.results["failed"].append("No historical trades")
                return

            logger.info(f"  Total Trades: {total:,}")

            cursor.execute("""
                SELECT
                    COUNT(*) as total,
                    COUNT(CASE WHEN direction IS NOT NULL THEN 1 END) as with_direction,
                    COUNT(CASE WHEN iv IS NOT NULL THEN 1 END) as with_iv
                FROM (
                    SELECT direction, iv
                    FROM historical_trades
                    ORDER BY trade_timestamp DESC
                    LIMIT 10000
                ) recent
            """)
            sample_total, with_direction, with_iv = cursor.fetchone()

            if sample_total > 0:
                direction_pct = (with_direction / sample_total) * 100
                iv_pct = (with_iv / sample_total) * 100

                logger.info(f"  Direction field (recent {sample_total:,}): {direction_pct:.1f}% populated")
                logger.info(f"  IV field (recent {sample_total:,}): {iv_pct:.1f}% populated")

                if direction_pct > 95:
                    self.results["passed"].append(f"Trade direction data quality ({direction_pct:.1f}%)")
                elif direction_pct > 80:
                    self.results["warnings"].append(f"Trade direction coverage only {direction_pct:.1f}%")
                else:
                    self.results["failed"].append(f"Trade direction coverage too low ({direction_pct:.1f}%)")

        finally:
            cursor.close()
            self.repo._return_connection(conn)

    def _check_ohlcv_history(self):
        """Check OHLCV history has data (feeds regime detection)."""
        logger.info(f"\n[9/{TOTAL_CHECKS}] Checking OHLCV History (regime detection input)...")
        logger.info("-" * 80)

        conn = self.repo._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT currency, COUNT(*), MIN(date)::date, MAX(date)::date
                FROM ohlcv_history
                GROUP BY currency
                ORDER BY currency
            """)
            rows = cursor.fetchall()

            if not rows:
                logger.error("  ohlcv_history: EMPTY")
                logger.error("  Run: python -m scripts.backfill_ohlcv")
                self.results["failed"].append(
                    "ohlcv_history is empty - regime detection features will be degraded. "
                    "Run: python -m scripts.backfill_ohlcv"
                )
                return

            all_ok = True
            for currency, count, earliest, latest in rows:
                days = (latest - earliest).days if latest and earliest else 0
                logger.info(f"  {currency}: {count} candles | {earliest} to {latest} ({days} days)")

                if count < 30:
                    logger.error(f"    {currency}: INSUFFICIENT (<30 candles)")
                    self.results["failed"].append(f"ohlcv_history {currency}: only {count} candles")
                    all_ok = False
                elif count < 180:
                    logger.warning(f"    {currency}: LIMITED (<180 candles)")
                    self.results["warnings"].append(f"ohlcv_history {currency}: only {count} candles (<6 months)")
                    all_ok = False

            if all_ok:
                total = sum(r[1] for r in rows)
                self.results["passed"].append(f"OHLCV History ({total} candles across {len(rows)} currencies)")

        finally:
            cursor.close()
            self.repo._return_connection(conn)

    def _check_backfill_coverage(self):
        """Check overall historical_trades backfill date-range coverage."""
        logger.info(f"\n[10/{TOTAL_CHECKS}] Checking Backfill Coverage...")
        logger.info("-" * 80)

        conn = self.repo._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("""
                SELECT MIN(trade_timestamp), MAX(trade_timestamp)
                FROM historical_trades
            """)
            result = cursor.fetchone()

            if result[0]:
                earliest = datetime.fromtimestamp(result[0] / 1000)
                latest = datetime.fromtimestamp(result[1] / 1000)
                days_coverage = (latest - earliest).days

                logger.info(f"  Coverage: {days_coverage} days")
                logger.info(f"    From: {earliest}")
                logger.info(f"    To: {latest}")

                if days_coverage > 30:
                    self.results["passed"].append(f"Backfill Coverage ({days_coverage} days)")
                else:
                    self.results["warnings"].append(f"Limited backfill ({days_coverage} days)")
            else:
                logger.warning("  No backfill data")
                self.results["warnings"].append("No backfill data")

        finally:
            cursor.close()
            self.repo._return_connection(conn)

    def _print_summary(self):
        """Print validation summary."""
        logger.info("\n" + "=" * 80)
        logger.info("VALIDATION SUMMARY")
        logger.info("=" * 80)

        total_checks = len(self.results["passed"]) + len(self.results["warnings"]) + len(self.results["failed"])

        logger.info(f"\nTotal Checks: {total_checks}")
        logger.info(f"  Passed: {len(self.results['passed'])}")
        logger.info(f"  Warnings: {len(self.results['warnings'])}")
        logger.info(f"  Failed: {len(self.results['failed'])}")

        if self.results["passed"]:
            logger.info(f"\nPASSED ({len(self.results['passed'])}):")
            for item in self.results["passed"]:
                logger.info(f"  - {item}")

        if self.results["warnings"]:
            logger.info(f"\nWARNINGS ({len(self.results['warnings'])}):")
            for item in self.results["warnings"]:
                logger.warning(f"  - {item}")

        if self.results["failed"]:
            logger.info(f"\nFAILED ({len(self.results['failed'])}):")
            for item in self.results["failed"]:
                logger.error(f"  - {item}")

        logger.info("\n" + "=" * 80)

        if not self.results["failed"]:
            if not self.results["warnings"]:
                logger.info("OVERALL STATUS: ALL SYSTEMS OPERATIONAL")
            else:
                logger.info("OVERALL STATUS: OPERATIONAL WITH WARNINGS")
        else:
            logger.info("OVERALL STATUS: SYSTEM HAS CRITICAL ISSUES")

        logger.info("=" * 80)


def main():
    """Run system validation."""
    validator = SystemValidator()
    results = validator.validate_all()

    if results["failed"]:
        exit(1)


if __name__ == "__main__":
    main()
