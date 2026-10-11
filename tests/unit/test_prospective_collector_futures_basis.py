"""Tests for the futures-basis step of ProspectiveCollector's cycle.

Deribit lists dated futures but does not serve expired ones, so history only
accumulates from the first capture. Each hour the collector stores, per dated
future: mark price, index price, basis (absolute and %), annualized basis and
days to expiry.
"""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from coding.service.data_collection.prospective_collector import ProspectiveCollector

NOW = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


def _collector():
    collector = ProspectiveCollector.__new__(ProspectiveCollector)
    collector.api = MagicMock()
    collector.repo = MagicMock()
    return collector


def _capture(collector, hour=datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)):
    with patch("coding.service.data_collection.prospective_collector.datetime", _FrozenDateTime):
        return collector._capture_futures_basis("BTC", hour)


def test_basis_is_stored_for_each_priced_future_with_hand_calculated_values():
    collector = _collector()
    collector.api.get_dated_futures_marks.return_value = [
        {"instrument_name": "BTC-31DEC26", "mark_price": 70000.0, "index_price": 68000.0},
    ]
    collector.repo.save_futures_basis.return_value = 1

    assert _capture(collector) == 1

    (rows,) = collector.repo.save_futures_basis.call_args.args
    assert len(rows) == 1
    row = rows[0]
    # Hand calculation: settlement 2026-12-31 08:00 UTC, now 2026-10-01 00:00 UTC
    #   days = 91 + 8/24 = 91.3333 -> stored rounded to 2 decimals
    #   basis = 70000 - 68000 = 2000;  2000 / 68000 * 100 = 2.941176 %
    #   annualized (simple, ACT/365) = 2.941176 % * 365 / 91.3333 = 11.7547 %
    assert row["futures_instrument"] == "BTC-31DEC26"
    assert row["currency"] == "BTC"
    assert row["timestamp"] == datetime(2026, 10, 1, 0, 0)  # UTC-valued, naive
    assert row["futures_price"] == 70000.0
    assert row["spot_price"] == 68000.0
    assert row["basis_absolute"] == pytest.approx(2000.0)
    assert row["basis_percentage"] == pytest.approx(2.941176, abs=1e-5)
    assert row["days_to_expiry"] == pytest.approx(91.33, abs=0.005)
    assert row["implied_repo_rate"] == pytest.approx(11.7547, abs=1e-3)


def test_future_without_a_price_is_skipped_not_stored_as_zero():
    collector = _collector()
    collector.api.get_dated_futures_marks.return_value = [
        {"instrument_name": "BTC-31DEC26", "mark_price": 70000.0, "index_price": None},
        {"instrument_name": "BTC-26MAR27", "mark_price": 0, "index_price": 68000.0},
    ]

    assert _capture(collector) == 0

    collector.repo.save_futures_basis.assert_not_called()


def test_hour_is_truncated_to_the_hour():
    collector = _collector()
    collector.api.get_dated_futures_marks.return_value = [
        {"instrument_name": "BTC-31DEC26", "mark_price": 70000.0, "index_price": 68000.0},
    ]
    collector.repo.save_futures_basis.return_value = 1

    _capture(collector, hour=datetime(2026, 10, 1, 0, 37, 12, tzinfo=timezone.utc))

    (rows,) = collector.repo.save_futures_basis.call_args.args
    assert rows[0]["timestamp"] == datetime(2026, 10, 1, 0, 0)


def test_failure_in_the_basis_step_does_not_stop_the_rest_of_the_cycle():
    """The step is wrapped in its own try/except in the cycle: an API error must be
    logged, not raised. (Checked on the cycle source: the step sits in a try block.)"""
    import inspect

    source = inspect.getsource(ProspectiveCollector)
    step = source.index("self._capture_futures_basis(currency, hour)")
    assert "try:" in source[step - 120:step]
    assert "except Exception" in source[step:step + 200]
