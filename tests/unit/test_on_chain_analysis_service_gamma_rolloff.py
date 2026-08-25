"""
Unit tests for OnChainAnalysisService._build_gamma_rolloff
(institutional_metrics_spec.md section 5 / Task C6: report-path service
wiring).

Mirrors test_on_chain_analysis_service_exposure_profile.py's pattern: this
is a purely additive computation over data the GEX/DEX loop already
produced (per-expiry GexDexResult.total_net_gex) -- any failure must
degrade to None, never crash the GEX/DEX pipeline it runs alongside
(isolation constraint from the task brief: must not share a try/except
with the pre-existing aggregate_across_expirations call).
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from coding.core.analytics.gex_dex_calculator import GexDexCalculator
from coding.core.analytics.results.market_wide_results import GammaRolloffResult
from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService

# 0.6 days before 25JUL26's 08:00 UTC settlement -- reproduces the spec's
# own T5.1 worked DTEs (0.6d/6.6d/34.6d) for 25JUL26/31JUL26/28AUG26, same
# anchor test_gex_dex_calculator.py's TestCalculateRolloffProfile uses.
NOW_UTC = datetime(2026, 7, 25, 8, 0, 0, tzinfo=timezone.utc) - timedelta(days=0.6)


def _make_service():
    service = OnChainAnalysisService.__new__(OnChainAnalysisService)
    service.api = MagicMock()
    service.repository = None
    return service


def _gex_dex_stub(total_net_gex: float):
    """Minimal stand-in for GexDexResult -- _build_gamma_rolloff reads only
    .total_net_gex off each per-expiry result."""
    return MagicMock(total_net_gex=total_net_gex)


class TestHappyPath:
    def test_returns_populated_gamma_rolloff_result(self):
        service = _make_service()
        gex_dex_by_expiry = {
            "25JUL26": _gex_dex_stub(30_000_000.0),
            "31JUL26": _gex_dex_stub(50_000_000.0),
            "28AUG26": _gex_dex_stub(20_000_000.0),
        }

        result = service._build_gamma_rolloff(gex_dex_by_expiry, NOW_UTC)

        assert isinstance(result, GammaRolloffResult)
        assert len(result.rows) == 3
        assert result.gamma_cliff_7d is True
        assert result.cum_share_7d == pytest.approx(80.0)
        assert result.gross_total == pytest.approx(100_000_000.0)
        # Chronological order (ascending DTE).
        assert [r.expiration for r in result.rows] == ["25JUL26", "31JUL26", "28AUG26"]

    def test_empty_dict_returns_none(self):
        service = _make_service()
        assert service._build_gamma_rolloff({}, NOW_UTC) is None


class TestAdditiveOnlyGuard:
    """A failure computing the roll-off profile must never crash the
    caller -- degrades to None, matching _calculate_exposure_profile's and
    _calculate_inferred_dealer_positioning's established guard."""

    def test_unexpected_failure_returns_none_not_raises(self, caplog):
        service = _make_service()
        gex_dex_by_expiry = {"25JUL26": _gex_dex_stub(1.0)}

        with patch.object(
            GexDexCalculator, "calculate_rolloff_profile", side_effect=RuntimeError("boom"),
        ):
            with caplog.at_level("ERROR"):
                result = service._build_gamma_rolloff(gex_dex_by_expiry, NOW_UTC)

        assert result is None
        assert any("gamma roll-off" in r.message.lower() for r in caplog.records)
