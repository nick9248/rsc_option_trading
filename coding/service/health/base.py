"""Base class for health checkers."""

from abc import ABC, abstractmethod
from typing import List

from coding.core.health.models import CheckEnvironment, CheckResult


class HealthCheck(ABC):
    """One health-check module. Subclasses set category/environment and implement run()."""

    category: str
    environment: CheckEnvironment

    @abstractmethod
    def run(self, repo) -> List[CheckResult]:
        """Execute this check and return its results."""
        raise NotImplementedError
