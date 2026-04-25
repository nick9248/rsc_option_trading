"""Tests that LabelGenerator computes realized_vol_24h from FORWARD prices."""
import math
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch, call
import numpy as np
import pytest

from coding.core.ml.label_generator import LabelGenerator


def _make_hourly_prices(base_price: float, n: int, step: float = 0.0):
    """Return list of price dicts with a known vol profile."""
    base_ts = datetime(2026, 4, 10, 0, 0)
    return [{"timestamp": base_ts + timedelta(hours=h), "price": base_price * (1 + step * h)}
            for h in range(n)]


def _annualized_vol(prices: list) -> float:
    arr = np.array([p["price"] for p in prices])
    log_ret = np.diff(np.log(arr))
    return float(np.std(log_ret) * np.sqrt(24 * 365) * 100)


class TestForwardVolLabels:

    def _make_generator(self):
        gen = LabelGenerator.__new__(LabelGenerator)
        gen.repo = MagicMock()
        return gen

    def test_forward_prices_query_covers_24h_window(self):
        """_get_forward_prices should query [timestamp, timestamp+24h]."""
        gen = self._make_generator()
        conn = MagicMock()
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        conn.cursor.return_value = cursor
        gen.repo._get_connection.return_value = conn

        ts = datetime(2026, 4, 10, 12, 0)
        gen._get_forward_prices("BTC", ts, forward_hours=24)

        cursor.execute.assert_called_once()
        sql, params = cursor.execute.call_args[0]
        assert params[1] == ts                          # start = timestamp
        assert params[2] == ts + timedelta(hours=24)    # end = timestamp + 24h

    def test_generate_labels_rv24h_uses_forward_prices(self):
        """realized_vol_24h in labels must equal vol computed from FORWARD prices."""
        gen = self._make_generator()
        ts = datetime(2026, 4, 10, 12, 0)

        # Past prices: calm (near-zero returns)
        calm = _make_hourly_prices(80000, 720, step=0.00001)
        # Forward prices: volatile (1% step each hour)
        volatile = _make_hourly_prices(80000, 25, step=0.01)

        with patch.object(gen, '_get_price_history', return_value=calm), \
             patch.object(gen, '_get_forward_prices', return_value=volatile), \
             patch.object(gen, '_calculate_iv_metrics', return_value=(50.0, 'contango')):

            labels = gen.generate_labels("BTC", ts)

        expected_vol = _annualized_vol(volatile[-24:])
        assert labels is not None
        assert abs(labels.realized_vol_24h - expected_vol) < 0.1

    def test_generate_labels_rv24h_is_none_when_insufficient_forward_data(self):
        """If fewer than 3 forward price points exist, realized_vol_24h should be None."""
        gen = self._make_generator()
        ts = datetime(2026, 4, 10, 12, 0)

        calm = _make_hourly_prices(80000, 720, step=0.00001)
        sparse_forward = _make_hourly_prices(80000, 2, step=0.01)  # only 2 points

        with patch.object(gen, '_get_price_history', return_value=calm), \
             patch.object(gen, '_get_forward_prices', return_value=sparse_forward), \
             patch.object(gen, '_calculate_iv_metrics', return_value=(50.0, 'contango')):

            labels = gen.generate_labels("BTC", ts)

        assert labels is None or labels.realized_vol_24h is None

    def test_forward_prices_returns_correct_format(self):
        """_get_forward_prices must return list of dicts with 'price' key."""
        gen = self._make_generator()
        conn = MagicMock()
        cursor = MagicMock()
        ts = datetime(2026, 4, 10, 12, 0)
        cursor.fetchall.return_value = [
            (datetime(2026, 4, 10, 12), 80000.0),
            (datetime(2026, 4, 10, 13), 80100.0),
        ]
        conn.cursor.return_value = cursor
        gen.repo._get_connection.return_value = conn

        result = gen._get_forward_prices("BTC", ts, forward_hours=24)

        assert len(result) == 2
        assert result[0]["price"] == 80000.0
        assert result[1]["price"] == 80100.0
