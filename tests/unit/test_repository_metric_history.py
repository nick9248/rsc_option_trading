"""
Unit tests for DatabaseRepository.get_metric_history and
get_metric_freshness.

institutional_metrics_spec.md section 1(c): a generic (table, column)
history reader behind a whitelist, feeding HistoricalNormalizer, plus
(C1 review Important #4) a freshness reader for the "STALE: history ends
{ts}" report gate. Mocked cursor only -- no live database.
"""

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from coding.core.database.repository import DatabaseRepository


def _make_repo():
    return DatabaseRepository.__new__(DatabaseRepository)


def _patched(repo, rows):
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = rows
    ctx = patch.object(repo, "_db_cursor")
    mock_ctx = ctx.start()
    mock_ctx.return_value.__enter__ = lambda s: mock_cursor
    mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
    return ctx, mock_cursor


def _patched_one(repo, row):
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = row
    ctx = patch.object(repo, "_db_cursor")
    mock_ctx = ctx.start()
    mock_ctx.return_value.__enter__ = lambda s: mock_cursor
    mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
    return ctx, mock_cursor


class TestWhitelist:
    def test_unwhitelisted_table_column_pair_raises(self):
        repo = _make_repo()
        with pytest.raises(ValueError, match="not whitelisted"):
            repo.get_metric_history(
                table="onchain_analysis_snapshots",
                column="some_arbitrary_column",
                currency="BTC",
                lookback_hours=720,
            )

    def test_unwhitelisted_table_raises(self):
        repo = _make_repo()
        with pytest.raises(ValueError, match="not whitelisted"):
            repo.get_metric_history(
                table="pg_shadow",
                column="passwd",
                currency="BTC",
                lookback_hours=720,
            )


class TestQueryShape:
    def test_per_expiry_query_filters_on_expiration(self):
        repo = _make_repo()
        ctx, mock_cursor = _patched(repo, [])
        try:
            repo.get_metric_history(
                table="onchain_analysis_snapshots",
                column="total_net_gex",
                currency="BTC",
                lookback_hours=2160,
                expiration="25DEC26",
            )
        finally:
            ctx.stop()

        sql, params = mock_cursor.execute.call_args[0]
        assert "total_net_gex" in sql
        assert "onchain_analysis_snapshots" in sql
        assert "expiration" in sql
        assert "BTC" in params and "25DEC26" in params

    def test_market_wide_query_has_no_expiration_filter(self):
        repo = _make_repo()
        ctx, mock_cursor = _patched(repo, [])
        try:
            repo.get_metric_history(
                table="volatility_index_history",
                column="dvol",
                currency="BTC",
                lookback_hours=2160,
                time_column="date",
            )
        finally:
            ctx.stop()

        sql, params = mock_cursor.execute.call_args[0]
        assert "dvol" in sql
        assert "expiration" not in sql
        assert "date" in sql


class TestResultShape:
    def test_oldest_first_nulls_dropped_decimal_cast(self):
        repo = _make_repo()
        ctx, _ = _patched(repo, [
            (Decimal("1.5"),),
            (None,),
            (Decimal("2.25"),),
        ])
        try:
            result = repo.get_metric_history(
                table="onchain_volatility_snapshots",
                column="vrp_absolute",
                currency="BTC",
                lookback_hours=2160,
                expiration="25DEC26",
            )
        finally:
            ctx.stop()

        assert result == [1.5, 2.25]
        assert all(isinstance(v, float) for v in result)

    def test_empty_history_returns_empty_list(self):
        repo = _make_repo()
        ctx, _ = _patched(repo, [])
        try:
            result = repo.get_metric_history(
                table="funding_rate_history",
                column="funding_rate",
                currency="BTC",
                lookback_hours=720,
                time_column="date",
            )
        finally:
            ctx.stop()

        assert result == []


class TestVolatilitySkewHistoryWhitelist:
    """
    institutional_metrics_spec.md section 3(c): rr_25d/bf_25d are
    whitelisted for percentile windows via the SAME generic reader,
    additionally filtered ``WHERE n_quotes_used >= 8`` so thin-chain rows
    (the class of degeneracy this whole table replaces, F2) never feed a
    percentile.
    """

    def test_rr_25d_and_bf_25d_are_whitelisted(self):
        repo = _make_repo()
        ctx, mock_cursor = _patched(repo, [])
        try:
            repo.get_metric_history(
                table="volatility_skew_history", column="rr_25d",
                currency="BTC", lookback_hours=720, expiration="25JUL26",
            )
            repo.get_metric_history(
                table="volatility_skew_history", column="bf_25d",
                currency="BTC", lookback_hours=720, expiration="25JUL26",
            )
        finally:
            ctx.stop()
        assert mock_cursor.execute.call_count == 2

    def test_query_filters_on_n_quotes_used(self):
        repo = _make_repo()
        ctx, mock_cursor = _patched(repo, [])
        try:
            repo.get_metric_history(
                table="volatility_skew_history", column="rr_25d",
                currency="BTC", lookback_hours=720, expiration="25JUL26",
            )
        finally:
            ctx.stop()
        sql, params = mock_cursor.execute.call_args[0]
        assert "n_quotes_used >= 8" in sql
        assert "rr_25d" in sql and "volatility_skew_history" in sql

    def test_other_whitelisted_tables_unaffected_by_the_extra_filter(self):
        """The n_quotes_used filter is specific to volatility_skew_history
        -- it must not leak into other tables' queries."""
        repo = _make_repo()
        ctx, mock_cursor = _patched(repo, [])
        try:
            repo.get_metric_history(
                table="onchain_analysis_snapshots", column="total_net_gex",
                currency="BTC", lookback_hours=720, expiration="25JUL26",
            )
        finally:
            ctx.stop()
        sql, _ = mock_cursor.execute.call_args[0]
        assert "n_quotes_used" not in sql


class TestGetMetricFreshness:
    def test_returns_max_timestamp(self):
        repo = _make_repo()
        ts = datetime(2026, 7, 26, 15, 0)
        ctx, mock_cursor = _patched_one(repo, (ts,))
        try:
            result = repo.get_metric_freshness(
                table="onchain_analysis_snapshots", currency="BTC", expiration="25DEC26",
            )
        finally:
            ctx.stop()
        assert result == ts
        sql, params = mock_cursor.execute.call_args[0]
        assert "MAX(snapshot_hour)" in sql
        assert "expiration" in sql
        assert params == ("BTC", "25DEC26")

    def test_market_wide_table_no_expiration_filter(self):
        repo = _make_repo()
        ts = datetime(2026, 7, 26, 0, 0)
        ctx, mock_cursor = _patched_one(repo, (ts,))
        try:
            result = repo.get_metric_freshness(table="volatility_index_history", currency="BTC")
        finally:
            ctx.stop()
        assert result == ts
        sql, params = mock_cursor.execute.call_args[0]
        assert "MAX(date)" in sql
        assert "expiration" not in sql
        assert params == ("BTC",)

    def test_unwhitelisted_table_returns_none(self):
        repo = _make_repo()
        result = repo.get_metric_freshness(table="pg_shadow", currency="BTC")
        assert result is None

    def test_no_rows_returns_none(self):
        repo = _make_repo()
        ctx, _ = _patched_one(repo, (None,))
        try:
            result = repo.get_metric_freshness(table="funding_rate_history", currency="BTC")
        finally:
            ctx.stop()
        assert result is None

    def test_query_failure_returns_none_not_raise(self):
        repo = _make_repo()
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = RuntimeError("db down")
        ctx = patch.object(repo, "_db_cursor")
        mock_ctx = ctx.start()
        mock_ctx.return_value.__enter__ = lambda s: mock_cursor
        mock_ctx.return_value.__exit__ = MagicMock(return_value=False)
        try:
            result = repo.get_metric_freshness(table="funding_rate_history", currency="BTC")
        finally:
            ctx.stop()
        assert result is None
