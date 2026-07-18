"""Integration-level test that the full checker registry is wired correctly."""

from coding.service.health.base import HealthCheck
from coding.service.health.registry import CHECKERS


def test_all_checkers_are_health_check_instances():
    assert len(CHECKERS) == 12
    for checker in CHECKERS:
        assert isinstance(checker, HealthCheck)
        assert checker.category
        assert checker.environment is not None


def test_expected_categories_present():
    categories = {checker.category for checker in CHECKERS}
    expected = {
        "API Connectivity", "Database — Local", "Database — VPS Internal Continuity",
        "Database — VPS Sync", "Scanner Activity", "Daemon Service", "Telegram",
        "Forward-Test Harnesses", "IV-Percentile Window", "Morning Note",
    }
    assert expected.issubset(categories)
