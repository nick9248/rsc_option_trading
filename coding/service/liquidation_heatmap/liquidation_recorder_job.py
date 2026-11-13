"""
The liquidation recorder jobs inside the collector (unified_scheduler).

Registered only with LIQUIDATION_RECORDER_ENABLED=1 (off by default; the VPS .env sets it, like TAKER_FLOW_ENABLED):
  - ``liquidation_events``    every ``EVENT_POLL_MINUTES`` minutes: store new real liquidations of OKX BTC-USDT-SWAP
  - ``liquidation_snapshot``  every hour at ``SNAPSHOT_MINUTE`` UTC: compute and store the estimated levels
Both also run once at registration. A failure is logged and never stops the collector or the other job. The candles
for the snapshot come from BINANCE_FUTURES_BASE_URL (the relay on the VPS).
"""

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv

from coding.core.database.repository import DatabaseRepository
from coding.service.futures_flow.taker_flow_settings import read_binance_futures_base_url
from coding.service.liquidation_heatmap.binance_history_loader import BinanceHistoryLoader
from coding.service.liquidation_heatmap.heatmap_snapshot_service import HeatmapSnapshotService
from coding.service.liquidation_heatmap.liquidation_event_recorder import LiquidationEventRecorder
from coding.service.liquidation_heatmap.okx_liquidation_client import OkxLiquidationClient

logger = logging.getLogger(__name__)

EVENT_JOB_ID = "liquidation_events"
SNAPSHOT_JOB_ID = "liquidation_snapshot"
EVENT_POLL_MINUTES = 2
SNAPSHOT_MINUTE = 12
MISFIRE_GRACE_SECONDS = 600
TABLES = ("liquidation_events", "liquidation_heatmap_snapshots")


class LiquidationRecorderJob:
    """The two scheduled recorders."""

    def __init__(self, event_recorder: LiquidationEventRecorder, snapshot_service: HeatmapSnapshotService):
        """
        Args:
            event_recorder: Stores real liquidation events.
            snapshot_service: Stores the hourly level snapshot.
        """
        self.event_recorder = event_recorder
        self.snapshot_service = snapshot_service

    def run_events(self) -> None:
        """One event recording; an error is logged, never raised."""
        try:
            self.event_recorder.record()
        except Exception:
            logger.exception("Liquidation event recording failed")

    def run_snapshot(self) -> None:
        """One snapshot; an error is logged, never raised."""
        try:
            self.snapshot_service.snapshot()
        except Exception:
            logger.exception("Liquidation snapshot failed")

    def register(self, scheduler) -> None:
        """Add both jobs to an APScheduler scheduler; each also runs once now."""
        now = datetime.now(timezone.utc)
        scheduler.add_job(func=self.run_events, trigger=IntervalTrigger(minutes=EVENT_POLL_MINUTES),
                          id=EVENT_JOB_ID, name="Liquidation events (OKX BTC-USDT-SWAP)", replace_existing=True,
                          max_instances=1, coalesce=True, misfire_grace_time=MISFIRE_GRACE_SECONDS, next_run_time=now)
        scheduler.add_job(func=self.run_snapshot, trigger=CronTrigger(minute=SNAPSHOT_MINUTE, timezone=timezone.utc),
                          id=SNAPSHOT_JOB_ID, name="Liquidation level snapshot (hourly)", replace_existing=True,
                          max_instances=1, coalesce=True, misfire_grace_time=MISFIRE_GRACE_SECONDS, next_run_time=now)
        logger.info("[OK] Liquidation recorder scheduled: events every %d min, snapshot hourly at :%02d UTC",
                    EVENT_POLL_MINUTES, SNAPSHOT_MINUTE)


def recorder_enabled() -> bool:
    """True when LIQUIDATION_RECORDER_ENABLED is 1/true/yes/on; an invalid value raises ValueError."""
    load_dotenv(dotenv_path=Path(__file__).parents[3] / ".env")
    text = (os.environ.get("LIQUIDATION_RECORDER_ENABLED") or "").strip().lower()
    if text in ("", "0", "false", "no", "off"):
        return False
    if text in ("1", "true", "yes", "on"):
        return True
    raise ValueError(f"LIQUIDATION_RECORDER_ENABLED={text!r} is not a boolean (use 1 or 0)")


def start_liquidation_recorder_job(repository: Optional[DatabaseRepository] = None) -> Optional[LiquidationRecorderJob]:
    """
    The job, or None unless LIQUIDATION_RECORDER_ENABLED=1. A bad setting, missing tables (migration 034 not applied)
    or a construction error is logged at ERROR and returns None, so the collector keeps running.
    """
    try:
        if not recorder_enabled():
            logger.info("Liquidation recorder OFF: nothing is recorded (set LIQUIDATION_RECORDER_ENABLED=1, "
                        "production collector only)")
            return None
        repository = repository or DatabaseRepository()
        missing = repository.missing_tables(list(TABLES))
        if missing:
            logger.error("Liquidation recorder NOT started: table(s) %s missing (apply migration 034, then restart "
                         "the collector); the collector keeps running", ", ".join(missing))
            return None
        base_url = read_binance_futures_base_url()
        loader = BinanceHistoryLoader(None, rest_base_url=base_url)          # candles by REST only: no file cache
        logger.info("Liquidation recorder ON: candles from %s", base_url)
        return LiquidationRecorderJob(LiquidationEventRecorder(OkxLiquidationClient(), repository),
                                      HeatmapSnapshotService(loader, repository))
    except Exception:
        logger.exception("Liquidation recorder NOT started (configuration or construction error); "
                         "the collector keeps running")
        return None
