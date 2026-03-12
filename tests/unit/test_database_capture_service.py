"""
Unit tests for DatabaseCaptureService.get_last_captured.
"""
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from coding.service.database.capture_service import DatabaseCaptureService


@pytest.fixture
def mock_repo():
    return MagicMock()


@pytest.fixture
def service(mock_repo):
    return DatabaseCaptureService(repository=mock_repo)


def test_get_last_captured_returns_all_six_keys(service, mock_repo):
    """All 6 capture types are always present in the result."""
    mock_repo.get_last_captured_times.return_value = {
        "snapshot": datetime(2026, 3, 12, 10, 0),
        "max_pain": datetime(2026, 3, 12, 10, 1),
        "open_interest": datetime(2026, 3, 12, 10, 2),
        "volume": datetime(2026, 3, 12, 10, 3),
        "levels": datetime(2026, 3, 12, 10, 4),
        "gex_dex": datetime(2026, 3, 12, 10, 5),
    }
    result = service.get_last_captured("BTC")
    assert set(result.keys()) == {"snapshot", "max_pain", "open_interest", "volume", "levels", "gex_dex"}


def test_get_last_captured_delegates_to_repository(service, mock_repo):
    """Service delegates to repository with correct currency."""
    mock_repo.get_last_captured_times.return_value = {
        k: None for k in ["snapshot", "max_pain", "open_interest", "volume", "levels", "gex_dex"]
    }
    service.get_last_captured("ETH")
    mock_repo.get_last_captured_times.assert_called_once_with("ETH")


def test_get_last_captured_returns_none_for_never_captured(service, mock_repo):
    """None values are passed through for capture types with no data."""
    mock_repo.get_last_captured_times.return_value = {
        "snapshot": None,
        "max_pain": datetime(2026, 3, 12, 9, 0),
        "open_interest": None,
        "volume": None,
        "levels": None,
        "gex_dex": None,
    }
    result = service.get_last_captured("BTC")
    assert result["snapshot"] is None
    assert result["max_pain"] == datetime(2026, 3, 12, 9, 0)


def test_get_last_captured_returns_datetime_values(service, mock_repo):
    """Datetime values are returned unchanged."""
    ts = datetime(2026, 3, 12, 14, 32, 55)
    mock_repo.get_last_captured_times.return_value = {
        k: ts for k in ["snapshot", "max_pain", "open_interest", "volume", "levels", "gex_dex"]
    }
    result = service.get_last_captured("BTC")
    for v in result.values():
        assert v == ts
