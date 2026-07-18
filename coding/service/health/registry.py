"""Runs registered HealthCheck instances, grouped by category."""

import logging
from typing import Dict, List, Optional

from coding.core.health.models import CheckEnvironment, CheckResult, CheckStatus
from coding.service.health.base import HealthCheck

logger = logging.getLogger(__name__)

CHECKERS: List[HealthCheck] = []


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
