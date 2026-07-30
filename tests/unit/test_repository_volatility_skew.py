"""
Unit tests for DatabaseRepository.save_volatility_skew.

institutional_metrics_spec.md Migration M3 / section 3 (Task C4): persists
the delta-interpolated RR25/BF25 term-structure row
(VolatilitySurfaceCalculator.calculate_risk_reversal_butterfly()'s dict
shape) to volatility_skew_history (migration 018). Mocked cursor only --
no live database.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

from coding.core.database.repository import DatabaseRepository


def _make_repo():
    return DatabaseRepository.__new__(DatabaseRepository)


def _patched(repo):
    mock_cursor = MagicMock()
    ctx = patch.object(repo, "_db_cursor")
    mock_ctx = ctx.start()
    mock_ctx.return_value.__enter__ = lambda s: mock_cursor
    mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
    return ctx, mock_cursor


def _skew_dict(**overrides):
    base = {
        "rr_25d": -3.80,
        "bf_25d": 0.90,
        "call_25d_iv": 17.11,
        "put_25d_iv": 20.91,
        "call_25d_strike": 95000.0,
        "put_25d_strike": 80000.0,
        "atm_iv_interp": 18.51,
        "n_quotes_used": 14,
        "method": "linear_delta",
    }
    base.update(overrides)
    return base


class TestSaveVolatilitySkewSchema:
    """Every column the migration 018 schema defines (except id/created_at,
    which the DB owns) must be written by this INSERT."""

    def test_insert_targets_volatility_skew_history(self):
        repo = _make_repo()
        ctx, mock_cursor = _patched(repo)
        hour = datetime(2026, 7, 29, 18, 0)
        try:
            repo.save_volatility_skew(
                snapshot_hour=hour, currency="BTC", expiration="25JUL26",
                dte_years=0.00164, skew=_skew_dict(),
            )
        finally:
            ctx.stop()

        sql, params = mock_cursor.execute.call_args[0]
        assert "INSERT INTO volatility_skew_history" in sql
        assert "ON CONFLICT (snapshot_hour, currency, expiration)" in sql

        for column in (
            "snapshot_hour", "currency", "expiration", "dte_years",
            "atm_iv_interp", "call_25d_iv", "put_25d_iv",
            "call_25d_strike", "put_25d_strike", "rr_25d", "bf_25d",
            "n_quotes_used", "interp_method",
        ):
            assert column in sql, f"missing column {column!r} in INSERT"

        assert params["snapshot_hour"] == hour
        assert params["currency"] == "BTC"
        assert params["expiration"] == "25JUL26"
        assert params["dte_years"] == 0.00164
        assert params["rr_25d"] == -3.80
        assert params["bf_25d"] == 0.90
        assert params["call_25d_iv"] == 17.11
        assert params["put_25d_iv"] == 20.91
        assert params["call_25d_strike"] == 95000.0
        assert params["put_25d_strike"] == 80000.0
        assert params["atm_iv_interp"] == 18.51
        assert params["n_quotes_used"] == 14
        assert params["interp_method"] == "linear_delta"

    def test_null_rr_bf_persisted_as_none_not_zero(self):
        """T3.3's F2-degeneracy fix must survive the DB round trip: a None
        (insufficient chain) skew dict must write NULL, never 0.0 -- that
        was the exact bug this whole task replaces."""
        repo = _make_repo()
        ctx, mock_cursor = _patched(repo)
        hour = datetime(2026, 7, 29, 18, 0)
        try:
            repo.save_volatility_skew(
                snapshot_hour=hour, currency="BTC", expiration="7AUG26",
                dte_years=0.03,
                skew=_skew_dict(
                    rr_25d=None, bf_25d=None, call_25d_iv=None,
                    put_25d_iv=None, call_25d_strike=None,
                    put_25d_strike=None, atm_iv_interp=None,
                ),
            )
        finally:
            ctx.stop()

        _, params = mock_cursor.execute.call_args[0]
        assert params["rr_25d"] is None
        assert params["bf_25d"] is None
        assert params["call_25d_iv"] is None
        assert params["put_25d_iv"] is None

    def test_conflict_updates_every_value_column(self):
        repo = _make_repo()
        ctx, mock_cursor = _patched(repo)
        try:
            repo.save_volatility_skew(
                snapshot_hour=datetime(2026, 7, 29, 18, 0), currency="BTC",
                expiration="25JUL26", dte_years=0.00164, skew=_skew_dict(),
            )
        finally:
            ctx.stop()

        sql, _ = mock_cursor.execute.call_args[0]
        update_clause = sql.split("DO UPDATE SET", 1)[1]
        for column in (
            "dte_years", "atm_iv_interp", "call_25d_iv", "put_25d_iv",
            "call_25d_strike", "put_25d_strike", "rr_25d", "bf_25d",
            "n_quotes_used", "interp_method",
        ):
            assert f"{column} = EXCLUDED.{column}" in update_clause
