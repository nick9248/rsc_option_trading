"""VPS daemon (systemd service) liveness health check."""

import subprocess
from typing import List

from coding.core.health.models import CheckEnvironment, CheckResult, CheckStatus
from coding.service.health.base import HealthCheck

_SERVICE_NAME = "option-trading"


class DaemonServiceCheck(HealthCheck):
    """Checks the option-trading systemd service is active. VPS-only -- systemctl doesn't exist locally."""

    category = "Daemon Service"
    environment = CheckEnvironment.VPS

    def __init__(self, subprocess_runner=subprocess.run):
        self._run = subprocess_runner

    def run(self, repo) -> List[CheckResult]:
        try:
            result = self._run(
                ["systemctl", "is-active", _SERVICE_NAME],
                capture_output=True, text=True, timeout=5,
            )
        except FileNotFoundError:
            return [CheckResult(
                name="Daemon service", status=CheckStatus.WARN,
                message="systemctl not available (not Linux) — skipping",
            )]
        except Exception as exc:
            return [CheckResult(
                name="Daemon service", status=CheckStatus.FAIL,
                message=f"systemd check failed: {exc}",
            )]

        if result.stdout.strip() == "active":
            return [CheckResult(
                name="Daemon service", status=CheckStatus.PASS,
                message=f"{_SERVICE_NAME} systemd service RUNNING",
            )]
        return [CheckResult(
            name="Daemon service", status=CheckStatus.FAIL,
            message=f"{_SERVICE_NAME} systemd service NOT RUNNING (state: {result.stdout.strip()})",
        )]
