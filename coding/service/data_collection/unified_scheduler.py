"""
Unified Task Scheduler - Coordinates All Data Collection

MODULAR ARCHITECTURE:
- Runs ProspectiveCollector (existing, every 30 min)
- Runs TradeCollector (new, every 60 sec)
- Keeps collectors independent and modular
- Single process to manage
- Unified health monitoring

This is the master scheduler that starts with your PC.
"""

import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

# APScheduler for robust scheduling
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from coding.core.config import SUPPORTED_CURRENCIES
from coding.core.logging.logging_setup import init_logging
from coding.service.data_collection.prospective_collector import ProspectiveCollector
from coding.service.data_collection.trade_collector import TradeCollector
from coding.core.database.repository import DatabaseRepository

logger = logging.getLogger(__name__)


class UnifiedScheduler:
    """
    Master scheduler coordinating all data collection tasks.

    Manages:
    - ProspectiveCollector: Every 30 minutes (hourly snapshots, greeks)
    - TradeCollector: Every 60 seconds (trade-level flow data)

    Architecture:
    - Single process, two independent schedulers
    - Modular: Each collector can be started/stopped independently
    - Self-healing: Restarts on errors
    - Graceful shutdown
    """

    # Bounded grace period (seconds) given to in-flight collection jobs to
    # finish naturally during shutdown before we force-terminate the
    # process. See _shutdown() for why a hard-exit fallback is necessary here.
    SHUTDOWN_GRACE_SECONDS = 60

    def __init__(
        self,
        prospective_interval_minutes: int = 30,
        trade_interval_seconds: int = 60,
        trade_lookback_minutes: int = 5,
        currencies: list = None
    ):
        """
        Initialize unified scheduler.

        Args:
            prospective_interval_minutes: How often to run prospective collector (default: 30 min)
            trade_interval_seconds: How often to collect trades (default: 60 sec)
            trade_lookback_minutes: How far back to look for trades (default: 5 min)
            currencies: Currencies to collect (default: BTC, ETH)
        """
        self.prospective_interval = prospective_interval_minutes
        self.trade_interval = trade_interval_seconds
        self.trade_lookback = trade_lookback_minutes
        self.currencies = currencies or SUPPORTED_CURRENCIES

        # Initialize collectors
        self.prospective_collector = ProspectiveCollector()

        # TradeCollector needs API service and repository
        from coding.service.deribit.deribit_api_service import DeribitApiService
        self.api_service = DeribitApiService()
        repository = DatabaseRepository()
        self.trade_collector = TradeCollector(
            api_service=self.api_service,
            repository=repository,
            collection_interval_seconds=self.trade_interval,
            lookback_minutes=self.trade_lookback
        )

        # Scheduler
        self.scheduler = BackgroundScheduler()
        self.is_running = False

        # Track stats
        self.stats = {
            "scheduler_started": None,
            "prospective_collections": 0,
            "prospective_success": 0,
            "prospective_failed": 0,
            "trade_collections": 0,
            "trade_success": 0,
            "trade_failed": 0,
            "last_prospective": None,
            "last_trade": None,
            "next_prospective": None,
            "next_trade": None,
        }

        # Setup graceful shutdown
        signal.signal(signal.SIGINT, self._shutdown_handler)
        signal.signal(signal.SIGTERM, self._shutdown_handler)

    def start(self):
        """
        Start the unified scheduler.

        Startup sequence:
        1. Run initial collections (both collectors)
        2. Schedule recurring collections
        3. Keep running until shutdown
        """
        self.stats["scheduler_started"] = datetime.now()

        logger.info(f"{'='*70}")
        logger.info(f"UNIFIED DATA COLLECTION SCHEDULER STARTING")
        logger.info(f"{'='*70}")
        logger.info(f"Start time: {self.stats['scheduler_started']}")
        logger.info(f"Prospective interval: {self.prospective_interval} minutes")
        logger.info(f"Trade interval: {self.trade_interval} seconds")
        logger.info(f"Trade lookback: {self.trade_lookback} minutes")
        logger.info(f"Currencies: {self.currencies}")
        logger.info(f"{'='*70}\n")

        # Step 1: Run initial collections
        logger.info("Running INITIAL collections on startup...")
        logger.info("-" * 70)

        # Initial prospective collection
        logger.info("\n[1/2] Initial Prospective Collection...")
        self._run_prospective_collection()

        # Initial trade collection
        logger.info("\n[2/2] Initial Trade Collection...")
        self._run_trade_collection()

        # Step 2: Schedule recurring collections
        logger.info("\n" + "=" * 70)
        logger.info("SCHEDULING RECURRING COLLECTIONS")
        logger.info("=" * 70)

        # Schedule prospective collector (every 30 min)
        self.scheduler.add_job(
            func=self._run_prospective_collection,
            trigger=IntervalTrigger(minutes=self.prospective_interval),
            id='prospective_collection',
            name='Prospective Collection (Hourly Snapshots)',
            replace_existing=True,
            max_instances=1
        )
        logger.info(f"[OK] Prospective Collector scheduled (every {self.prospective_interval} min)")

        # Schedule trade collector (every 60 sec)
        self.scheduler.add_job(
            func=self._run_trade_collection,
            trigger=IntervalTrigger(seconds=self.trade_interval),
            id='trade_collection',
            name='Trade Collection (Flow Data)',
            replace_existing=True,
            max_instances=1
        )
        logger.info(f"[OK] Trade Collector scheduled (every {self.trade_interval} sec)")

        # Start scheduler
        self.scheduler.start()
        self.is_running = True

        # Log next run times
        self._update_next_run_times()
        logger.info(f"\nScheduler Status:")
        logger.info(f"  Next Prospective: {self.stats['next_prospective']}")
        logger.info(f"  Next Trade: {self.stats['next_trade']}")

        # Step 3: Keep running
        self._keep_alive()

    def _run_prospective_collection(self):
        """
        Run prospective collection cycle.

        Collects hourly snapshots with greeks.
        Runs every 30 minutes.
        """
        collection_start = datetime.now()

        logger.info(f"\n{'='*70}")
        logger.info(f"PROSPECTIVE COLLECTION CYCLE")
        logger.info(f"{'='*70}")
        logger.info(f"Time: {collection_start}")
        logger.info(f"Cycle: #{self.stats['prospective_collections'] + 1}")

        result = None
        try:
            # Run collection
            result = self.prospective_collector.collect_hour(currencies=self.currencies)

            # Update stats
            self.stats["prospective_collections"] += 1
            self.stats["last_prospective"] = collection_start

            if result["status"] == "success":
                self.stats["prospective_success"] += 1
                logger.info(f"[SUCCESS] Prospective collection SUCCESSFUL")
            elif result["status"] == "partial":
                self.stats["prospective_success"] += 1
                logger.warning(f"[PARTIAL] Prospective collection PARTIAL")
            else:
                self.stats["prospective_failed"] += 1
                logger.error(f"[FAILED] Prospective collection FAILED")

            # Log details
            logger.info(f"   Trades: {result.get('trades_collected', 0)}")
            logger.info(f"   Instruments: {result.get('instruments_collected', 0)}")
            logger.info(f"   Duration: {result.get('duration_seconds', 0):.1f}s")

        except Exception as e:
            logger.exception(f"[CRASHED] Prospective collection CRASHED: {e}")
            self.stats["prospective_collections"] += 1
            self.stats["prospective_failed"] += 1

        self._update_next_run_times()
        logger.info(f"   Next prospective: {self.stats['next_prospective']}")
        logger.info(f"{'='*70}\n")

    def _run_trade_collection(self):
        """
        Run trade collection cycle.

        Collects individual trades with direction field.
        Runs every 60 seconds.
        """
        collection_start = datetime.now()

        logger.info(f"\n{'='*70}")
        logger.info(f"TRADE COLLECTION CYCLE")
        logger.info(f"{'='*70}")
        logger.info(f"Time: {collection_start}")
        logger.info(f"Cycle: #{self.stats['trade_collections'] + 1}")

        try:
            # Store stats before collection
            prev_collected = self.trade_collector.stats["total_trades_collected"]
            prev_stored = self.trade_collector.stats["total_trades_stored"]
            prev_errors = self.trade_collector.stats["errors"]

            # Run collection for all currencies
            for currency in self.currencies:
                try:
                    logger.info(f"\nCollecting trades for {currency}...")
                    self.trade_collector._collect_currency(currency)
                except Exception as e:
                    logger.error(f"  Error collecting {currency}: {e}")

            # Calculate delta
            trades_collected = self.trade_collector.stats["total_trades_collected"] - prev_collected
            trades_stored = self.trade_collector.stats["total_trades_stored"] - prev_stored
            errors = self.trade_collector.stats["errors"] - prev_errors

            # Update stats
            self.stats["trade_collections"] += 1
            self.stats["last_trade"] = collection_start

            if errors == 0:
                self.stats["trade_success"] += 1
                logger.info(f"[SUCCESS] Trade collection SUCCESSFUL")
            else:
                self.stats["trade_failed"] += 1
                logger.warning(f"[PARTIAL] Trade collection had {errors} errors")

            logger.info(f"   Total: Collected {trades_collected}, Stored {trades_stored}")

        except Exception as e:
            logger.exception(f"[CRASHED] Trade collection CRASHED: {e}")
            self.stats["trade_collections"] += 1
            self.stats["trade_failed"] += 1

        self._update_next_run_times()
        logger.info(f"   Next trade: {self.stats['next_trade']}")
        logger.info(f"{'='*70}\n")

    def _update_next_run_times(self):
        """Update next run times for both jobs."""
        prospective_job = self.scheduler.get_job('prospective_collection')
        if prospective_job:
            self.stats["next_prospective"] = prospective_job.next_run_time

        trade_job = self.scheduler.get_job('trade_collection')
        if trade_job:
            self.stats["next_trade"] = trade_job.next_run_time

    def _keep_alive(self):
        """Keep scheduler running until shutdown."""
        logger.info(f"\n{'='*70}")
        logger.info(f"UNIFIED SCHEDULER RUNNING")
        logger.info(f"{'='*70}")
        logger.info(f"Press Ctrl+C to stop gracefully")
        logger.info(f"Or wait for system shutdown\n")

        try:
            while self.is_running:
                # Print status every 5 minutes
                time.sleep(300)
                self._print_status()

        except KeyboardInterrupt:
            logger.info(f"\nKeyboard interrupt received - shutting down...")
            self._shutdown()

    def _print_status(self):
        """Print scheduler status (heartbeat)."""
        logger.info(f"\n{'='*70}")
        logger.info(f"SCHEDULER STATUS")
        logger.info(f"{'='*70}")
        logger.info(f"Running since: {self.stats['scheduler_started']}")
        logger.info(f"\nProspective Collector:")
        logger.info(f"  Collections: {self.stats['prospective_collections']}")
        logger.info(f"  Success: {self.stats['prospective_success']}")
        logger.info(f"  Failed: {self.stats['prospective_failed']}")
        logger.info(f"  Last: {self.stats['last_prospective']}")
        logger.info(f"  Next: {self.stats['next_prospective']}")
        logger.info(f"\nTrade Collector:")
        logger.info(f"  Collections: {self.stats['trade_collections']}")
        logger.info(f"  Success: {self.stats['trade_success']}")
        logger.info(f"  Failed: {self.stats['trade_failed']}")
        logger.info(f"  Last: {self.stats['last_trade']}")
        logger.info(f"  Next: {self.stats['next_trade']}")
        logger.info(f"{'='*70}\n")

    def _shutdown_handler(self, signum, frame):
        """Handle shutdown signals."""
        logger.info(f"\nShutdown signal received (signal {signum})")
        self._shutdown()

    def _shutdown(self):
        """
        Graceful shutdown.

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
        flag. If a prospective or trade collection cycle is still executing
        in that worker thread when SIGTERM arrives, `sys.exit(0)` below
        raises SystemExit and unwinds the main thread fine, but the process
        does not actually terminate until the in-flight job finishes --
        potentially indefinitely if it is blocked on a slow/stuck network or
        DB call. Confirmed empirically: a scheduled job sleeping 8s caused
        the process to hang ~8s past `sys.exit(0)` even with
        `scheduler.shutdown(wait=False)`.
        """
        logger.info(f"\n{'='*70}")
        logger.info(f"UNIFIED SCHEDULER SHUTTING DOWN")
        logger.info(f"{'='*70}")

        # Stop scheduler
        if self.scheduler.running:
            logger.info(f"Stopping scheduler...")
            self.scheduler.shutdown(wait=False)

        # Final status
        self._print_status()

        # Mark as stopped
        self.is_running = False

        logger.info(f"Shutdown complete. Goodbye!")
        logger.info(f"{'='*70}\n")

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
    Main entry point for unified scheduler.

    This is what Task Scheduler will call on system startup.
    """
    # Initialize logging
    init_logging(
        level="INFO",
        task_name="unified_scheduler",
        log_to_file=True
    )

    logger.info(f"\n{'='*70}")
    logger.info(f"UNIFIED DATA COLLECTION SCHEDULER")
    logger.info(f"{'='*70}")
    logger.info(f"System startup detected - initializing scheduler...")
    logger.info(f"Boot time: {datetime.now()}")
    logger.info(f"{'='*70}\n")

    # Create and start scheduler
    scheduler = UnifiedScheduler(
        prospective_interval_minutes=30,
        trade_interval_seconds=60,
        trade_lookback_minutes=5,
        currencies=SUPPORTED_CURRENCIES
    )

    scheduler.start()


if __name__ == "__main__":
    main()
