"""Runs registered HealthCheck instances, grouped by category."""

import logging
from typing import Dict, List, Optional

from coding.core.health.models import CheckEnvironment, CheckResult, CheckStatus
from coding.service.health.base import HealthCheck
from coding.service.health.checkers.api_checker import ApiConnectivityCheck
from coding.service.health.checkers.daemon_checker import DaemonServiceCheck
from coding.service.health.checkers.database_local_checker import DatabaseLocalFreshnessCheck
from coding.service.health.checkers.database_sync_gap_checker import DatabaseSyncGapCheck
from coding.service.health.checkers.database_vps_gap_checker import DatabaseVpsGapCheck
from coding.service.health.checkers.forward_test_checker import (
    OnChainForwardTestCheck, StraddleForwardTestCheck,
)
from coding.service.health.checkers.iv_percentile_checker import IvPercentileWindowCheck
from coding.service.health.checkers.morning_note_checker import MorningNoteSmokeTestCheck
from coding.service.health.checkers.scanner_checker import ScannerActivityCheck
from coding.service.health.checkers.telegram_checker import TelegramConfigCheck, TelegramDeliveryCheck

logger = logging.getLogger(__name__)

CHECKERS: List[HealthCheck] = [
    ApiConnectivityCheck(),
    DatabaseLocalFreshnessCheck(),
    DatabaseVpsGapCheck(),
    DatabaseSyncGapCheck(),
    ScannerActivityCheck(),
    DaemonServiceCheck(),
    TelegramConfigCheck(),
    TelegramDeliveryCheck(),
    OnChainForwardTestCheck(),
    StraddleForwardTestCheck(),
    IvPercentileWindowCheck(),
    MorningNoteSmokeTestCheck(),
]


def run_checks(
    environment: CheckEnvironment,
    repo,
    checkers: Optional[List[HealthCheck]] = None,
) -> Dict[str, List[CheckResult]]:
    """
    Run every checker whose environment matches (BOTH always matches),
    grouped by category. A checker whose run() raises is converted to a
    single FAIL CheckResult instead of aborting the whole run.
    """
    active_checkers = CHECKERS if checkers is None else checkers
    grouped: Dict[str, List[CheckResult]] = {}

    for checker in active_checkers:
        if checker.environment != environment and checker.environment != CheckEnvironment.BOTH:
            continue

        try:
            results = checker.run(repo)
        except Exception as exc:
            logger.exception("Health checker %s raised", checker.category)
            results = [CheckResult(
                name=checker.category,
                status=CheckStatus.FAIL,
                message=f"Checker raised an exception: {exc}",
            )]

        grouped.setdefault(checker.category, []).extend(results)

    return grouped
