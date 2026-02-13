"""
Comprehensive System Validator

Validates EVERY module in the option trading pipeline:
- API connectivity and authentication
- Database tables and schema
- Data collection daemon status
- Backfill status
- Data quality and completeness
- Prospective collection
- Historical trades
- Hourly snapshots
- ML data pipeline

Run this before claiming "everything works".
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Tuple
from coding.core.database.repository import DatabaseRepository
from coding.core.logging.logging_setup import init_logging

init_logging(level="INFO")
logger = logging.getLogger(__name__)


class SystemValidator:
    """Comprehensive system health checker."""

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
        logger.info("COMPREHENSIVE SYSTEM VALIDATION")
        logger.info("=" * 80)
        logger.info(f"Started: {datetime.now()}\n")

        # Run all checks
        self._check_api_connectivity()
        self._check_database_connection()
        self._check_required_tables()
        self._check_collection_daemon()
        self._check_trade_collector()  # NEW: Check trade collector daemon
        self._check_data_freshness()
        self._check_historical_trades()
        self._check_hourly_snapshots()
        self._check_backfill_status()
        self._check_data_quality()
        self._check_prospective_collection()
        self._check_ml_pipeline()

        # Print summary
        self._print_summary()

        return self.results

    def _check_api_connectivity(self):
        """Check Deribit API connectivity."""
        logger.info("\n[1/12] Checking API Connectivity...")
        logger.info("-" * 80)

        try:
            from coding.service.deribit.deribit_api_service import DeribitApiService
            api = DeribitApiService()

            # Test public endpoint
            response = api.get_ticker("BTC-PERPETUAL")

            if response and "index_price" in response:
                btc_price = response["index_price"]
                logger.info(f"  API Status: CONNECTED")
                logger.info(f"  BTC Price: ${btc_price:,.2f}")
                self.results["passed"].append("API Connectivity")
            else:
                raise Exception("Invalid API response")

        except Exception as e:
            logger.error(f"  API Status: FAILED - {e}")
            self.results["failed"].append(f"API Connectivity: {e}")

    def _check_database_connection(self):
        """Check database connection."""
        logger.info("\n[2/12] Checking Database Connection...")
        logger.info("-" * 80)

        try:
            conn = self.repo._get_connection()
            cursor = conn.cursor()

            cursor.execute("SELECT version()")
            version = cursor.fetchone()[0]

            logger.info(f"  Database: CONNECTED")
            logger.info(f"  PostgreSQL: {version.split(',')[0]}")

            cursor.close()
            self.repo._return_connection(conn)

            self.results["passed"].append("Database Connection")

        except Exception as e:
            logger.error(f"  Database: FAILED - {e}")
            self.results["failed"].append(f"Database Connection: {e}")

    def _check_required_tables(self):
        """Check all required tables exist."""
        logger.info("\n[3/12] Checking Required Tables...")
        logger.info("-" * 80)

        required_tables = [
            "snapshots",
            "max_pain",
            "open_interest",
            "volume",
            "gex_dex",
            "levels",
            "historical_trades",
            "hourly_snapshots",
            "latest_hourly_snapshots",
            "strategy_signals"
        ]

        conn = self.repo._get_connection()
        cursor = conn.cursor()

        try:
            missing_tables = []
            for table in required_tables:
                cursor.execute(f"""
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_name = '{table}'
                    )
                """)
                exists = cursor.fetchone()[0]

                if exists:
                    logger.info(f"  {table}: EXISTS")
                else:
                    logger.error(f"  {table}: MISSING")
                    missing_tables.append(table)

            if missing_tables:
                self.results["failed"].append(f"Missing tables: {', '.join(missing_tables)}")
            else:
                self.results["passed"].append("All Required Tables")

        finally:
            cursor.close()
            self.repo._return_connection(conn)

    def _check_collection_daemon(self):
        """Check if collection daemon is running."""
        logger.info("\n[4/12] Checking Collection Daemon...")
        logger.info("-" * 80)

        import os
        from pathlib import Path

        try:
            # Check for recent log file
            import glob

            # Use absolute path relative to script location
            project_root = Path(__file__).parent.parent
            log_pattern = str(project_root / "output" / "log" / "collection_daemon_????????_??????.log")
            log_files = glob.glob(log_pattern)

            if not log_files:
                logger.warning("  Daemon Logs: NOT FOUND")
                self.results["warnings"].append("No daemon logs found")
                return

            # Read most recent log
            latest_log = max(log_files, key=os.path.getmtime)
            mod_time = datetime.fromtimestamp(os.path.getmtime(latest_log))
            hours_ago = (datetime.now() - mod_time).total_seconds() / 3600

            if hours_ago < 0.25:  # Modified in last 15 minutes
                logger.info(f"  Daemon Status: ACTIVE")
                logger.info(f"  Last Activity: {mod_time} ({hours_ago*60:.0f} min ago)")
                self.results["passed"].append("Collection Daemon Running")
            elif hours_ago < 2:
                logger.warning(f"  Daemon Status: POSSIBLY INACTIVE")
                logger.warning(f"  Last Activity: {hours_ago:.1f} hours ago")
                self.results["warnings"].append(f"Daemon may be inactive ({hours_ago:.1f}h since last log)")
            else:
                logger.error(f"  Daemon Status: STOPPED")
                logger.error(f"  Last Activity: {hours_ago:.1f} hours ago")
                self.results["failed"].append(f"Daemon stopped ({hours_ago:.1f}h ago)")

        except Exception as e:
            logger.error(f"  Error checking daemon: {e}")
            self.results["failed"].append(f"Daemon check failed: {e}")

    def _check_trade_collector(self):
        """Check if trade collector is running and collecting data."""
        logger.info("\n[5/12] Checking Trade Collector...")
        logger.info("-" * 80)

        try:
            conn = self.repo._get_connection()
            cursor = conn.cursor()

            # Check for recent trade collection activity (last 5 minutes)
            cursor.execute("""
                SELECT
                    COUNT(*) as trade_count,
                    MAX(trade_timestamp) as latest_trade,
                    COUNT(DISTINCT currency) as currencies
                FROM historical_trades
                WHERE trade_timestamp >= EXTRACT(EPOCH FROM NOW() - INTERVAL '5 minutes') * 1000
            """)

            result = cursor.fetchone()
            trade_count = result[0]
            latest_trade_ts = result[1]
            currencies_count = result[2]

            cursor.close()
            self.repo._return_connection(conn)

            if latest_trade_ts:
                latest_trade = datetime.fromtimestamp(latest_trade_ts / 1000)
                minutes_ago = (datetime.now() - latest_trade).total_seconds() / 60

                logger.info(f"  Trade Collector Status:")
                logger.info(f"    Trades (last 5 min): {trade_count:,}")
                logger.info(f"    Currencies active: {currencies_count}")
                logger.info(f"    Latest trade: {latest_trade} ({minutes_ago:.1f} min ago)")

                if minutes_ago < 2:
                    logger.info(f"  Status: ACTIVE")
                    self.results["passed"].append(f"Trade Collector Active ({trade_count} trades/5min)")
                elif minutes_ago < 5:
                    logger.warning(f"  Status: POSSIBLY INACTIVE (last trade {minutes_ago:.1f} min ago)")
                    self.results["warnings"].append(f"Trade collector may be inactive ({minutes_ago:.1f} min)")
                else:
                    logger.error(f"  Status: STOPPED (last trade {minutes_ago:.1f} min ago)")
                    self.results["failed"].append(f"Trade collector stopped ({minutes_ago:.1f} min ago)")

            else:
                logger.error(f"  Status: NO RECENT TRADES (last 5 minutes)")
                logger.warning(f"  Trade collector may not be running")
                self.results["warnings"].append("No trades collected in last 5 minutes")

            # Check for trade direction field (critical for flow-based GEX)
            conn = self.repo._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    COUNT(*) as total,
                    COUNT(CASE WHEN direction IS NOT NULL THEN 1 END) as with_direction,
                    COUNT(CASE WHEN iv IS NOT NULL THEN 1 END) as with_iv
                FROM historical_trades
                LIMIT 10000
            """)

            result = cursor.fetchone()
            total = result[0]
            with_direction = result[1]
            with_iv = result[2]

            cursor.close()
            self.repo._return_connection(conn)

            if total > 0:
                direction_pct = (with_direction / total) * 100
                iv_pct = (with_iv / total) * 100

                logger.info(f"\n  Data Quality:")
                logger.info(f"    Direction field: {direction_pct:.1f}% populated")
                logger.info(f"    IV field: {iv_pct:.1f}% populated")

                if direction_pct > 95:
                    self.results["passed"].append(f"Trade direction data quality ({direction_pct:.1f}%)")
                elif direction_pct > 80:
                    self.results["warnings"].append(f"Trade direction coverage only {direction_pct:.1f}%")
                else:
                    self.results["failed"].append(f"Trade direction coverage too low ({direction_pct:.1f}%)")

        except Exception as e:
            logger.error(f"  Error checking trade collector: {e}")
            self.results["failed"].append(f"Trade collector check failed: {e}")

    def _check_data_freshness(self):
        """Check if data is fresh (recently collected)."""
        logger.info("\n[6/12] Checking Data Freshness...")
        logger.info("-" * 80)

        tables_to_check = {
            "historical_trades": "trade_timestamp",
            "snapshots": "captured_at",
            "hourly_snapshots": "captured_at"
        }

        conn = self.repo._get_connection()
        cursor = conn.cursor()

        try:
            for table, time_col in tables_to_check.items():
                cursor.execute(f"SELECT MAX({time_col}) FROM {table}")
                latest = cursor.fetchone()[0]

                if latest:
                    # Handle unix timestamp (milliseconds)
                    if isinstance(latest, int):
                        latest = datetime.fromtimestamp(latest / 1000)

                    hours_ago = (datetime.now() - latest).total_seconds() / 3600

                    if hours_ago < 1:
                        logger.info(f"  {table}: FRESH ({hours_ago*60:.0f} min ago)")
                        self.results["passed"].append(f"{table} is fresh")
                    elif hours_ago < 24:
                        logger.warning(f"  {table}: STALE ({hours_ago:.1f} hours ago)")
                        self.results["warnings"].append(f"{table} is {hours_ago:.1f}h old")
                    else:
                        logger.error(f"  {table}: VERY STALE ({hours_ago/24:.1f} days ago)")
                        self.results["failed"].append(f"{table} is {hours_ago/24:.1f} days old")
                else:
                    logger.error(f"  {table}: NO DATA")
                    self.results["failed"].append(f"{table} has no data")

        finally:
            cursor.close()
            self.repo._return_connection(conn)

    def _check_historical_trades(self):
        """Check historical trades collection."""
        logger.info("\n[7/12] Checking Historical Trades...")
        logger.info("-" * 80)

        conn = self.repo._get_connection()
        cursor = conn.cursor()

        try:
            # Total trades
            cursor.execute("SELECT COUNT(*) FROM historical_trades")
            total = cursor.fetchone()[0]

            # Trades by currency
            cursor.execute("""
                SELECT currency, COUNT(*)
                FROM historical_trades
                GROUP BY currency
            """)
            by_currency = dict(cursor.fetchall())

            logger.info(f"  Total Trades: {total:,}")
            for currency, count in by_currency.items():
                logger.info(f"    {currency}: {count:,}")

            if total > 0:
                self.results["passed"].append(f"Historical Trades ({total:,} records)")
            else:
                self.results["failed"].append("No historical trades")

        finally:
            cursor.close()
            self.repo._return_connection(conn)

    def _check_hourly_snapshots(self):
        """Check hourly snapshots."""
        logger.info("\n[8/12] Checking Hourly Snapshots...")
        logger.info("-" * 80)

        conn = self.repo._get_connection()
        cursor = conn.cursor()

        try:
            # Total snapshots
            cursor.execute("SELECT COUNT(*) FROM hourly_snapshots")
            total = cursor.fetchone()[0]

            # Snapshots by currency
            cursor.execute("""
                SELECT currency, COUNT(DISTINCT snapshot_hour)
                FROM hourly_snapshots
                GROUP BY currency
            """)
            by_currency = dict(cursor.fetchall())

            logger.info(f"  Total Snapshots: {total:,}")
            for currency, hours in by_currency.items():
                logger.info(f"    {currency}: {hours} hours")

            if total > 0:
                self.results["passed"].append(f"Hourly Snapshots ({total:,} records)")
            else:
                self.results["warnings"].append("No hourly snapshots (run aggregation)")

        finally:
            cursor.close()
            self.repo._return_connection(conn)

    def _check_backfill_status(self):
        """Check backfill coverage."""
        logger.info("\n[9/12] Checking Backfill Status...")
        logger.info("-" * 80)

        conn = self.repo._get_connection()
        cursor = conn.cursor()

        try:
            # Get date range of historical trades
            cursor.execute("""
                SELECT
                    MIN(trade_timestamp) as earliest,
                    MAX(trade_timestamp) as latest
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

    def _check_data_quality(self):
        """Check data quality metrics."""
        logger.info("\n[10/12] Checking Data Quality...")
        logger.info("-" * 80)

        conn = self.repo._get_connection()
        cursor = conn.cursor()

        try:
            # Check for null IVs in trades
            cursor.execute("""
                SELECT
                    COUNT(*) as total,
                    COUNT(iv) as with_iv,
                    COUNT(*) - COUNT(iv) as missing_iv
                FROM historical_trades
            """)
            total, with_iv, missing_iv = cursor.fetchone()

            if total > 0:
                iv_completeness = (with_iv / total) * 100
                logger.info(f"  IV Completeness: {iv_completeness:.1f}%")

                if iv_completeness > 95:
                    self.results["passed"].append(f"Data Quality ({iv_completeness:.1f}% IV coverage)")
                elif iv_completeness > 80:
                    self.results["warnings"].append(f"IV coverage {iv_completeness:.1f}%")
                else:
                    self.results["failed"].append(f"Low IV coverage ({iv_completeness:.1f}%)")

        finally:
            cursor.close()
            self.repo._return_connection(conn)

    def _check_prospective_collection(self):
        """Check prospective collection (real-time)."""
        logger.info("\n[11/12] Checking Prospective Collection...")
        logger.info("-" * 80)

        conn = self.repo._get_connection()
        cursor = conn.cursor()

        try:
            # Check trades in last hour
            one_hour_ago = int((datetime.now() - timedelta(hours=1)).timestamp() * 1000)

            cursor.execute("""
                SELECT COUNT(*)
                FROM historical_trades
                WHERE trade_timestamp >= %s
            """, (one_hour_ago,))

            recent_trades = cursor.fetchone()[0]

            logger.info(f"  Trades (last hour): {recent_trades}")

            if recent_trades > 0:
                self.results["passed"].append(f"Prospective Collection ({recent_trades} trades/hour)")
            else:
                logger.warning("  No trades in last hour (market closed or collection stopped)")
                self.results["warnings"].append("No recent prospective trades")

        finally:
            cursor.close()
            self.repo._return_connection(conn)

    def _check_ml_pipeline(self):
        """Check ML models and data pipeline readiness."""
        logger.info("\n[12/12] Checking ML Pipeline...")
        logger.info("-" * 80)

        import os
        import glob
        from pathlib import Path

        try:
            # Check for trained models (use absolute path)
            project_root = Path(__file__).parent.parent
            models_dir = project_root / "models"

            if not models_dir.exists():
                logger.warning("  Models directory: NOT FOUND")
                self.results["warnings"].append("No ML models directory")
                return

            # Find regime detection models (direct subdirectories)
            regime_models = [d for d in models_dir.glob("*market_regime*") if d.is_dir()]
            vol_models = [d for d in models_dir.glob("*realized_vol*") if d.is_dir()]

            logger.info(f"  Trained Models:")
            logger.info(f"    Market Regime: {len(regime_models)} models")
            logger.info(f"    Realized Vol: {len(vol_models)} models")

            if regime_models or vol_models:
                # Check if we have enough data for predictions
                conn = self.repo._get_connection()
                cursor = conn.cursor()

                try:
                    # Check hourly snapshots coverage
                    cursor.execute("""
                        SELECT
                            currency,
                            COUNT(DISTINCT snapshot_hour) as hours_available,
                            MIN(snapshot_hour) as earliest,
                            MAX(snapshot_hour) as latest
                        FROM hourly_snapshots
                        GROUP BY currency
                    """)

                    results = cursor.fetchall()

                    ml_ready_for_inference = True
                    ml_ready_for_training = True

                    for currency, hours, earliest, latest in results:
                        logger.info(f"  {currency} Data Coverage:")
                        logger.info(f"    Hours: {hours}")
                        logger.info(f"    Days: {hours/24:.1f}")
                        logger.info(f"    From: {earliest}")
                        logger.info(f"    To: {latest}")

                        # Data requirements:
                        # - Minimum 48h for basic inference
                        # - Recommended 720h (30 days) for production training
                        if hours < 48:
                            logger.error(f"    Status: INSUFFICIENT - Cannot run inference ({hours}h < 48h)")
                            self.results["failed"].append(f"{currency}: Insufficient data for ML inference ({hours}h)")
                            ml_ready_for_inference = False
                            ml_ready_for_training = False
                        elif hours < 720:  # Less than 30 days
                            logger.warning(f"    Status: LIMITED - Can infer but insufficient for training ({hours}h < 720h)")
                            logger.warning(f"    Need {720-hours} more hours ({(720-hours)/24:.1f} days) for production training")
                            self.results["warnings"].append(f"{currency}: Insufficient for production training ({hours}h < 30 days)")
                            ml_ready_for_training = False
                        else:
                            logger.info(f"    Status: SUFFICIENT - Ready for training and inference")

                    # Summarize ML readiness
                    total_models = len(regime_models) + len(vol_models)
                    if ml_ready_for_training and total_models > 0:
                        self.results["passed"].append(f"ML Pipeline PRODUCTION READY ({total_models} models, sufficient training data)")
                    elif ml_ready_for_inference and total_models > 0:
                        self.results["warnings"].append(f"ML Pipeline ({total_models} models, can infer but need more data for training)")
                    elif ml_ready_for_inference:
                        self.results["warnings"].append("ML data sufficient for inference but no models trained")
                    elif total_models > 0:
                        self.results["failed"].append(f"ML models exist ({total_models}) but insufficient data for inference")

                finally:
                    cursor.close()
                    self.repo._return_connection(conn)

            else:
                logger.info("  No trained models found")

                # Check if we have enough data to start training
                conn = self.repo._get_connection()
                cursor = conn.cursor()
                try:
                    cursor.execute("""
                        SELECT currency, COUNT(DISTINCT snapshot_hour)
                        FROM hourly_snapshots
                        GROUP BY currency
                    """)
                    for currency, hours in cursor.fetchall():
                        if hours >= 720:
                            logger.info(f"  {currency}: {hours}h available - Ready for training")
                        else:
                            logger.warning(f"  {currency}: {hours}h available - Need {720-hours}h more for training")
                finally:
                    cursor.close()
                    self.repo._return_connection(conn)

                self.results["warnings"].append("No ML models trained yet")

            # Check if ML modules are importable
            try:
                from coding.core.ml.feature_engineering import FeatureEngineer
                logger.info("  ML Core Modules: Importable")
            except ImportError as e:
                logger.warning(f"  ML Core Modules: Import failed - {e}")
                self.results["warnings"].append(f"ML core modules not importable: {e}")

            # Check for inference service (optional)
            try:
                from coding.service.ml.inference_service import InferenceService
                logger.info("  ML Inference Service: Available")
            except ImportError:
                logger.info("  ML Inference Service: Not available (optional)")

        except Exception as e:
            logger.error(f"  ML Pipeline check failed: {e}")
            self.results["failed"].append(f"ML Pipeline check error: {e}")

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

        # Overall status
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

    # Exit with error code if failed
    if results["failed"]:
        exit(1)


if __name__ == "__main__":
    main()
