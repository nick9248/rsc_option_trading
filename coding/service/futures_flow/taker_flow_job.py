"""
The taker flow collection job inside the collector (unified_scheduler).

Registered on the collector's scheduler only with TAKER_FLOW_ENABLED=1 (off by
default; the VPS .env sets it): every hour at ``RUN_MINUTE`` UTC, plus one run
at registration so a restart does not wait up to an hour. A failure is logged
and never stops the collector or the next run.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from apscheduler.triggers.cron import CronTrigger

from coding.core.database.repository import DatabaseRepository
from coding.service.futures_flow.binance_taker_flow_client import BinanceTakerFlowClient
from coding.service.futures_flow.taker_flow_collection_service import TakerFlowCollectionService
from coding.service.futures_flow.taker_flow_settings import RUN_MINUTE, TakerFlowSettings

logger = logging.getLogger(__name__)

JOB_ID = "taker_flow_hourly"
TABLE_NAME = "taker_flow_hourly"
MISFIRE_GRACE_SECONDS = 600


class TakerFlowJob:
    """The scheduled hourly collection."""

    def __init__(self, service: TakerFlowCollectionService):
        """
        Args:
            service: The collection to run.
        """
        self.service = service

    def run(self) -> None:
        """One collection; an unexpected error is logged, never raised."""
        try:
            self.service.collect()
        except Exception:
            logger.exception("Taker flow collection run failed")

    def register(self, scheduler) -> None:
        """Add the hourly job to an APScheduler scheduler and run once now."""
        scheduler.add_job(
            func=self.run,
            trigger=CronTrigger(minute=RUN_MINUTE, timezone=timezone.utc),
            id=JOB_ID,
            name="Taker flow (Binance USD-M hourly)",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=MISFIRE_GRACE_SECONDS,
            next_run_time=datetime.now(timezone.utc),
        )
        logger.info("[OK] Taker flow scheduled: hourly at :%02d UTC (first run now)", RUN_MINUTE)


def start_taker_flow_job(repository: Optional[DatabaseRepository] = None) -> Optional[TakerFlowJob]:
    """
    The job, or None unless TAKER_FLOW_ENABLED=1. A bad setting or a construction
    error is logged at ERROR and returns None, so the collector keeps running.
    """
    try:
        settings = TakerFlowSettings.from_environment()
        if not settings.enabled:
            logger.info("Taker flow OFF: nothing is collected (set TAKER_FLOW_ENABLED=1, production collector only)")
            return None
        repository = repository or DatabaseRepository()
        if repository.missing_tables([TABLE_NAME]):
            logger.error("Taker flow NOT started: table %s is missing (apply migration 033, then restart "
                         "the collector); the collector keeps running", TABLE_NAME)
            return None
        service = TakerFlowCollectionService(BinanceTakerFlowClient(settings.base_url), repository)
        logger.info("Taker flow ON: source %s", settings.base_url)
        return TakerFlowJob(service)
    except Exception:
        logger.exception("Taker flow NOT started (configuration or construction error); the collector keeps running")
        return None
