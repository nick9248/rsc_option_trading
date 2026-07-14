"""
Data Collection Daemon - Auto-Start Service

ADAPTIVE DESIGN:
- Starts automatically when PC boots (via Task Scheduler)
- Runs continuously while system is ON
- Collects every 30 minutes regardless of start time
- Handles gaps intelligently
- Stops gracefully on shutdown

Nobel-level architecture:
- Self-healing (restarts on crash)
- Adaptive scheduling (works with any boot time)
- Comprehensive logging
- Graceful shutdown handling
"""

import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# APScheduler for robust scheduling
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from coding.core.config import SUPPORTED_CURRENCIES
from coding.core.logging.logging_setup import init_logging
from coding.service.data_collection.prospective_collector import ProspectiveCollector


logger = logging.getLogger(__name__)


class CollectionDaemon:
    """
    Long-running data collection daemon.

    Adaptive features:
    - Auto-starts on system boot
    - Collects every 30 minutes while running
    - Handles startup gaps intelligently
    - Graceful shutdown on system stop
    - Self-healing on errors
    """

    # Bounded grace period (seconds) given to an in-flight collection cycle
    # to finish naturally during shutdown before we force-terminate the
    # process. See _shutdown() for why a hard-exit fallback is necessary here.
    SHUTDOWN_GRACE_SECONDS = 60

    def __init__(
        self,
        collection_interval_minutes: int = 30,
        currencies: list = None
    ):
        """
        Initialize daemon.

        Args:
            collection_interval_minutes: How often to collect (default: 30 min)
            currencies: Currencies to collect (default: BTC, ETH)
        """
        self.collection_interval = collection_interval_minutes
        self.currencies = currencies or SUPPORTED_CURRENCIES
        self.collector = ProspectiveCollector()
        self.scheduler = BackgroundScheduler()
        self.is_running = False

        # Track collection stats
        self.stats = {
            "daemon_started": None,
            "collections_run": 0,
            "collections_success": 0,
            "collections_failed": 0,
            "last_collection": None,
            "next_collection": None
        }

        # Setup graceful shutdown
        signal.signal(signal.SIGINT, self._shutdown_handler)
        signal.signal(signal.SIGTERM, self._shutdown_handler)

    def start(self):
        """
        Start the collection daemon.

        Adaptive startup sequence:
        1. Check for gaps since last run
        2. Run initial collection NOW
        3. Schedule recurring collections every 30 minutes
        4. Keep running until shutdown
        """
        self.stats["daemon_started"] = datetime.now()

        logger.info(f"{'='*60}")
        logger.info(f"COLLECTION DAEMON STARTING")
        logger.info(f"{'='*60}")
        logger.info(f"Start time: {self.stats['daemon_started']}")
        logger.info(f"Collection interval: {self.collection_interval} minutes")
        logger.info(f"Currencies: {self.currencies}")
        logger.info(f"{'='*60}\n")

        # Step 1: Handle startup (check for gaps)
        self._handle_startup()

        # Step 2: Run initial collection NOW
        logger.info(f"Running INITIAL collection (on startup)...")
        self._run_collection()

        # Step 3: Schedule recurring collections
        logger.info(f"\nScheduling collections every {self.collection_interval} minutes...")

        self.scheduler.add_job(
            func=self._run_collection,
            trigger=IntervalTrigger(minutes=self.collection_interval),
            id='recurring_collection',
            name='Recurring Data Collection',
            replace_existing=True,
            max_instances=1  # Don't run concurrent collections
        )

        # Start scheduler
        self.scheduler.start()
        self.is_running = True

        # Get next scheduled time
        next_job = self.scheduler.get_job('recurring_collection')
        if next_job:
            self.stats["next_collection"] = next_job.next_run_time
            logger.info(f"[OK] Scheduler started")
            logger.info(f"   Next collection: {self.stats['next_collection']}")

        # Step 4: Keep running
        self._keep_alive()

    def _handle_startup(self):
        """
        Handle startup intelligently.

        Check for gaps since last run:
        - If gap < 1.5 hours: Attempt backfill
        - If gap > 1.5 hours: Log gap, start fresh
        """
        logger.info("Checking for gaps since last run...")

        try:
            # Get last collection time from database
            last_collection = self._get_last_collection_time()

            if last_collection:
                gap = datetime.now() - last_collection
                gap_hours = gap.total_seconds() / 3600

                logger.info(f"  Last collection: {last_collection}")
                logger.info(f"  Current time: {datetime.now()}")
                logger.info(f"  Gap: {gap_hours:.2f} hours")

                if gap_hours <= 1.5:
                    logger.info(f"  [OK] Gap is small ({gap_hours:.2f}h) - attempting backfill...")
                    self._backfill_gap(last_collection, datetime.now())
                else:
                    logger.warning(f"  [WARN] Gap is large ({gap_hours:.2f}h) - cannot backfill")
                    logger.warning(f"      API only provides 1.5h lookback")
                    logger.warning(f"      Accepting gap and starting fresh")

                    # Log gap to database for tracking
                    self._log_gap(last_collection, datetime.now(), gap_hours)
            else:
                logger.info(f"  No previous collection found - this is first run")

        except Exception as e:
            logger.error(f"  Error checking gaps: {e}")
            logger.info(f"  Proceeding with fresh collection")

    def _get_last_collection_time(self) -> Optional[datetime]:
        """
        Get timestamp of last successful collection from database.

        Returns:
            Datetime of last collection, or None if no previous runs
        """
        try:
            # Get most recent trade timestamp (most reliable indicator)
            conn = self.collector.repo._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                SELECT MAX(TO_TIMESTAMP(trade_timestamp / 1000.0))
                FROM historical_trades
            """)

            result = cursor.fetchone()
            cursor.close()
            self.collector.repo._return_connection(conn)

            return result[0] if result and result[0] else None

        except Exception as e:
            logger.error(f"Error getting last collection time: {e}")
            return None

    def _backfill_gap(self, start_time: datetime, end_time: datetime):
        """
        Attempt to backfill gap by fetching recent trades.

        Only works if gap < 1.5 hours (API limitation).

        Args:
            start_time: Start of gap
            end_time: End of gap
        """
        logger.info(f"  Attempting backfill from {start_time} to {end_time}...")

        try:
            # Calculate hours to backfill
            hours_to_fill = []
            current = start_time.replace(minute=0, second=0, microsecond=0)
            end = end_time.replace(minute=0, second=0, microsecond=0)

            while current <= end:
                hours_to_fill.append(current)
                current += timedelta(hours=1)

            logger.info(f"  Need to backfill {len(hours_to_fill)} hours")

            # Backfill each hour
            total_trades = 0
            total_instruments = 0

            for hour in hours_to_fill:
                logger.info(f"    Backfilling hour: {hour}")
                result = self.collector.collect_hour(
                    currencies=self.currencies,
                    hour=hour
                )

                total_trades += result.get("trades_collected", 0)
                total_instruments += result.get("instruments_collected", 0)

            logger.info(f"  Backfill complete:")
            logger.info(f"    Hours filled: {len(hours_to_fill)}")
            logger.info(f"    Trades: {total_trades}")
            logger.info(f"    Instruments: {total_instruments}")

        except Exception as e:
            logger.exception(f"  Backfill failed: {e}")

    def _log_gap(self, start_time: datetime, end_time: datetime, gap_hours: float):
        """
        Log data gap to collection_logs table for quality tracking.

        Args:
            start_time: Gap start
            end_time: Gap end
            gap_hours: Gap duration in hours
        """
        try:
            self.collector.repo.save_collection_log(
                collection_hour=start_time.replace(minute=0, second=0, microsecond=0),
                status="gap_detected",
                currencies_collected=self.currencies,
                trades_collected=0,
                instruments_collected=0,
                greeks_calculated=0,
                duration_seconds=0.0,
                error_message=(
                    f"Data gap: {gap_hours:.2f}h from {start_time} to {end_time}. "
                    f"Exceeds API lookback (1.5h), cannot backfill."
                ),
                error_count=0
            )
            logger.info(f"  Gap logged to collection_logs ({gap_hours:.2f}h)")

        except Exception as e:
            logger.error(f"  Failed to log gap: {e}")

    def _run_collection(self):
        """
        Run a single collection cycle.

        Wrapped with error handling to prevent daemon crash.
        """
        collection_start = datetime.now()

        logger.info(f"\n{'='*60}")
        logger.info(f"COLLECTION CYCLE STARTED")
        logger.info(f"{'='*60}")
        logger.info(f"Time: {collection_start}")
        logger.info(f"Cycle: #{self.stats['collections_run'] + 1}")

        result = None
        try:
            # Run collection
            result = self.collector.collect_hour(currencies=self.currencies)

            # Update stats
            self.stats["collections_run"] += 1
            self.stats["last_collection"] = collection_start

            if result["status"] == "success":
                self.stats["collections_success"] += 1
                logger.info(f"[SUCCESS] Collection SUCCESSFUL")
            elif result["status"] == "partial":
                self.stats["collections_success"] += 1
                logger.warning(f"[PARTIAL] Collection PARTIAL (some errors)")
            else:
                self.stats["collections_failed"] += 1
                logger.error(f"[FAILED] Collection FAILED")

            # Log result details
            logger.info(f"   Trades: {result.get('trades_collected', 0)}")
            logger.info(f"   Instruments: {result.get('instruments_collected', 0)}")
            logger.info(f"   Duration: {result.get('duration_seconds', 0)}s")

            if result.get("errors"):
                logger.warning(f"   Errors: {len(result['errors'])}")

        except Exception as e:
            logger.exception(f"[CRASHED] Collection CRASHED: {e}")

            self.stats["collections_run"] += 1
            self.stats["collections_failed"] += 1

        # Write to collection_logs table
        try:
            hour = collection_start.replace(minute=0, second=0, microsecond=0)
            if result:
                agg_data = result.get("aggregation", {})
                self.collector.repo.save_collection_log(
                    collection_hour=hour,
                    status=result["status"],
                    currencies_collected=self.currencies,
                    trades_collected=result.get("trades_collected", 0),
                    instruments_collected=result.get("instruments_collected", 0),
                    greeks_calculated=agg_data.get("snapshots_created", 0),
                    duration_seconds=result.get("duration_seconds", 0),
                    error_message="; ".join(result.get("errors", [])) or None,
                    error_count=len(result.get("errors", []))
                )
            else:
                self.collector.repo.save_collection_log(
                    collection_hour=hour,
                    status="failed",
                    currencies_collected=self.currencies,
                    trades_collected=0,
                    instruments_collected=0,
                    greeks_calculated=0,
                    duration_seconds=(time.time() - collection_start.timestamp()),
                    error_message="Collection crashed before returning result",
                    error_count=1
                )
        except Exception as log_err:
            logger.error(f"Failed to write collection log: {log_err}")

        # Calculate next collection time
        next_job = self.scheduler.get_job('recurring_collection')
        if next_job:
            self.stats["next_collection"] = next_job.next_run_time
            logger.info(f"   Next collection: {self.stats['next_collection']}")

        logger.info(f"{'='*60}\n")

    def _keep_alive(self):
        """
        Keep daemon running until shutdown signal.

        Adaptive design: Runs forever until:
        - User presses Ctrl+C
        - System shutdown
        - SIGTERM signal
        """
        logger.info(f"\n{'='*60}")
        logger.info(f"DAEMON RUNNING")
        logger.info(f"{'='*60}")
        logger.info(f"Press Ctrl+C to stop gracefully")
        logger.info(f"Or wait for system shutdown\n")

        try:
            while self.is_running:
                # Print status every 5 minutes
                time.sleep(300)  # 5 minutes
                self._print_status()

        except KeyboardInterrupt:
            logger.info(f"\nKeyboard interrupt received - shutting down...")
            self._shutdown()

    def _print_status(self):
        """Print daemon status (heartbeat)."""
        logger.info(f"\n--- DAEMON STATUS ---")
        logger.info(f"Running since: {self.stats['daemon_started']}")
        logger.info(f"Collections run: {self.stats['collections_run']}")
        logger.info(f"  Success: {self.stats['collections_success']}")
        logger.info(f"  Failed: {self.stats['collections_failed']}")
        logger.info(f"Last collection: {self.stats['last_collection']}")
        logger.info(f"Next collection: {self.stats['next_collection']}")
        logger.info(f"---------------------\n")

    def _shutdown_handler(self, signum, frame):
        """Handle shutdown signals (SIGINT, SIGTERM)."""
        logger.info(f"\nShutdown signal received (signal {signum})")
        self._shutdown()

    def _shutdown(self):
        """
        Graceful shutdown sequence.

        Root cause of the historical shutdown hang (required `kill -9` on the
        VPS despite this method completing and logging "Shutdown complete.
        Goodbye!"): APScheduler's default executor runs jobs on a
        `concurrent.futures.ThreadPoolExecutor`. That module registers a
        process-wide, UNCONDITIONAL, no-timeout thread-join
        (`threading._register_atexit(_python_exit)` in
        Lib/concurrent/futures/thread.py) that runs during interpreter
        finalization -- it joins every worker thread the pool has ever
        spawned, regardless of the `wait` flag passed to
        `scheduler.shutdown()` and regardless of the worker thread's daemon
        flag. If a collection cycle (`_run_collection`) is still executing
        in that worker thread when SIGTERM arrives, `sys.exit(0)` below
        raises SystemExit and unwinds the main thread fine, but the process
        does not actually terminate until the in-flight job finishes --
        potentially indefinitely if it is blocked on a slow/stuck network or
        DB call. Confirmed empirically: a scheduled job sleeping 8s caused
        the process to hang ~8s past `sys.exit(0)` even with
        `scheduler.shutdown(wait=False)`.
        """
        logger.info(f"\n{'='*60}")
        logger.info(f"DAEMON SHUTTING DOWN")
        logger.info(f"{'='*60}")

        # Stop scheduler
        if self.scheduler.running:
            logger.info(f"Stopping scheduler...")
            self.scheduler.shutdown(wait=False)

        # Final status
        self._print_status()

        # Mark as stopped
        self.is_running = False

        logger.info(f"Shutdown complete. Goodbye!")
        logger.info(f"{'='*60}\n")

        # Give any in-flight collection job a bounded grace period to finish
        # naturally (this is the "proper" shutdown path -- scheduler.shutdown()
        # above already stopped new jobs from being scheduled). If it hasn't
        # finished within SHUTDOWN_GRACE_SECONDS, force-terminate at the OS
        # level: os._exit() does not run atexit handlers and does not wait on
        # any thread, so it bypasses concurrent.futures.thread's unconditional
        # atexit join described above. This watchdog thread is a plain
        # threading.Timer (not a ThreadPoolExecutor worker), so it is exempt
        # from that same atexit join and fires independently even while the
        # main thread is stuck waiting on it.
        watchdog = threading.Timer(self.SHUTDOWN_GRACE_SECONDS, os._exit, args=(0,))
        watchdog.daemon = True
        watchdog.start()

        sys.exit(0)


# ============================================================
# Entry point for Task Scheduler / Startup
# ============================================================

def main():
    """
    Main entry point for daemon.

    This is what Task Scheduler will call on system startup.
    """
    # Initialize logging
    init_logging(
        level="INFO",
        task_name="collection_daemon",
        log_to_file=True
    )

    logger.info(f"\n{'='*60}")
    logger.info(f"DATA COLLECTION DAEMON")
    logger.info(f"{'='*60}")
    logger.info(f"System startup detected - initializing daemon...")
    logger.info(f"Boot time: {datetime.now()}")
    logger.info(f"{'='*60}\n")

    # Create and start daemon
    daemon = CollectionDaemon(
        collection_interval_minutes=30,
        currencies=SUPPORTED_CURRENCIES
    )

    daemon.start()


if __name__ == "__main__":
    main()
