"""
Unit tests for DatabaseRepository.get_hourly_snapshots_for_hour (Task
Wave-J-E Fix 1 + Fix 2).

Fix 1 bug: this query never selected bid_price/ask_price, so every
instrument dict it returned was missing both keys. VolatilitySurfaceCalculator
._build_delta_points defaults missing bid_price/ask_price to 0 via
``inst.get("bid_price") or 0`` -- meaning its "quoted" filter
(bid_price > 0 or ask_price > 0) failed unconditionally for every row this
method ever returned, regardless of real market data, and
VolatilityReconstructionService silently persisted skew_25d/put_25d_iv/
call_25d_iv as None ("insufficient chain") for every historical hour.

Fix 2: bid_is_estimated/ask_is_estimated are also selected, disclosing
whether each side is a genuine trade-derived value or the vwap+/-0.5%
fallback (see migration 025 / HourlyAggregationService._aggregate_instrument).

Mocked cursor only -- no live database (matches the established pattern at
test_repository_save_hourly_snapshots_upsert.py / test_repository_save_
snapshot.py for this repository's methods).
"""
from datetime import datetime
from unittest.mock import MagicMock, patch

from coding.core.database.repository import DatabaseRepository


def _make_repo():
    return DatabaseRepository.__new__(DatabaseRepository)


def _run_query(repo, rows):
    """Execute get_hourly_snapshots_for_hour against a mocked cursor that
    returns `rows` (tuples in the SELECT's exact column order) and capture
    the SQL actually issued."""
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = rows
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = MagicMock(return_value=mock_cursor)
    mock_ctx.__exit__ = MagicMock(return_value=False)

    with patch.object(repo, "_db_cursor", return_value=mock_ctx):
        result = repo.get_hourly_snapshots_for_hour("BTC", datetime(2026, 2, 6, 22, 0), "10FEB26")

    sql = mock_cursor.execute.call_args[0][0]
    return result, sql


class TestSelectColumnsIncludeBidAsk:
    """Regression guard for the exact Fix 1 defect: bid_price/ask_price
    (and the Fix 2 disclosure flags) must be present in the SELECT and in
    the returned instrument dicts."""

    def test_select_statement_names_bid_ask_columns(self):
        repo = _make_repo()
        _, sql = _run_query(repo, [])

        assert "bid_price" in sql
        assert "ask_price" in sql
        assert "bid_is_estimated" in sql
        assert "ask_is_estimated" in sql

    def test_returned_dict_carries_bid_ask_and_estimated_flags(self):
        """Real dict shape this query now produces -- one row, matching the
        SELECT's exact column order:
        instrument_name, strike, option_type, mark_iv, avg_delta, avg_gamma,
        avg_theta, avg_vega, open_interest, index_price, bid_price,
        ask_price, bid_is_estimated, ask_is_estimated."""
        repo = _make_repo()
        rows = [(
            "BTC-10FEB26-65000-P", 65000.0, "P", 77.82,
            -0.1555637, 4.53518e-05, -184.10147966, 16.16585425,
            94.3, 69952.23,
            0.0064675, 0.0065, False, True,
        )]
        result, _ = _run_query(repo, rows)

        assert len(result) == 1
        inst = result[0]
        assert inst["bid_price"] == 0.0064675
        assert inst["ask_price"] == 0.0065
        assert inst["bid_is_estimated"] is False
        assert inst["ask_is_estimated"] is True

    def test_null_estimated_flags_default_conservative_true(self):
        """Rows written before migration 025 have no recorded provenance --
        NULL must coerce to True (estimated), not False, matching the
        migration's own conservative DEFAULT TRUE."""
        repo = _make_repo()
        rows = [(
            "BTC-10FEB26-65000-P", 65000.0, "P", 77.82,
            -0.1555637, 4.53518e-05, -184.10147966, 16.16585425,
            94.3, 69952.23,
            0.0064675, 0.0065, None, None,
        )]
        result, _ = _run_query(repo, rows)

        assert result[0]["bid_is_estimated"] is True
        assert result[0]["ask_is_estimated"] is True


class TestQuotedFilterNowSurvivesRealData:
    """End-to-end: feed this method's REAL output shape through
    VolatilitySurfaceCalculator's quoted filter and confirm a genuinely
    trade-derived instrument now survives (Fix 1 unblocked), while a fully
    estimated one (both sides fallback-derived, Fix 2's disclosure) still
    does not."""

    def test_real_bid_ask_with_genuine_trade_survives_quoted_filter(self):
        from coding.core.analytics.volatility_surface_calculator import VolatilitySurfaceCalculator

        repo = _make_repo()
        rows = [(
            "BTC-10FEB26-65000-P", 65000.0, "P", 77.82,
            -0.25, 4.53518e-05, -184.10147966, 16.16585425,
            94.3, 69952.23,
            0.0064675, 0.0065, False, True,  # bid genuinely traded, ask estimated
        )]
        instruments, _ = _run_query(repo, rows)

        calc = VolatilitySurfaceCalculator(
            instruments=instruments, spot_price=69952.23, expiration="10FEB26",
        )
        points = calc._build_delta_points("P")
        assert len(points) == 1

    def test_fully_estimated_both_sides_still_excluded(self):
        from coding.core.analytics.volatility_surface_calculator import VolatilitySurfaceCalculator

        repo = _make_repo()
        rows = [(
            "BTC-10FEB26-65000-P", 65000.0, "P", 77.82,
            -0.25, 4.53518e-05, -184.10147966, 16.16585425,
            94.3, 69952.23,
            0.0064675, 0.0065, True, True,  # neither side has real trade evidence
        )]
        instruments, _ = _run_query(repo, rows)

        calc = VolatilitySurfaceCalculator(
            instruments=instruments, spot_price=69952.23, expiration="10FEB26",
        )
        points = calc._build_delta_points("P")
        assert points == []
