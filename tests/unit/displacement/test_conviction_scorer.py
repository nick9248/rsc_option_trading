from datetime import datetime, timezone
import pytest

from coding.core.displacement.models.displacement_config import DisplacementConfig
from coding.core.displacement.models.displacement_event import DisplacementEvent
from coding.core.displacement.conviction_scorer import ConvictionScorer


def _event(drop_24h=0.22, drop_1h=0.09):
    return DisplacementEvent(
        asset="BTC", detected_at=datetime.now(tz=timezone.utc),
        current_price=78000.0,
        drop_1h_pct=drop_1h, drop_4h_pct=0.13,
        drop_24h_pct=drop_24h, drop_7d_pct=0.28,
        triggering_timeframe="24h",
    )


def _market_data(funding=-0.008, dvol_current=85.0, dvol_history=None, ohlcv=None, options=None):
    if dvol_history is None:
        dvol_history = [50.0] * 90
    if ohlcv is None:
        closes = [100000.0] * 500
        closes[0] = 78000.0
        ohlcv = [{"close": c} for c in closes]
    if options is None:
        options = [
            {"strike": 70000.0, "option_type": "call", "open_interest": 500.0, "dte": 30, "mark_iv": 0.90},
            {"strike": 80000.0, "option_type": "call", "open_interest": 1000.0, "dte": 30, "mark_iv": 0.88},
            {"strike": 90000.0, "option_type": "put", "open_interest": 800.0, "dte": 30, "mark_iv": 0.85},
            {"strike": 70000.0, "option_type": "call", "open_interest": 300.0, "dte": 90, "mark_iv": 0.80},
            {"strike": 80000.0, "option_type": "put", "open_interest": 200.0, "dte": 90, "mark_iv": 0.75},
        ]
    return {
        "funding_rate": funding,
        "dvol_current": dvol_current,
        "dvol_history": dvol_history,
        "ohlcv_history": ohlcv,
        "options_chain": options,
    }


class TestConvictionScorer:
    def setup_method(self):
        self.scorer = ConvictionScorer(DisplacementConfig())

    def test_score_returns_tuple_probability_and_breakdown(self):
        prob, breakdown = self.scorer.score(_event(), _market_data())
        assert 0.0 <= prob <= 100.0
        assert "drop_magnitude" in breakdown
        assert "funding_rate" in breakdown
        assert "dvol_spike" in breakdown
        assert "max_pain" in breakdown
        assert "term_structure" in breakdown
        assert "drop_speed" in breakdown

    def test_deeply_negative_funding_scores_high(self):
        _, breakdown = self.scorer.score(_event(), _market_data(funding=-0.01))
        assert breakdown["funding_rate"] >= 90.0

    def test_positive_funding_scores_low(self):
        _, breakdown = self.scorer.score(_event(), _market_data(funding=0.005))
        assert breakdown["funding_rate"] < 30.0

    def test_dvol_in_sweet_spot_scores_100(self):
        # Mean=50, std=0. Using uniform history, std=0 so returns 50 (neutral).
        # Use varied history with mean~50, std~5 so dvol_current=60 → sigma=2.0 (sweet spot)
        history = [50.0 + (i % 3 - 1) * 5 for i in range(90)]  # alternates 45, 50, 55
        prob, breakdown = self.scorer.score(_event(), _market_data(dvol_current=60.0, dvol_history=history))
        assert breakdown["dvol_spike"] == 100.0

    def test_dvol_way_too_high_is_penalized(self):
        # sigma > 3 should score below 100 (sweet spot ends at 2.5σ)
        history = [50.0 + (i % 3 - 1) * 5 for i in range(90)]
        _, breakdown = self.scorer.score(_event(), _market_data(dvol_current=80.0, dvol_history=history))
        assert breakdown["dvol_spike"] < 100.0

    def test_flash_crash_scores_higher_than_slow_bleed(self):
        flash = _event(drop_24h=0.22, drop_1h=0.20)   # 20% happened in 1h
        bleed = _event(drop_24h=0.22, drop_1h=0.01)   # 1% happened in 1h
        _, b_flash = self.scorer.score(flash, _market_data())
        _, b_bleed = self.scorer.score(bleed, _market_data())
        assert b_flash["drop_speed"] > b_bleed["drop_speed"]

    def test_spot_below_max_pain_scores_high(self):
        # Max pain will be ~80000 (most OI), spot is 70000 → 12.5% below → score 100
        options = [
            {"strike": 80000.0, "option_type": "call", "open_interest": 5000.0, "dte": 30, "mark_iv": 0.88},
            {"strike": 80000.0, "option_type": "put", "open_interest": 5000.0, "dte": 30, "mark_iv": 0.88},
            {"strike": 70000.0, "option_type": "call", "open_interest": 100.0, "dte": 30, "mark_iv": 0.90},
        ]
        event = DisplacementEvent(
            asset="BTC", detected_at=datetime.now(tz=timezone.utc), current_price=70000.0,
            drop_1h_pct=0.09, drop_4h_pct=0.13, drop_24h_pct=0.22,
            drop_7d_pct=0.28, triggering_timeframe="24h",
        )
        _, breakdown = self.scorer.score(event, _market_data(options=options))
        assert breakdown["max_pain"] == 100.0

    def test_scorer_exposes_raw_values(self):
        self.scorer.score(_event(), _market_data())
        assert hasattr(self.scorer, "_last_dvol_sigma")
        assert hasattr(self.scorer, "_last_term_inversion_pct")

    def test_equal_weights_when_no_model(self):
        # Without trained model, probability = average of 6 scores
        prob, breakdown = self.scorer.score(_event(), _market_data())
        expected = sum(breakdown.values()) / 6.0
        assert abs(prob - expected) < 0.01
