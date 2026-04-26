from datetime import datetime, date, timezone
import pytest
from pydantic import ValidationError

from coding.core.displacement.models.displacement_config import DisplacementConfig
from coding.core.displacement.models.displacement_event import DisplacementEvent
from coding.core.displacement.models.displacement_signal import DisplacementSignal


class TestDisplacementConfig:
    def test_default_values(self):
        cfg = DisplacementConfig()
        assert cfg.drop_24h_threshold == 0.20
        assert cfg.min_delta == 0.10
        assert cfg.max_delta == 0.20
        assert cfg.preferred_delta == 0.15
        assert cfg.min_dte == 90
        assert cfg.max_dte == 270
        assert cfg.alert_high_threshold == 0.70
        assert cfg.alert_medium_threshold == 0.50

    def test_frozen(self):
        cfg = DisplacementConfig()
        with pytest.raises(ValidationError):
            cfg.drop_24h_threshold = 0.30

    def test_invalid_threshold_raises(self):
        with pytest.raises(ValidationError):
            DisplacementConfig(drop_24h_threshold=1.5)

    def test_custom_values(self):
        cfg = DisplacementConfig(drop_24h_threshold=0.15, risk_budget_usd=5000.0)
        assert cfg.drop_24h_threshold == 0.15
        assert cfg.risk_budget_usd == 5000.0


class TestDisplacementEvent:
    def test_create(self):
        event = DisplacementEvent(
            asset="BTC",
            detected_at=datetime(2026, 4, 25, 10, 0, 0),
            current_price=75000.0,
            drop_1h_pct=0.09,
            drop_4h_pct=0.13,
            drop_24h_pct=0.22,
            drop_7d_pct=0.28,
            triggering_timeframe="24h",
        )
        assert event.asset == "BTC"
        assert event.drop_24h_pct == 0.22
        assert event.triggering_timeframe == "24h"

    def test_frozen(self):
        event = DisplacementEvent(
            asset="ETH", detected_at=datetime.now(timezone.utc), current_price=1500.0,
            drop_1h_pct=0.05, drop_4h_pct=0.08, drop_24h_pct=0.20,
            drop_7d_pct=0.25, triggering_timeframe="24h",
        )
        with pytest.raises(ValidationError):
            event.asset = "BTC"


class TestDisplacementSignal:
    def _make_signal(self, **kwargs):
        defaults = dict(
            asset="BTC", detected_at=datetime.now(timezone.utc),
            drop_24h_pct=0.22, drop_1h_pct=0.09,
            conviction_pct=75.0, conviction_label="HIGH",
            score_drop_magnitude=82.0, score_drop_speed=55.0,
            score_funding_rate=91.0, score_dvol_spike=71.0,
            score_max_pain=64.0, score_term_structure=80.0,
            funding_rate_value=-0.008, dvol_sigma=2.1,
            max_pain_distance_pct=0.09, term_structure_inversion_pct=0.06,
        )
        defaults.update(kwargs)
        return DisplacementSignal(**defaults)

    def test_create_without_contract(self):
        sig = self._make_signal()
        assert sig.instrument_name is None
        assert sig.conviction_label == "HIGH"

    def test_create_with_contract(self):
        sig = self._make_signal(
            instrument_name="BTC-25SEP26-70000-C",
            strike=70000.0,
            expiry_date=date(2026, 9, 25),
            dte=153,
            delta=0.14,
            mark_iv=0.87,
            premium_usd=1240.0,
            target_50pct_price=98400.0,
            target_100pct_price=107200.0,
            target_200pct_price=124800.0,
        )
        assert sig.instrument_name == "BTC-25SEP26-70000-C"
        assert sig.dte == 153

    def test_score_out_of_range_raises(self):
        with pytest.raises(ValidationError):
            self._make_signal(conviction_pct=101.0)

    def test_score_below_zero_raises(self):
        with pytest.raises(ValidationError):
            self._make_signal(score_dvol_spike=-1.0)


class TestDisplacementConfigValidation:
    def test_inverted_delta_range_raises(self):
        with pytest.raises(ValidationError):
            DisplacementConfig(min_delta=0.30, max_delta=0.10)

    def test_preferred_delta_outside_range_raises(self):
        with pytest.raises(ValidationError):
            DisplacementConfig(min_delta=0.10, max_delta=0.20, preferred_delta=0.50)

    def test_inverted_dte_range_raises(self):
        with pytest.raises(ValidationError):
            DisplacementConfig(min_dte=200, max_dte=90)

    def test_inverted_alert_thresholds_raises(self):
        with pytest.raises(ValidationError):
            DisplacementConfig(alert_medium_threshold=0.80, alert_high_threshold=0.40)

    def test_negative_cooldown_raises(self):
        with pytest.raises(ValidationError):
            DisplacementConfig(cooldown_hours=-1)


class TestDisplacementEventValidation:
    def test_negative_price_raises(self):
        with pytest.raises(ValidationError):
            DisplacementEvent(
                asset="BTC", detected_at=datetime.now(timezone.utc),
                current_price=-100.0,
                drop_1h_pct=0.09, drop_4h_pct=0.13,
                drop_24h_pct=0.22, drop_7d_pct=0.28,
                triggering_timeframe="24h",
            )

    def test_invalid_asset_raises(self):
        with pytest.raises(ValidationError):
            DisplacementEvent(
                asset="DOGE", detected_at=datetime.now(timezone.utc),
                current_price=1.0,
                drop_1h_pct=0.09, drop_4h_pct=0.13,
                drop_24h_pct=0.22, drop_7d_pct=0.28,
                triggering_timeframe="24h",
            )

    def test_invalid_timeframe_raises(self):
        with pytest.raises(ValidationError):
            DisplacementEvent(
                asset="BTC", detected_at=datetime.now(timezone.utc),
                current_price=80000.0,
                drop_1h_pct=0.09, drop_4h_pct=0.13,
                drop_24h_pct=0.22, drop_7d_pct=0.28,
                triggering_timeframe="12h",
            )


class TestDisplacementSignalValidation:
    def test_invalid_conviction_label_raises(self):
        with pytest.raises(ValidationError):
            DisplacementSignal(
                asset="BTC", detected_at=datetime.now(timezone.utc),
                drop_24h_pct=0.22, drop_1h_pct=0.09,
                conviction_pct=75.0, conviction_label="YOLO",
                score_drop_magnitude=80.0, score_drop_speed=55.0,
                score_funding_rate=90.0, score_dvol_spike=70.0,
                score_max_pain=60.0, score_term_structure=80.0,
                funding_rate_value=-0.008, dvol_sigma=2.1,
                max_pain_distance_pct=0.09, term_structure_inversion_pct=0.06,
            )

    def test_partial_contract_fields_raises(self):
        with pytest.raises(ValidationError):
            DisplacementSignal(
                asset="BTC", detected_at=datetime.now(timezone.utc),
                drop_24h_pct=0.22, drop_1h_pct=0.09,
                conviction_pct=75.0, conviction_label="HIGH",
                score_drop_magnitude=80.0, score_drop_speed=55.0,
                score_funding_rate=90.0, score_dvol_spike=70.0,
                score_max_pain=60.0, score_term_structure=80.0,
                funding_rate_value=-0.008, dvol_sigma=2.1,
                max_pain_distance_pct=0.09, term_structure_inversion_pct=0.06,
                instrument_name="BTC-25SEP26-70000-C",  # set but strike/dte/delta etc are None
            )
