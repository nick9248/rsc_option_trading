"""
Unit tests for OnChainAnalysisService._calculate_vwap_iv (bugfix_spec.md Item 3:
VWAP IV compared against the wrong baseline).

Fixture and hand-computed numbers are verbatim from bugfix_spec.md section 3.5.
"""

import pytest

from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService

CHAIN = [
    {"instrument_name": "BTC-1AUG26-60000-C", "mark_iv": 40.0, "strike": 60000.0, "option_type": "C"},
    {"instrument_name": "BTC-1AUG26-64000-C", "mark_iv": 50.0, "strike": 64000.0, "option_type": "C"},
    {"instrument_name": "BTC-1AUG26-90000-C", "mark_iv": 90.0, "strike": 90000.0, "option_type": "C"},  # wing, never trades
]

TRADES = [
    {"instrument_name": "BTC-1AUG26-60000-C", "amount": 10, "iv": 42.0},
    {"instrument_name": "BTC-1AUG26-64000-C", "amount": 30, "iv": 49.0},
]


@pytest.fixture
def service() -> OnChainAnalysisService:
    return OnChainAnalysisService(api_service=None, repository=None)


class TestCalculateVwapIv:
    """T3.1-T3.3: matched-baseline VWAP IV calculation."""

    def test_matched_baseline(self, service):
        """T3.1 - baseline weighted by the SAME traded volumes as VWAP, not
        an unweighted chain average."""
        vwap, baseline, n = service._calculate_vwap_iv(TRADES, CHAIN)

        assert vwap == pytest.approx(47.25)
        assert baseline == pytest.approx(47.50)
        assert n == 2

    def test_no_trades_returns_all_none(self, service):
        vwap, baseline, n = service._calculate_vwap_iv([], CHAIN)
        assert (vwap, baseline, n) == (None, None, 0)

    def test_traded_instrument_without_mark_iv_excluded_from_both_legs(self, service):
        """T3.3 - an instrument that traded but has no chain mark_iv must not
        leak into either leg (previously it inflated only the traded leg)."""
        chain = CHAIN + [{"instrument_name": "BTC-1AUG26-70000-C", "mark_iv": None}]
        trades = TRADES + [{"instrument_name": "BTC-1AUG26-70000-C", "iv": 200.0, "amount": 1000}]

        vwap, baseline, n = service._calculate_vwap_iv(trades, chain)

        assert vwap == pytest.approx(47.25)  # unchanged: the 200-IV trade must NOT leak in
        assert baseline == pytest.approx(47.50)
        assert n == 2

    def test_traded_instrument_with_nonpositive_mark_iv_excluded(self, service):
        chain = CHAIN + [{"instrument_name": "BTC-1AUG26-70000-C", "mark_iv": 0.0}]
        trades = TRADES + [{"instrument_name": "BTC-1AUG26-70000-C", "iv": 55.0, "amount": 5}]

        vwap, baseline, n = service._calculate_vwap_iv(trades, chain)

        assert vwap == pytest.approx(47.25)
        assert baseline == pytest.approx(47.50)
        assert n == 2

    def test_trade_with_zero_amount_skipped(self, service):
        trades = TRADES + [{"instrument_name": "BTC-1AUG26-60000-C", "iv": 999.0, "amount": 0}]
        vwap, baseline, n = service._calculate_vwap_iv(trades, CHAIN)
        assert vwap == pytest.approx(47.25)
        assert n == 2

    def test_trade_with_none_iv_skipped(self, service):
        trades = TRADES + [{"instrument_name": "BTC-1AUG26-64000-C", "iv": None, "amount": 5}]
        vwap, baseline, n = service._calculate_vwap_iv(trades, CHAIN)
        assert vwap == pytest.approx(47.25)
        assert n == 2


# NOTE (task A7, carried finding #1): TestVwapReportGate
# (test_label_flips_vs_old_behavior / T3.2, test_thin_data_suppresses_
# aggression_label / T3.4) used to live here, covering
# VolatilitySurfaceCalculator.generate_report_section() (deleted -- zero
# production callers). T3.4's suppressed-aggression-label coverage is
# equivalent (better) in tests/unit/analytics/reporting/test_vol_surface_
# formatter.py::test_vwap_iv_aggression_suppressed_below_instrument_floor.
# T3.2's "Balanced" regression guard (the specific label the matched-
# baseline fix must produce, vs. the old buggy "Sellers aggressive") was
# NOT otherwise covered at the live-formatter level -- migrated to
# test_vol_surface_formatter.py::test_vwap_iv_balanced_within_threshold
# rather than dropped.
