"""Core data types for the health-check framework."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict


class CheckStatus(Enum):
    """Outcome of a single health check."""
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class CheckEnvironment(Enum):
    """Which entry point(s) a HealthCheck runs under."""
    LOCAL = "local"
    VPS = "vps"
    BOTH = "both"


@dataclass
class CheckResult:
    """Outcome of one health check."""
    name: str
    status: CheckStatus
    message: str
    details: Dict[str, Any] = field(default_factory=dict)
