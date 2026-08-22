"""
Unit tests for VolatilityReconstructionService._reconstruct_vrp's None
handling of VRPCalculator's now-Optional returns (Wave H Task H-D).

Before this task, VRPCalculator.calculate_realized_volatility/
calculate_average_iv returned a fabricated 0.0 on insufficient data, and
this call site immediately did ``float(vrp_calc.calculate_realized_
volatility(...))`` -- float(0.0) never raised, so the pre-existing
``if realized_vol <= 0`` guard silently worked around the fabrication.
Now that those methods return None instead, ``float(None)`` raises
TypeError unless the None check happens BEFORE the float() cast. These
tests prove that ordering fix: no crash, and the same insufficient-data
dict shape as before.
"""
from datetime import datetime
from unittest.mock import MagicMock

from coding.core.analytics.vrp_calculator import VRPCalculator
from coding.service.on_chain.volatility_reconstruction_service import (
    VolatilityReconstructionService,
)


def _make_service():
    service = VolatilityReconstructionService.__new__(VolatilityReconstructionService)
    service.repo = MagicMock()
    service.repo.get_ohlcv_by_date_range.return_value = [
        {"date": datetime(2026, 6, d, 0, 0, 0), "close": 60000.0 + i * 10}
        for i, d in enumerate(range(1, 15))
    ]
    return service


class TestReconstructVrpNoneRealizedVolatility:
    def test_none_realized_volatility_does_not_crash_and_returns_insufficient_dict(self, monkeypatch):
        service = _make_service()

        def fake_calculate_realized_volatility(self, price_history, window_days=None, *, reference_time):
            return None

        monkeypatch.setattr(
            VRPCalculator, "calculate_realized_volatility", fake_calculate_realized_volatility
        )

        snapshot_hour = datetime(2026, 7, 1, 8, 0, 0)
        result = service._reconstruct_vrp("BTC", snapshot_hour, instruments=[], underlying_price=65000.0)

        assert result == {"vrp_absolute": None, "vrp_percentage": None, "realized_vol": None}


class TestReconstructVrpNoneImpliedVolatility:
    def test_none_implied_volatility_does_not_crash_and_preserves_realized_vol(self, monkeypatch):
        """realized_vol computes fine (real, positive); implied_vol is None
        (nothing passed calculate_average_iv's filter) -- must not crash
        on float(None), and must preserve the already-computed realized_vol
        in the returned dict, matching the pre-existing contract."""
        service = _make_service()

        def fake_calculate_realized_volatility(self, price_history, window_days=None, *, reference_time):
            return 0.55

        def fake_calculate_average_iv(self, options_data, moneyness_filter=(0.9, 1.1)):
            return None

        monkeypatch.setattr(
            VRPCalculator, "calculate_realized_volatility", fake_calculate_realized_volatility
        )
        monkeypatch.setattr(VRPCalculator, "calculate_average_iv", fake_calculate_average_iv)

        snapshot_hour = datetime(2026, 7, 1, 8, 0, 0)
        result = service._reconstruct_vrp(
            "BTC", snapshot_hour,
            instruments=[{"mark_iv": 65.0, "strike": 65000.0}],
            underlying_price=65000.0,
        )

        assert result == {"vrp_absolute": None, "vrp_percentage": None, "realized_vol": 0.55}


class TestReconstructVrpSelfContradictionClosed:
    def test_zero_realized_volatility_with_real_iv_returns_insufficient_not_neutral(self, monkeypatch):
        """realized_vol is a real (non-None) but exactly-zero value (e.g. a
        genuinely flat historical price window) with a real, positive
        implied_vol -- calculate_vrp now folds this into None rather than a
        self-contradicting NEUTRAL signal alongside a large vrp_absolute."""
        service = _make_service()

        def fake_calculate_realized_volatility(self, price_history, window_days=None, *, reference_time):
            return 0.0

        monkeypatch.setattr(
            VRPCalculator, "calculate_realized_volatility", fake_calculate_realized_volatility
        )

        snapshot_hour = datetime(2026, 7, 1, 8, 0, 0)
        result = service._reconstruct_vrp("BTC", snapshot_hour, instruments=[], underlying_price=65000.0)

        # realized_vol <= 0 gate fires before implied_vol is even computed
        # (matches the pre-existing early-return shape).
        assert result == {"vrp_absolute": None, "vrp_percentage": None, "realized_vol": None}


class TestReconstructVrpHappyPath:
    def test_real_inputs_still_compute_a_real_vrp(self, monkeypatch):
        service = _make_service()

        def fake_calculate_realized_volatility(self, price_history, window_days=None, *, reference_time):
            return 0.50

        def fake_calculate_average_iv(self, options_data, moneyness_filter=(0.9, 1.1)):
            return 0.65

        monkeypatch.setattr(
            VRPCalculator, "calculate_realized_volatility", fake_calculate_realized_volatility
        )
        monkeypatch.setattr(VRPCalculator, "calculate_average_iv", fake_calculate_average_iv)

        snapshot_hour = datetime(2026, 7, 1, 8, 0, 0)
        result = service._reconstruct_vrp(
            "BTC", snapshot_hour,
            instruments=[{"mark_iv": 65.0, "strike": 65000.0}],
            underlying_price=65000.0,
        )

        assert result["realized_vol"] == 0.50
        assert result["vrp_absolute"] is not None
        assert result["vrp_percentage"] is not None
        assert result["vrp_absolute"] > 0  # IV (0.65) > RV (0.50)
