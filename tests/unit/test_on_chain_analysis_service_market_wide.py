"""
Unit tests for OnChainAnalysisService._calculate_market_wide_metrics's typed
sub-result construction (refactor_design_spec.md T10 / task A6 carried
findings).

CARRIED FINDING #1 (A5 review): funding_data_struct pre-seeds "funding_8h":
0.0 (market_wide_calculator.py:488), so
``funding_data_struct.get("funding_8h") is not None`` was always true and a
genuinely-unavailable funding reading built a zero-value PerpetualFundingResult
instead of None. Fixed by reading the ticker's raw values directly and
gating construction on their presence.

Three more instances of the SAME bug class, found while verifying
render_market_wide_from_result would render byte-identically once wired
live (T10): VRP's dvol-availability gate (the service re-gated on dvol
being not-None even though VarianceRiskPremiumResult.dvol is Optional and
the calculator's own text branch already handles it), the volatility cone's
whole-result gate (constructed unconditionally whenever price_history was
truthy, even when too short for the calculator's own "Insufficient" branch,
producing a fake all-zero-percentile result), and cross-asset correlation's
0.0-sentinel pre-seed (indistinguishable from a genuine zero correlation).
"""

from unittest.mock import MagicMock

import pytest

from coding.service.on_chain.analysis_builder import OnChainAnalysisBuilder
from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService

# CARRIED FINDING #4 (A6 review, task A7): an arbitrary but FIXED instant --
# previously _price_points anchored its synthetic timestamps to a live
# time.time() call, making this test suite's timing non-reproducible run to
# run (harmless today only because nothing here asserts on exact rolling-
# window boundaries, but a latent flakiness source). Every test now freezes
# the clock to this instant via the ``frozen_clock`` fixture (tests/conftest.py)
# before calling _run, so "now" is deterministic end to end.
FROZEN_EPOCH = 1_780_000_000.0


class _FakeAnalyzer:
    """
    Minimal stand-in for OnChainMetricsCalculator's attributes this method
    reads -- avoids needing a full parsed book summary.

    CARRIED FINDING #4 (A6 review, task A7): this fake used to also define
    market_wide_sections/market_wide_structured attributes and
    set_market_wide_section/set_market_wide_structured methods, mirroring
    setters T10 deleted from the real OnChainMetricsCalculator. The fake was
    richer than the real object it stands in for and would not have caught
    a regression if the service tried to call a deleted setter on the real
    class. Trimmed to exactly the attributes _calculate_market_wide_metrics
    (via MarketWideOrchestrator) actually reads.
    """

    def __init__(self, underlying_price=90000.0, dvol=None, atm_ivs=None, recent_trades=None):
        self.underlying_price = underlying_price
        self.currency = "BTC"
        self.market_metrics = {"dvol": dvol}
        self._atm_ivs = atm_ivs or {}
        self._recent_trades = recent_trades or []


def _price_points(n: int, epoch: float = FROZEN_EPOCH):
    """
    ``n`` daily points ending at ``epoch`` -- VRPCalculator.
    calculate_realized_volatility filters price_history to a window
    relative to ``datetime.now()`` (no reference_time is threaded through
    from this service), so synthetic timestamps must line up with whatever
    instant the test froze the clock to, or every point gets filtered out
    of the RV window.
    """
    now_ms = int(epoch * 1000)
    day_ms = 86_400_000
    return {
        "ticks": [now_ms - (n - 1 - i) * day_ms for i in range(n)],
        "close": [90_000.0 + i for i in range(n)],
    }


def _dvol_points(n: int):
    return {"data": [[i, 60.0, 61.0, 59.0, 60.0 + i * 0.1] for i in range(n)]}


def _make_api(
    funding_8h=None,
    current_funding=None,
    own_price_len=200,
    other_price_len=200,
    own_dvol_len=30,
    other_dvol_len=30,
):
    api = MagicMock()

    def get_ticker(name):
        if "PERPETUAL" in name:
            return {"open_interest": 1000.0, "current_funding": current_funding, "funding_8h": funding_8h}
        return {"mark_price": 0, "index_price": 0}

    def get_instruments(currency, kind, expired):
        return []  # no dated futures -> futures basis phase skipped, irrelevant here

    def get_tradingview_chart_data(instrument_name, resolution, start_timestamp, end_timestamp):
        if instrument_name == "BTC-PERPETUAL":
            return _price_points(own_price_len)
        return _price_points(other_price_len)

    def get_funding_chart_data(instrument_name, length):
        return {"data": []}

    def get_volatility_index_data(currency, resolution, start_timestamp, end_timestamp):
        return _dvol_points(own_dvol_len if currency == "BTC" else other_dvol_len)

    api.get_ticker.side_effect = get_ticker
    api.get_instruments.side_effect = get_instruments
    api.get_tradingview_chart_data.side_effect = get_tradingview_chart_data
    api.get_funding_chart_data.side_effect = get_funding_chart_data
    api.get_volatility_index_data.side_effect = get_volatility_index_data
    return api


def _run(frozen_clock, api, analyzer) -> "MarketWideResult":  # noqa: F821 (typing only)
    frozen_clock(FROZEN_EPOCH)
    service = OnChainAnalysisService(api_service=api, repository=None)
    builder = OnChainAnalysisBuilder("BTC", analyzer.underlying_price, {})
    service._calculate_market_wide_metrics(
        analyzer, "BTC", progress_callback=lambda m: None,
        builder=builder, aggregate_gex_dex_result=None,
    )
    return builder.build().market_wide


class TestFundingGate:
    """Carried finding #1."""

    def test_funding_unavailable_produces_none_not_zero_result(self, frozen_clock):
        api = _make_api(funding_8h=None, current_funding=None)
        mw = _run(frozen_clock, api, _FakeAnalyzer())

        assert mw.perpetual_funding is None

    def test_funding_available_produces_populated_result(self, frozen_clock):
        api = _make_api(funding_8h=0.0001, current_funding=0.0002)
        mw = _run(frozen_clock, api, _FakeAnalyzer())

        assert mw.perpetual_funding is not None
        assert mw.perpetual_funding.funding_8h == 0.0001
        assert mw.perpetual_funding.funding_rate == 0.0002

    def test_only_current_funding_available_funding_8h_stays_none(self, frozen_clock):
        """Half-available case: current_funding present, funding_8h absent --
        the typed result must carry funding_8h=None (not the calculator's
        internal 0.0 pre-seed), so the formatter renders "not available" for
        the 8h line while still showing the instantaneous rate."""
        api = _make_api(funding_8h=None, current_funding=0.0003)
        mw = _run(frozen_clock, api, _FakeAnalyzer())

        assert mw.perpetual_funding is not None
        assert mw.perpetual_funding.funding_8h is None
        assert mw.perpetual_funding.funding_rate == 0.0003


class TestVrpGate:
    """Additional finding: VRP dvol-availability gate mismatch."""

    def test_dvol_unavailable_with_sufficient_rv_still_produces_result_with_none_dvol(self, frozen_clock):
        api = _make_api(own_price_len=200)
        mw = _run(frozen_clock, api, _FakeAnalyzer(dvol=None))

        assert mw.variance_risk_premium is not None
        assert mw.variance_risk_premium.dvol is None

    def test_dvol_available_produces_result_with_dvol_set(self, frozen_clock):
        api = _make_api(own_price_len=200)
        mw = _run(frozen_clock, api, _FakeAnalyzer(dvol=65.0))

        assert mw.variance_risk_premium is not None
        assert mw.variance_risk_premium.dvol == 65.0


class TestVolatilityConeGate:
    """Additional finding: whole-result gate mismatch (fake all-zero result
    on insufficient price history)."""

    def test_insufficient_price_history_produces_none_not_fake_zero_result(self, frozen_clock):
        api = _make_api(own_price_len=20)  # >=11 (RV computes) but <35 (cone insufficient)
        mw = _run(frozen_clock, api, _FakeAnalyzer())

        assert mw.volatility_cone is None

    def test_sufficient_price_history_produces_populated_result(self, frozen_clock):
        api = _make_api(own_price_len=200)
        mw = _run(frozen_clock, api, _FakeAnalyzer())

        assert mw.volatility_cone is not None
        assert set(mw.volatility_cone.percentile_by_window.keys()) == {10, 20, 30}


class TestCrossAssetCorrelationGate:
    """Additional finding: 0.0-sentinel pre-seed indistinguishable from a
    genuine zero correlation."""

    def test_insufficient_data_produces_none_correlations_not_zero(self, frozen_clock):
        api = _make_api(own_price_len=200, other_price_len=5, own_dvol_len=5, other_dvol_len=5)
        mw = _run(frozen_clock, api, _FakeAnalyzer())

        assert mw.cross_asset_correlation is not None
        assert mw.cross_asset_correlation.price_correlation is None
        assert mw.cross_asset_correlation.dvol_correlation is None

    def test_sufficient_data_produces_real_correlation_values(self, frozen_clock):
        api = _make_api(own_price_len=200, other_price_len=200, own_dvol_len=30, other_dvol_len=30)
        mw = _run(frozen_clock, api, _FakeAnalyzer())

        assert mw.cross_asset_correlation is not None
        assert mw.cross_asset_correlation.price_correlation is not None
        assert mw.cross_asset_correlation.dvol_correlation is not None
