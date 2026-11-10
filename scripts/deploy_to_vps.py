"""
Stage merged code on the VPS as a git bundle (fast-forward of main).

Does NOT restart the service, change .env or run migrations. See
documentation/vps/vps_setup_and_operations.md, "Deploying code to the VPS (bundle)".

Usage:
    python -m scripts.deploy_to_vps --commit <sha | origin/main> [--dry-run]
"""

import argparse
import logging
import sys
from typing import Optional, Sequence

from coding.core.logging.logging_setup import init_logging
from coding.service.deployment.vps_code_staging_service import (
    StagingError,
    SubprocessCommandRunner,
    VpsCodeStagingService,
)

logger = logging.getLogger(__name__)


def parse_arguments(arguments: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse the command line. --commit is required."""
    parser = argparse.ArgumentParser(
        prog="python -m scripts.deploy_to_vps",
        description="Stage a merged commit on the VPS with a git bundle. "
        "The service is NOT restarted.",
    )
    parser.add_argument(
        "--commit", required=True,
        help="Commit to stage: a hex hash (7 to 40 characters) or the literal origin/main",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run only the read-only local git checks and log every other command",
    )
    return parser.parse_args(arguments)


def main(arguments: Optional[Sequence[str]] = None) -> int:
    """Run the staging; 0 on success, 1 on any refusal or failure."""
    parsed = parse_arguments(arguments)
    init_logging(level="INFO", task_name="deploy_to_vps")
    service = VpsCodeStagingService(runner=SubprocessCommandRunner())
    try:
        result = service.stage(parsed.commit, dry_run=parsed.dry_run)
    except StagingError as error:
        logger.error("Staging stopped: %s", error)
        return 1
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
