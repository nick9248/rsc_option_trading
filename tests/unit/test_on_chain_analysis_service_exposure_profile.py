"""
Unit tests for OnChainAnalysisService._calculate_exposure_profile
(institutional_metrics_spec.md section 4 / task C5: report-path service
wiring).

Mirrors test_on_chain_analysis_service_dealer_inventory.py's pattern: this
is a purely additive computation (like GexDexCalculator._calculate_gamma_
profile's established guard, and _calculate_inferred_dealer_positioning
right above it in the same per-expiration loop) -- any failure must degrade
to None, never crash the GEX/DEX pipeline it runs alongside.
"""

from unittest.mock import MagicMock, patch

from coding.core.analytics.exposure_profile_calculator import ExposureProfileCalculator
from coding.core.analytics.results.exposure_profile_results import ExposureProfileResult
from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService


def _make_service():
    service = OnChainAnalysisService.__new__(OnChainAnalysisService)
    service.api = MagicMock()
    service.repository = None
    return service


def _instruments():
    # Far-future expiry (matches test_on_chain_analysis_service_skew_
    # isolation.py's convention) so tau > 0 regardless of when this suite runs.
    return [
        {
            "instrument_name": "BTC-27MAR27-64000-C", "strike": 64000.0,
            "option_type": "C", "mark_iv": 40.0, "open_interest": 1000.0,
        },
        {
            "instrument_name": "BTC-27MAR27-64000-P", "strike": 64000.0,
            "option_type": "P", "mark_iv": 40.0, "open_interest": 400.0,
        },
    ]


class TestHappyPath:
    def test_returns_populated_exposure_profile_result(self):
        service = _make_service()

        result = service._calculate_exposure_profile("BTC", "02APR26", _instruments(), 64000.0)

        assert isinstance(result, ExposureProfileResult)
        assert result.currency == "BTC"
        assert result.spot_price == 64000.0
        assert len(result.strike_rows) == 1
        row = result.strike_rows[0]
        assert row.strike == 64000.0
        assert row.call_oi == 1000.0
        assert row.put_oi == 400.0
        # Holder vs assumed-dealer must diverge (D7) -- not the same number.
        assert row.vex_holder != row.vex_assumed_dealer
        assert result.peak_vanna_strike == 64000.0

    def test_empty_instruments_returns_well_formed_empty_result(self):
        service = _make_service()
        result = service._calculate_exposure_profile("BTC", "02APR26", [], 64000.0)
        assert result is not None
        assert result.strike_rows == ()
        assert result.total_vex_holder == 0.0
        assert result.peak_vanna_strike is None


class TestAdditiveOnlyGuard:
    """A failure computing VEX/CEX must never crash the caller -- it
    degrades to None, matching _calculate_inferred_dealer_positioning's
    and GexDexCalculator._calculate_gamma_profile's established guard."""

    def test_unexpected_failure_returns_none_not_raises(self, caplog):
        service = _make_service()

        with patch.object(
            ExposureProfileCalculator, "calculate", side_effect=RuntimeError("boom"),
        ):
            with caplog.at_level("ERROR"):
                result = service._calculate_exposure_profile(
                    "BTC", "02APR26", _instruments(), 64000.0,
                )

        assert result is None
        assert any(
            "vanna/charm exposure profile" in r.message.lower() for r in caplog.records
        )

    def test_malformed_instrument_data_does_not_raise(self):
        """A shape mismatch (e.g. non-numeric strike) must degrade to None,
        not propagate and abort the GEX/DEX loop it runs alongside."""
        service = _make_service()
        bad_instruments = [
            {"instrument_name": "BTC-02APR26-X-C", "strike": "not-a-number",
             "option_type": "C", "mark_iv": 40.0, "open_interest": 100.0},
        ]
        result = service._calculate_exposure_profile("BTC", "02APR26", bad_instruments, 64000.0)
        # Either degrades gracefully (skipped instrument, empty profile) or
        # None on unexpected failure -- either way, no exception escapes.
        assert result is None or result.strike_rows == ()
