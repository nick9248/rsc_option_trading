"""
Unit tests for VolatilityReconstructionService's VEX/CEX exposure-profile
persistence isolation (institutional_metrics_spec.md section 4, Task C5).

Task C4's review lesson (Important #2, applied here proactively): a new
calculation must never share a try block with -- or run before -- a
pre-existing call it could suppress on failure. Here the pre-existing call
is ``self.repo.save_volatility_snapshot(...)`` (already saves atm_iv,
net_vanna, VRP, etc.); the new VEX/CEX aggregate computation must be
isolated in its own try/except so a failure in it never drops that save.
"""

from datetime import datetime
from unittest.mock import MagicMock, patch

from coding.core.analytics.exposure_profile_calculator import ExposureProfileCalculator
from coding.service.on_chain.volatility_reconstruction_service import (
    VolatilityReconstructionService,
)


def _instrument(instrument_name, strike, option_type, mark_iv=40.0, oi=100,
                 delta=0.5, gamma=0.00001, theta=-1.0, vega=1.0, index_price=64000.0):
    return {
        "instrument_name": instrument_name,
        "strike": strike,
        "option_type": option_type,
        "mark_iv": mark_iv,
        "delta": delta,
        "gamma": gamma,
        "theta": theta,
        "vega": vega,
        "open_interest": oi,
        "index_price": index_price,
    }


class TestExposureAggregatesNeverRaise:
    """The new method itself must swallow any failure and return an
    all-None dict -- callers never need their own try/except around it."""

    def test_returns_none_fields_on_calculator_failure(self, caplog):
        service = VolatilityReconstructionService.__new__(VolatilityReconstructionService)
        service.repo = MagicMock()

        with patch.object(
            ExposureProfileCalculator, "calculate", side_effect=RuntimeError("boom"),
        ):
            with caplog.at_level("WARNING"):
                result = service._calculate_exposure_aggregates(
                    "BTC", datetime(2026, 1, 1), "02APR26",
                    [_instrument("BTC-02APR26-64000-C", 64000, "C")],
                    64000.0,
                )

        assert result == {
            "vex_holder": None, "cex_holder": None,
            "vex_assumed_dealer": None, "cex_assumed_dealer": None,
            "vex_peak_strike": None, "cex_peak_strike": None,
        }
        assert any("exposure" in r.message.lower() for r in caplog.records)

    def test_happy_path_returns_computed_values(self):
        service = VolatilityReconstructionService.__new__(VolatilityReconstructionService)
        service.repo = MagicMock()

        instruments = [
            _instrument("BTC-02APR26-64000-C", 64000, "C", oi=1000),
            _instrument("BTC-02APR26-64000-P", 64000, "P", oi=400),
        ]
        result = service._calculate_exposure_aggregates(
            "BTC", datetime(2026, 1, 1), "02APR26", instruments, 64000.0,
        )
        assert result["vex_holder"] is not None
        assert result["vex_assumed_dealer"] is not None
        assert result["vex_peak_strike"] == 64000.0


class TestReconstructOneIsolation:
    """
    _reconstruct_one's pre-existing save (atm_iv, net_vanna, VRP, market
    metrics) must succeed even when the new VEX/CEX exposure-aggregate
    computation blows up internally. _reconstruct_vrp/_reconstruct_market_
    metrics are stubbed to isolate this from unrelated collaborators; the
    real ExposureProfileCalculator.calculate is made to raise so the REAL
    (not mocked) ``_calculate_exposure_aggregates`` isolation is exercised
    end to end.
    """

    def _make_service(self):
        service = VolatilityReconstructionService.__new__(VolatilityReconstructionService)
        service.repo = MagicMock()
        service.repo.get_hourly_snapshots_for_hour.return_value = [
            _instrument("BTC-02APR26-64000-C", 64000, "C"),
        ]
        service.repo.get_trades_for_hour_and_expiration.return_value = []
        return service

    def test_exposure_failure_does_not_prevent_save(self):
        service = self._make_service()

        with patch.object(service, "_reconstruct_vrp", return_value={
            "vrp_absolute": None, "vrp_percentage": None, "realized_vol": None,
        }), patch.object(service, "_reconstruct_market_metrics", return_value={
            "iv_percentile_365d": None, "iv_rank_365d": None,
            "expected_daily_move": None, "expected_weekly_move": None,
            "expected_monthly_move": None,
        }), patch.object(
            ExposureProfileCalculator, "calculate", side_effect=RuntimeError("boom"),
        ):
            saved = service._reconstruct_one("BTC", datetime(2026, 1, 1), "02APR26")

        assert saved is True
        assert service.repo.save_volatility_snapshot.called
        metrics_arg = service.repo.save_volatility_snapshot.call_args[0][3]
        assert metrics_arg["vex_holder"] is None
        assert metrics_arg["cex_holder"] is None
        # Pre-existing fields must still be present and unaffected.
        assert "atm_iv" in metrics_arg

    def test_exposure_success_populates_metrics(self):
        service = self._make_service()
        service.repo.get_hourly_snapshots_for_hour.return_value = [
            _instrument("BTC-02APR26-64000-C", 64000, "C", oi=1000),
            _instrument("BTC-02APR26-64000-P", 64000, "P", oi=400),
        ]

        with patch.object(service, "_reconstruct_vrp", return_value={
            "vrp_absolute": None, "vrp_percentage": None, "realized_vol": None,
        }), patch.object(service, "_reconstruct_market_metrics", return_value={
            "iv_percentile_365d": None, "iv_rank_365d": None,
            "expected_daily_move": None, "expected_weekly_move": None,
            "expected_monthly_move": None,
        }):
            saved = service._reconstruct_one("BTC", datetime(2026, 1, 1), "02APR26")

        assert saved is True
        metrics_arg = service.repo.save_volatility_snapshot.call_args[0][3]
        assert metrics_arg["vex_holder"] is not None
        assert metrics_arg["vex_assumed_dealer"] is not None
        assert metrics_arg["vex_peak_strike"] == 64000.0
