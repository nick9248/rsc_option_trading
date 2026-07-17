"""
Unit tests for DatabaseRepository straddle_scan_history CRUD methods
(migration 014). Mocked cursor only -- no live database.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from coding.core.database.repository import DatabaseRepository


def _make_repo():
    return DatabaseRepository.__new__(DatabaseRepository)


_UNSET = object()


def _patched(repo, fetchone=_UNSET, fetchall=None):
    mock_cursor = MagicMock()
    if fetchone is not _UNSET:
        mock_cursor.fetchone.return_value = fetchone
    if fetchall is not None:
        mock_cursor.fetchall.return_value = fetchall
    ctx = patch.object(repo, "_db_cursor")
    mock_ctx = ctx.start()
    mock_ctx.return_value.__enter__ = lambda s: mock_cursor
    mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
    return ctx, mock_cursor


_ROW = {
    "scan_time": datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc),
    "currency": "BTC",
    "expiration": "25SEP26",
    "dte": 68.0,
    "future_price": 118432.50,
    "index_price": 118432.50,
    "strike": 118000.0,
    "call_ask_usd": 2500.0,
    "put_ask_usd": 2320.0,
    "cost_usd": 4820.0,
    "breakeven_down": 113180.0,
    "breakeven_up": 122820.0,
    "atm_iv": 64.8,
    "iv_percentile": 8.2,
    "iv_percentile_n_obs": 1405,
    "iv_percentile_window_days": 112.0,
    "rv": 39.4,
    "rv_iv_ratio": 0.61,
    "vrp": 25.4,
    "min_pnl_score": 1240.0,
    "deribit_url": "https://www.deribit.com/options/BTC/BTC-25SEP26-118000-C",
}


class TestSaveStraddleScan:
    def test_inserts_row_and_returns_true_on_success(self):
        repo = _make_repo()
        ctx, mock_cursor = _patched(repo, fetchone=(1,))
        try:
            result = repo.save_straddle_scan(_ROW)
        finally:
            ctx.stop()

        assert result is True
        sql = mock_cursor.execute.call_args[0][0]
        assert "straddle_scan_history" in sql
        assert "ON CONFLICT" in sql
        assert "DO NOTHING" in sql

    def test_dedup_conflict_returns_false(self):
        """ON CONFLICT DO NOTHING with no RETURNING row -> already recorded this cycle."""
        repo = _make_repo()
        ctx, mock_cursor = _patched(repo, fetchone=None)
        try:
            result = repo.save_straddle_scan(_ROW)
        finally:
            ctx.stop()

        assert result is False


class TestGetUnresolvedStraddleScans:
    def test_returns_list_of_dicts(self):
        repo = _make_repo()
        rows = [(1, "25SEP26", datetime(2026, 7, 17, 12, 0), 118000.0, 4820.0)]
        ctx, mock_cursor = _patched(repo, fetchall=rows)
        try:
            result = repo.get_unresolved_straddle_scans("BTC")
        finally:
            ctx.stop()

        assert result == [{
            "id": 1, "expiration": "25SEP26",
            "scan_time": datetime(2026, 7, 17, 12, 0),
            "strike": 118000.0, "cost_usd": 4820.0,
        }]
        sql = mock_cursor.execute.call_args[0][0]
        assert "resolved_at IS NULL" in sql


class TestResolveStraddleScan:
    def test_updates_settlement_fields(self):
        repo = _make_repo()
        ctx, mock_cursor = _patched(repo)
        try:
            repo.resolve_straddle_scan(
                scan_id=1,
                settlement_index_price=120500.0,
                settlement_pnl_usd=1680.0,
                settlement_return_pct=34.85,
                resolved_at=datetime(2026, 9, 25, 8, 0, tzinfo=timezone.utc),
            )
        finally:
            ctx.stop()

        sql = mock_cursor.execute.call_args[0][0]
        params = mock_cursor.execute.call_args[0][1]
        assert "UPDATE straddle_scan_history" in sql
        assert "settlement_index_price" in sql
        assert 1 in params or 1 == params[-1]


class TestGetLastAlertForExpiry:
    def test_returns_last_alerted_row(self):
        repo = _make_repo()
        row = (8.2, datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc))
        ctx, mock_cursor = _patched(repo, fetchone=row)
        try:
            result = repo.get_last_alert_for_expiry("BTC", "25SEP26")
        finally:
            ctx.stop()

        assert result == {
            "iv_percentile": 8.2,
            "alert_sent_at": datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
        }
        sql = mock_cursor.execute.call_args[0][0]
        assert "alert_sent = TRUE" in sql or "alert_sent = true" in sql.lower()

    def test_returns_none_when_never_alerted(self):
        repo = _make_repo()
        ctx, mock_cursor = _patched(repo, fetchone=None)
        try:
            result = repo.get_last_alert_for_expiry("BTC", "25SEP26")
        finally:
            ctx.stop()

        assert result is None


class TestMarkStraddleScanAlertSent:
    def test_updates_alert_sent_flag(self):
        repo = _make_repo()
        ctx, mock_cursor = _patched(repo)
        try:
            repo.mark_straddle_scan_alert_sent(
                currency="BTC", expiration="25SEP26",
                scan_time=datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc),
            )
        finally:
            ctx.stop()

        sql = mock_cursor.execute.call_args[0][0]
        assert "alert_sent = TRUE" in sql or "alert_sent = true" in sql.lower()
        assert "alert_sent_at" in sql
