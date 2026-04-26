import json
from datetime import datetime, date, timezone
from unittest.mock import MagicMock, patch
import pytest

from coding.core.displacement.models.displacement_signal import DisplacementSignal


def _make_signal(**kwargs):
    defaults = dict(
        asset="BTC", detected_at=datetime(2026, 4, 25, 10, 0, tzinfo=timezone.utc),
        drop_24h_pct=0.22, drop_1h_pct=0.09,
        conviction_pct=75.0, conviction_label="HIGH",
        score_drop_magnitude=82.0, score_drop_speed=55.0,
        score_funding_rate=91.0, score_dvol_spike=71.0,
        score_max_pain=64.0, score_term_structure=80.0,
        funding_rate_value=-0.008, dvol_sigma=2.1,
        max_pain_distance_pct=0.09, term_structure_inversion_pct=0.06,
        instrument_name="BTC-25SEP26-70000-C",
        strike=70000.0, expiry_date=date(2026, 9, 25),
        dte=153, delta=0.14, mark_iv=0.87, premium_usd=1240.0,
        target_50pct_price=98400.0, target_100pct_price=107200.0, target_200pct_price=124800.0,
    )
    defaults.update(kwargs)
    return DisplacementSignal(**defaults)


class TestRepositoryDisplacementMethods:
    @patch("coding.core.database.repository.ConnectionPool")
    def test_save_displacement_signal_executes_insert(self, mock_pool_cls):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.__enter__ = lambda s: mock_conn
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_pool_cls.return_value.get_connection.return_value = mock_conn

        # Use _db_cursor context manager pattern
        from coding.core.database.repository import DatabaseRepository
        repo = DatabaseRepository.__new__(DatabaseRepository)
        repo.pool = mock_pool_cls.return_value
        repo.config = MagicMock()

        inserted_sql = []
        def fake_db_cursor():
            from contextlib import contextmanager
            @contextmanager
            def _ctx():
                yield mock_cursor
            return _ctx()
        repo._db_cursor = fake_db_cursor

        signal = _make_signal()
        repo.save_displacement_signal(signal)

        assert mock_cursor.execute.called
        sql_call = mock_cursor.execute.call_args[0][0]
        assert "INSERT INTO displacement_signals" in sql_call

    @patch("coding.core.database.repository.ConnectionPool")
    def test_get_last_displacement_signal_returns_dict(self, mock_pool_cls):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (
            1, "BTC", datetime(2026, 4, 25, 10, 0, tzinfo=timezone.utc),
            0.22, 0.09, 75.0, "HIGH",
            "BTC-25SEP26-70000-C", 70000.0, date(2026, 9, 25),
            153, 0.14, 0.87, 1240.0, json.dumps({}), False,
        )
        mock_cursor.description = [
            ("id",), ("asset",), ("detected_at",), ("drop_24h_pct",), ("drop_1h_pct",),
            ("conviction_pct",), ("conviction_label",), ("instrument_name",),
            ("strike",), ("expiry_date",), ("dte",), ("delta",), ("mark_iv",),
            ("premium_usd",), ("signal_breakdown",), ("telegram_sent",),
        ]

        from coding.core.database.repository import DatabaseRepository
        repo = DatabaseRepository.__new__(DatabaseRepository)
        repo.pool = mock_pool_cls.return_value
        repo.config = MagicMock()

        from contextlib import contextmanager
        @contextmanager
        def fake_db_cursor():
            yield mock_cursor
        repo._db_cursor = fake_db_cursor

        result = repo.get_last_displacement_signal("BTC")

        assert result is not None
        assert result["asset"] == "BTC"
        assert result["conviction_label"] == "HIGH"

    @patch("coding.core.database.repository.ConnectionPool")
    def test_get_last_displacement_signal_returns_none_when_empty(self, mock_pool_cls):
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None

        from coding.core.database.repository import DatabaseRepository
        repo = DatabaseRepository.__new__(DatabaseRepository)
        repo.pool = mock_pool_cls.return_value
        repo.config = MagicMock()

        from contextlib import contextmanager
        @contextmanager
        def fake_db_cursor():
            yield mock_cursor
        repo._db_cursor = fake_db_cursor

        result = repo.get_last_displacement_signal("ETH")
        assert result is None
