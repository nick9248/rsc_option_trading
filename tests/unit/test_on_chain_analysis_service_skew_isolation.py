"""
Unit test for OnChainAnalysisService._calculate_volatility_surface's RR25/BF25
isolation (Task C4 review Important #2).

Before the fix, the new RR25/BF25 calculation ran INSIDE the same try block
as (and before) the pre-existing ``builder.set_vol_surface(...)`` call. A
failure in the new code (e.g. a malformed instrument reaching
``calculate_risk_reversal_butterfly()``) aborted the whole block and silently
dropped the vol-surface builder entry too -- a pre-existing, already-shipped
feature taking collateral damage from a bug in new code. The fix moves the
skew calculation after ``set_vol_surface`` and isolates it in its own
try/except, matching the daemon's own isolation
(``ProspectiveCollector._calculate_and_save_skew``).
"""

from unittest.mock import MagicMock, patch

from coding.core.analytics.volatility_surface_calculator import VolatilitySurfaceCalculator
from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService


def _make_service():
    service = OnChainAnalysisService.__new__(OnChainAnalysisService)
    service.api = MagicMock()
    service.api.get_last_trades_by_currency.return_value = {"trades": []}
    service.repository = None
    return service


def _instrument(strike, option_type, delta, mark_iv=60.0):
    return {
        "instrument_name": f"BTC-27MAR27-{int(strike)}-{option_type}",
        "strike": strike,
        "option_type": option_type,
        "delta": delta,
        "mark_iv": mark_iv,
        "open_interest": 10,
        "volume": 1,
        "bid_price": 1.0,
        "ask_price": 1.0,
    }


class _FakeAnalyzer:
    def __init__(self):
        self.currency = "BTC"
        self.index_price = 70_000.0
        self.forward_price_by_expiration = {"27MAR27": 70_000.0}
        self.enriched_instruments = {
            "27MAR27": [
                _instrument(70_000, "C", 0.50),
                _instrument(70_000, "P", -0.50),
            ]
        }
        self._atm_ivs = {}
        self._skew_by_expiry = {}
        self._recent_trades = []


class TestSkewCalculationIsolatedFromVolSurface:
    def test_skew_failure_does_not_prevent_set_vol_surface(self):
        service = _make_service()
        analyzer = _FakeAnalyzer()
        builder = MagicMock()

        with patch.object(
            VolatilitySurfaceCalculator, "calculate_risk_reversal_butterfly",
            side_effect=RuntimeError("boom"),
        ):
            service._calculate_volatility_surface(analyzer, lambda *a, **k: None, builder)

        # The vol-surface result must still have been stored -- a failure
        # in the NEW RR25/BF25 code must not take out the PRE-EXISTING
        # vol-surface feature.
        assert builder.set_vol_surface.called
        assert "27MAR27" not in analyzer._skew_by_expiry

    def test_skew_failure_is_logged_not_silently_swallowed(self, caplog):
        service = _make_service()
        analyzer = _FakeAnalyzer()

        with patch.object(
            VolatilitySurfaceCalculator, "calculate_risk_reversal_butterfly",
            side_effect=RuntimeError("boom"),
        ):
            with caplog.at_level("WARNING"):
                service._calculate_volatility_surface(analyzer, lambda *a, **k: None, None)

        assert any(
            "Failed to calculate RR25/BF25 skew" in r.message for r in caplog.records
        )

    def test_success_path_populates_both_atm_iv_and_skew(self):
        """Sanity check the happy path still works end to end after the
        reorder (not just the failure-isolation case)."""
        service = _make_service()
        analyzer = _FakeAnalyzer()
        builder = MagicMock()

        service._calculate_volatility_surface(analyzer, lambda *a, **k: None, builder)

        assert builder.set_vol_surface.called
        assert "27MAR27" in analyzer._skew_by_expiry
