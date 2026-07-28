"""
Unit tests for DatabaseRepository.get_metric_history.

institutional_metrics_spec.md section 1(c): a generic (table, column)
history reader behind a whitelist, feeding HistoricalNormalizer. Mocked
cursor only -- no live database.
"""

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
