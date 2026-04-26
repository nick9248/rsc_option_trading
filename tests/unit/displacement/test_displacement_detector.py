from datetime import datetime, timezone
import pytest
from coding.core.displacement.models.displacement_config import DisplacementConfig
from coding.core.displacement.displacement_detector import DisplacementDetector


def _prices(now=80000.0, h1=87000.0, h4=92000.0, h24=100000.0, h7d=110000.0):
    return {"now": now, "1h_ago": h1, "4h_ago": h4, "24h_ago": h24, "7d_ago": h7d}


class TestDisplacementDetector:
    def setup_method(self):
        self.cfg = DisplacementConfig()
        self.detector = DisplacementDetector(self.cfg)

    def test_no_event_when_drop_below_all_thresholds(self):
        # Only 5% 24h drop — below 20% threshold
        prices = _prices(now=95000.0, h1=95500.0, h4=97000.0, h24=100000.0, h7d=102000.0)
        result = self.detector.check("BTC", prices)
        assert result is None

    def test_event_fired_when_24h_threshold_exceeded(self):
        # 22% 24h drop
        prices = _prices(now=78000.0, h1=80000.0, h4=85000.0, h24=100000.0, h7d=105000.0)
        result = self.detector.check("BTC", prices)
        assert result is not None
        assert result.asset == "BTC"
        assert result.triggering_timeframe == "24h"
        assert abs(result.drop_24h_pct - 0.22) < 0.01

    def test_event_fired_when_1h_threshold_exceeded(self):
        # 10% 1h drop (above 8% threshold)
        prices = _prices(now=90000.0, h1=100000.0, h4=100500.0, h24=101000.0, h7d=102000.0)
        result = self.detector.check("BTC", prices)
        assert result is not None
        assert result.triggering_timeframe == "1h"

    def test_cooldown_prevents_second_event(self):
        prices = _prices(now=78000.0, h1=80000.0, h4=85000.0, h24=100000.0, h7d=105000.0)
        first = self.detector.check("BTC", prices)
        assert first is not None
        # Second check within cooldown window
        second = self.detector.check("BTC", prices)
        assert second is None

    def test_different_assets_independent_cooldown(self):
        eth_prices = _prices(now=1200.0, h1=1230.0, h4=1300.0, h24=1540.0, h7d=1600.0)
        btc_prices = _prices(now=78000.0, h1=80000.0, h4=85000.0, h24=100000.0, h7d=105000.0)
        eth_event = self.detector.check("ETH", eth_prices)
        btc_event = self.detector.check("BTC", btc_prices)
        assert eth_event is not None
        assert btc_event is not None

    def test_drop_pct_values_are_positive(self):
        # Drops stored as positive fractions
        prices = _prices(now=80000.0, h1=90000.0, h4=92000.0, h24=100000.0, h7d=105000.0)
        event = self.detector.check("BTC", prices)
        if event:
            assert event.drop_24h_pct > 0
            assert event.drop_1h_pct > 0

    def test_event_contains_correct_current_price(self):
        prices = _prices(now=78000.0, h1=80000.0, h4=85000.0, h24=100000.0, h7d=105000.0)
        event = self.detector.check("BTC", prices)
        assert event is not None
        assert event.current_price == 78000.0

    def test_7d_threshold_triggers_event(self):
        # 32% 7d drop (above 30% threshold), smaller drops in other timeframes
        prices = _prices(now=68000.0, h1=68200.0, h4=68500.0, h24=69000.0, h7d=100000.0)
        event = self.detector.check("BTC", prices)
        assert event is not None
        assert event.triggering_timeframe == "7d"
