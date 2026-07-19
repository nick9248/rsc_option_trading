"""
System Health Validator — thin wrapper over the shared health-check
registry (coding/service/health), running every LOCAL-environment
checker. VPS-environment checks run separately via check_vps_health.py
on the VPS itself (cron, automatic) — see that script's docstring.

Run this before claiming "everything works":
    python -m scripts.validate_system
"""

import logging
import os
import sys
from typing import Dict, List

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from coding.core.database.repository import DatabaseRepository
from coding.core.health.models import CheckEnvironment, CheckResult, CheckStatus
from coding.core.logging.logging_setup import init_logging
from coding.service.health.registry import run_checks

init_logging(level="INFO")
logger = logging.getLogger(__name__)


class SystemValidator:
    """Runs every LOCAL-environment health check and logs a grouped summary."""

    def __init__(self, repository=None):
        self.repo = repository or DatabaseRepository()

    def validate_all(self) -> Dict[str, List[CheckResult]]:
        """
        Run all LOCAL health checks, grouped by category.

        Returns:
            Dict mapping category name to its list of CheckResult.
        """
        logger.info("=" * 80)
        logger.info("SYSTEM HEALTH VALIDATION")
        logger.info("=" * 80)

        grouped = run_checks(CheckEnvironment.LOCAL, self.repo)
        self._log_summary(grouped)
        return grouped

    def _log_summary(self, grouped: Dict[str, List[CheckResult]]) -> None:
        passed = warnings = failed = 0
        problems: List[str] = []
        for category, results in grouped.items():
            logger.info(f"\n{category}")
            logger.info("-" * 80)
            for result in results:
                if result.status == CheckStatus.PASS:
                    passed += 1
                    logger.info(f"  [PASS] {result.message}")
                elif result.status == CheckStatus.WARN:
                    warnings += 1
                    logger.warning(f"  [WARN] {result.message}")
                    problems.append(f"[WARN] {category}: {result.message}")
                else:
                    failed += 1
                    logger.error(f"  [FAIL] {result.message}")
                    problems.append(f"[FAIL] {category}: {result.message}")

        logger.info("\n" + "=" * 80)
        logger.info(f"Total: {passed} passed, {warnings} warnings, {failed} failed")
        if failed:
            logger.info("OVERALL STATUS: SYSTEM HAS CRITICAL ISSUES")
        elif warnings:
            logger.info("OVERALL STATUS: OPERATIONAL WITH WARNINGS")
        else:
            logger.info("OVERALL STATUS: ALL SYSTEMS OPERATIONAL")
        logger.info("=" * 80)

        if problems:
            logger.info(f"\nPROBLEMS ({len(problems)}):")
            for problem in problems:
                logger.info(f"  - {problem}")


def main():
    """Run system validation."""
    validator = SystemValidator()
    grouped = validator.validate_all()
    failed = any(r.status == CheckStatus.FAIL for results in grouped.values() for r in results)
    if failed:
        exit(1)


if __name__ == "__main__":
    main()
