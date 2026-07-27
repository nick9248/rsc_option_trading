"""
Unit tests for MarketWideOrchestrator (refactor_design_spec.md section T11 /
M1 — split of ``_calculate_market_wide_metrics`` into 8 named phase methods).

One test per extracted phase method with a stubbed API, per the spec's T11
proof requirement, plus a ``run()`` integration test wiring all 8 together.
"""

from unittest.mock import MagicMock

from coding.core.analytics.market_wide_calculator import MarketWideCalculator
from coding.service.on_chain.market_wide_orchestrator import MarketWideOrchestrator

# task A7 review: previously anchored to a live time.time() call at each
# call site -- the same non-determinism anti-pattern carried finding #4
# removed from tests/unit/test_on_chain_analysis_service_market_wide.py in
# this same task. Every test below that needs synthetic price-history
# timestamps now freezes the clock to this fixed instant (via the
# frozen_clock fixture, tests/conftest.py -- covers both
# coding.core.analytics.vrp_calculator's internal datetime.now() default
# and this module's own time.time() calls) and anchors _price_points to
# the same instant, so nothing here depends on real wall-clock time.
FROZEN_EPOCH = 1_780_000_000.0


class _FakeAnalyzer:
    """Minimal stand-in for OnChainMetricsCalculator's attributes this
    orchestrator reads."""

    def __init__(self, underlying_price=90000.0, dvol=None, atm_ivs=None, recent_trades=None):
        self.underlying_price = underlying_price
        self.currency = "BTC"
        self.market_metrics = {"dvol": dvol}
        self._atm_ivs = atm_ivs or {}
        self._recent_trades = recent_trades or []


def _price_points(n: int, base_ts_ms: int):
    """``n`` daily points ending at a fixed (not live) timestamp."""
    day_ms = 86_400_000
    return {
        "ticks": [base_ts_ms - (n - 1 - i) * day_ms for i in range(n)],
        "close": [90_000.0 + i for i in range(n)],
    }


def _dvol_points(n: int):
    return {"data": [[i, 60.0, 61.0, 59.0, 60.0 + i * 0.1] for i in range(n)]}


def _calc(currency="BTC", spot_price=90000.0, dvol=None) -> MarketWideCalculator:
    return MarketWideCalculator(currency=currency, spot_price=spot_price, dvol=dvol)


class TestCalculateTermStructure:
    """Phase 1."""

    def test_no_atm_ivs_returns_none(self):
        orchestrator = MarketWideOrchestrator(api=MagicMock())
        result = orchestrator._calculate_term_structure(
            _FakeAnalyzer(atm_ivs={}), _calc(), progress_callback=lambda m: None,
        )
        assert result is None

    def test_atm_ivs_produce_sorted_entries_and_shape(self):
        orchestrator = MarketWideOrchestrator(api=MagicMock())
        analyzer = _FakeAnalyzer(atm_ivs={"28MAR30": 70.0, "27JUN30": 60.0})
        result = orchestrator._calculate_term_structure(
            analyzer, _calc(), progress_callback=lambda m: None,
        )

        assert result is not None
        assert len(result.entries) == 2
        assert result.entries[0].dte <= result.entries[1].dte
        assert result.shape in ("CONTANGO", "BACKWARDATION", "FLAT")

    def test_progress_callback_invoked_when_atm_ivs_present(self):
        """
        task A7 review: this phase's progress_callback("Calculating IV term
        structure...") call was dropped during the T11 split (the other 5
        phases' messages survived the move) -- regression guard.
        """
        orchestrator = MarketWideOrchestrator(api=MagicMock())
        analyzer = _FakeAnalyzer(atm_ivs={"28MAR30": 70.0})
        messages = []

        orchestrator._calculate_term_structure(analyzer, _calc(), progress_callback=messages.append)

        assert "Calculating IV term structure..." in messages

    def test_progress_callback_not_invoked_when_no_atm_ivs(self):
        orchestrator = MarketWideOrchestrator(api=MagicMock())
        messages = []

        orchestrator._calculate_term_structure(
            _FakeAnalyzer(atm_ivs={}), _calc(), progress_callback=messages.append,
        )

        assert messages == []


class TestCalculateFuturesBasis:
    """Phase 2."""

    def test_no_futures_instruments_returns_none(self):
        api = MagicMock()
        api.get_instruments.return_value = []
        orchestrator = MarketWideOrchestrator(api=api)

        result = orchestrator._calculate_futures_basis(
            "BTC", _FakeAnalyzer(), _calc(), progress_callback=lambda m: None,
        )
        assert result is None

    def test_dated_future_produces_basis_result(self):
        api = MagicMock()
        api.get_instruments.return_value = [{"instrument_name": "BTC-27MAR26"}]
        api.get_ticker.return_value = {"mark_price": 91000.0, "index_price": 90000.0}
        orchestrator = MarketWideOrchestrator(api=api)

        result = orchestrator._calculate_futures_basis(
            "BTC", _FakeAnalyzer(), _calc(), progress_callback=lambda m: None,
        )
        assert result is not None
        assert len(result.entries) == 1

    def test_api_error_is_caught_and_returns_none(self):
        api = MagicMock()
        api.get_instruments.side_effect = RuntimeError("api down")
        orchestrator = MarketWideOrchestrator(api=api)

        result = orchestrator._calculate_futures_basis(
            "BTC", _FakeAnalyzer(), _calc(), progress_callback=lambda m: None,
        )
        assert result is None


class TestFetchPriceHistory:
    """Shared fetch feeding phases 3/4/5/8."""

    def test_chart_data_parsed_into_price_history(self, frozen_clock):
        frozen_clock(FROZEN_EPOCH)
        api = MagicMock()
        now_ms = int(FROZEN_EPOCH * 1000)
        api.get_tradingview_chart_data.return_value = _price_points(5, now_ms)
        orchestrator = MarketWideOrchestrator(api=api)

        history = orchestrator._fetch_price_history("BTC", progress_callback=lambda m: None)
        assert len(history) == 5
        assert history[0]["close"] == 90_000.0

    def test_api_error_returns_empty_list(self):
        api = MagicMock()
        api.get_tradingview_chart_data.side_effect = RuntimeError("api down")
        orchestrator = MarketWideOrchestrator(api=api)

        history = orchestrator._fetch_price_history("BTC", progress_callback=lambda m: None)
        assert history == []


class TestCalculateRealizedVolatility:
    """Phase 3."""

    def test_empty_price_history_returns_none_and_empty_dict(self):
        orchestrator = MarketWideOrchestrator(api=MagicMock())
        result, rv_values = orchestrator._calculate_realized_volatility(_calc(), [])
        assert result is None
        assert rv_values == {}

    def test_sufficient_price_history_produces_result(self, frozen_clock):
        frozen_clock(FROZEN_EPOCH)
        orchestrator = MarketWideOrchestrator(api=MagicMock())
        now_ms = int(FROZEN_EPOCH * 1000)
        history = _price_points(40, now_ms)
        price_history = [
            {"timestamp": ts / 1000, "close": c}
            for ts, c in zip(history["ticks"], history["close"])
        ]
        result, rv_values = orchestrator._calculate_realized_volatility(_calc(), price_history)
        assert result is not None
        assert set(result.rv_by_window.keys()) == {10, 20, 30}
        assert rv_values == result.rv_by_window


class TestCalculateVrp:
    """Phase 4."""

    def test_zero_rv_30d_returns_none(self):
        orchestrator = MarketWideOrchestrator(api=MagicMock())
        result = orchestrator._calculate_vrp(_calc(dvol=65.0), dvol=65.0, rv_values={})
        assert result is None

    def test_positive_rv_30d_produces_result_even_with_dvol_none(self):
        """A6-carried-finding-adjacent gate: dvol unavailable must not drop
        the whole section when rv_30d is usable."""
        orchestrator = MarketWideOrchestrator(api=MagicMock())
        result = orchestrator._calculate_vrp(_calc(dvol=None), dvol=None, rv_values={30: 0.5})
        assert result is not None
        assert result.dvol is None
        assert result.rv_30d == 0.5


class TestCalculateVolatilityCone:
    """Phase 5."""

    def test_insufficient_price_history_returns_none(self):
        orchestrator = MarketWideOrchestrator(api=MagicMock())
        result = orchestrator._calculate_volatility_cone(_calc(), [{"timestamp": 0, "close": 90000.0}] * 20)
        assert result is None

    def test_sufficient_price_history_produces_result(self, frozen_clock):
        frozen_clock(FROZEN_EPOCH)
        orchestrator = MarketWideOrchestrator(api=MagicMock())
        now_ms = int(FROZEN_EPOCH * 1000)
        history = _price_points(200, now_ms)
        price_history = [
            {"timestamp": ts / 1000, "close": c}
            for ts, c in zip(history["ticks"], history["close"])
        ]
        result = orchestrator._calculate_volatility_cone(_calc(), price_history)
        assert result is not None
        assert set(result.percentile_by_window.keys()) == {10, 20, 30}


class TestCalculatePerpetualFunding:
    """Phase 6."""

    def test_no_funding_data_returns_none(self):
        api = MagicMock()
        api.get_funding_chart_data.return_value = {"data": []}
        api.get_ticker.return_value = {"open_interest": 1000.0, "current_funding": None, "funding_8h": None}
        orchestrator = MarketWideOrchestrator(api=api)

        result = orchestrator._calculate_perpetual_funding("BTC", _calc(), progress_callback=lambda m: None)
        assert result is None

    def test_funding_available_produces_result(self):
        api = MagicMock()
        api.get_funding_chart_data.return_value = {"data": []}
        api.get_ticker.return_value = {"open_interest": 1000.0, "current_funding": 0.0002, "funding_8h": 0.0001}
        orchestrator = MarketWideOrchestrator(api=api)

        result = orchestrator._calculate_perpetual_funding("BTC", _calc(), progress_callback=lambda m: None)
        assert result is not None
        assert result.funding_8h == 0.0001
        assert result.funding_rate == 0.0002


class TestCalculateBlockTrades:
    """Phase 7."""

    def test_no_recent_trades_returns_none(self):
        orchestrator = MarketWideOrchestrator(api=MagicMock())
        result = orchestrator._calculate_block_trades(
            _FakeAnalyzer(recent_trades=[]), _calc(), progress_callback=lambda m: None,
        )
        assert result is None

    def test_block_trade_above_threshold_detected(self):
        orchestrator = MarketWideOrchestrator(api=MagicMock())
        trades = [{"amount": 5.0, "price": 0.05, "index_price": 100_000.0,
                   "instrument_name": "BTC-27MAR26-100000-C", "direction": "buy",
                   "timestamp": 1700000000000, "iv": 60.0}]
        result = orchestrator._calculate_block_trades(
            _FakeAnalyzer(recent_trades=trades), _calc(), progress_callback=lambda m: None,
        )
        assert result is not None
        assert result.total_detected == 1
        assert result.trades[0].notional == 500_000.0


class TestCalculateCrossAssetCorrelation:
    """Phase 8."""

    def test_insufficient_data_produces_none_correlations(self):
        api = MagicMock()
        api.get_tradingview_chart_data.return_value = {"ticks": [], "close": []}
        api.get_volatility_index_data.return_value = {"data": []}
        orchestrator = MarketWideOrchestrator(api=api)

        result = orchestrator._calculate_cross_asset_correlation(
            "BTC", _calc(), price_history=[], progress_callback=lambda m: None,
        )
        assert result is not None
        assert result.price_correlation is None
        assert result.dvol_correlation is None

    def test_api_error_is_caught_and_returns_none(self):
        api = MagicMock()
        api.get_tradingview_chart_data.side_effect = RuntimeError("api down")
        orchestrator = MarketWideOrchestrator(api=api)

        result = orchestrator._calculate_cross_asset_correlation(
            "BTC", _calc(), price_history=[], progress_callback=lambda m: None,
        )
        assert result is None


class TestRunIntegration:
    """run() wires all 8 phases together into one MarketWideResult."""

    def test_run_with_fully_stubbed_api_produces_populated_result(self, frozen_clock):
        frozen_clock(FROZEN_EPOCH)
        now_ms = int(FROZEN_EPOCH * 1000)

        api = MagicMock()

        def get_ticker(name):
            if "PERPETUAL" in name:
                return {"open_interest": 1000.0, "current_funding": 0.0002, "funding_8h": 0.0001}
            return {"mark_price": 91000.0, "index_price": 90000.0}

        def get_instruments(currency, kind, expired):
            return [{"instrument_name": f"{currency}-27MAR26"}]

        def get_tradingview_chart_data(instrument_name, resolution, start_timestamp, end_timestamp):
            return _price_points(200, now_ms)

        def get_funding_chart_data(instrument_name, length):
            return {"data": []}

        def get_volatility_index_data(currency, resolution, start_timestamp, end_timestamp):
            return _dvol_points(30)

        api.get_ticker.side_effect = get_ticker
        api.get_instruments.side_effect = get_instruments
        api.get_tradingview_chart_data.side_effect = get_tradingview_chart_data
        api.get_funding_chart_data.side_effect = get_funding_chart_data
        api.get_volatility_index_data.side_effect = get_volatility_index_data

        orchestrator = MarketWideOrchestrator(api=api)
        analyzer = _FakeAnalyzer(atm_ivs={"27MAR26": 65.0})

        result = orchestrator.run(analyzer, "BTC", progress_callback=lambda m: None)

        assert result.currency == "BTC"
        assert result.term_structure is not None
        assert result.futures_basis is not None
        assert result.realized_volatility is not None
        assert result.volatility_cone is not None
        assert result.perpetual_funding is not None
        assert result.cross_asset_correlation is not None
        assert result.failed_sections == ()
