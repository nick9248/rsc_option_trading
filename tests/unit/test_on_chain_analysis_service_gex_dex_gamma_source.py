"""
Task G2-A (Wave G fresh audit, bug 1): the report/GUI path
(``OnChainAnalysisService._fetch_greeks_and_store_gex_dex``) was reading
Deribit's own ticker ``greeks.gamma`` directly instead of computing gamma
from ``mark_iv`` via ``BlackScholesCalculator`` -- the same BS formula the
daemon path (``ProspectiveCollector._enrich_with_greeks``) already uses,
structurally forced there since ``get_book_summary`` carries no ``greeks``
key at all.

Live evidence (metric-verification agent, task brief): 211 of 533 live
option tickers all reported exactly ``gamma == 1e-05`` -- a clearly
quantized value, not a true internal greek -- and a 321-DTE expiry's
report-path GEX (390,049, ticker gamma) disagreed with the DB's own
daemon-persisted value (737,975, BS gamma) by up to 1.9x. Confirmed
independently against this repo's own recorded live fixture
(tests/fixtures/onchain/BTC_20260725_203222/tickers.json.gz): only 31
distinct gamma values across 870 non-null tickers, vs. 803/707/750 distinct
values for delta/vega/theta over the same sample -- gamma alone is
unusable at ticker precision; delta/vega/theta are not.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from coding.core.analytics.black_scholes_calculator import BlackScholesCalculator
from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService

_FROZEN_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()


class _FakeAnalyzer:
    """
    Minimal stand-in for ``OnChainMetricsCalculator`` -- only the
    attributes/methods ``_fetch_greeks_and_store_gex_dex`` actually reads.
    """

    def __init__(self, currency, index_price, parsed_data):
        self.currency = currency
        self.index_price = index_price
        self.parsed_data = parsed_data
        self.enriched_instruments = {}
        self.forward_price_by_expiration = {}

    def get_expirations(self):
        return list(self.parsed_data.keys())


def _make_service():
    service = OnChainAnalysisService.__new__(OnChainAnalysisService)
    service.api = MagicMock()
    service.repository = None
    return service


def _instrument(name, strike, option_type, oi=100.0):
    return {
        "instrument_name": name,
        "strike": strike,
        "option_type": option_type,
        "open_interest": oi,
    }


class TestBsGammaReplacesTickerGamma:
    def test_gex_uses_bs_gamma_not_quantized_ticker_gamma(self):
        # Deliberately NOT using the frozen_clock fixture here: it only
        # freezes datetime.now() inside production modules (see
        # conftest.py's _FROZEN_CLOCK_MODULES), not this test file's own
        # "expected" recomputation below -- freezing one side only would
        # desync the two tau calculations by however far real wall-clock
        # time has drifted from the frozen epoch, corrupting this exact
        # comparison. Both calls happen within microseconds of each other
        # in real time instead, which is immaterial for a far-dated
        # (2026-12-31) expiry's time-to-expiry in years.
        name = "BTC-31DEC26-50000-C"
        underlying_price = 95000.0
        mark_iv = 60.0  # percent, matches ticker's own convention

        # Deliberately-wrong, obviously-quantized ticker gamma -- 1e-05 was
        # the single most common non-zero value in the live 883-ticker
        # sample this task's audit examined (242/870 non-null tickers).
        ticker_gamma = 1e-05

        service = _make_service()
        service.api.get_ticker.return_value = {
            "greeks": {"gamma": ticker_gamma, "delta": 0.55, "vega": 12.3, "theta": -4.5},
            "mark_iv": mark_iv,
            "underlying_price": underlying_price,
            "best_bid_price": 0.1,
            "best_ask_price": 0.11,
        }

        analyzer = _FakeAnalyzer(
            currency="BTC",
            index_price=underlying_price,
            parsed_data={"31DEC26": [_instrument(name, 50000.0, "C", oi=100.0)]},
        )

        aggregate_result, _ = service._fetch_greeks_and_store_gex_dex(
            analyzer, progress_callback=lambda msg: None,
        )

        enriched = analyzer.enriched_instruments["31DEC26"]
        assert len(enriched) == 1
        actual_gamma = enriched[0]["gamma"]

        # Independently recompute the expected BS gamma the exact same way
        # ProspectiveCollector._enrich_with_greeks does for the daemon path.
        bs = BlackScholesCalculator()
        parsed = bs.parse_instrument_name(name)
        now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        tte = bs.calculate_time_to_expiry(now_utc_naive, parsed["expiry_time"])
        expected_gamma = bs.calculate_greeks(
            spot_price=underlying_price, strike_price=50000.0,
            time_to_expiry=tte, implied_volatility=mark_iv / 100.0,
            option_type=parsed["option_type"],
        )["gamma"]

        assert actual_gamma == pytest.approx(expected_gamma, rel=1e-9)
        assert actual_gamma != pytest.approx(ticker_gamma)

        # The resulting GEX for this single-instrument, single-strike book
        # must be computed from the BS gamma, not the ticker's.
        assert aggregate_result is not None
        expected_gex = expected_gamma * 100.0 * underlying_price ** 2 * 0.01
        assert aggregate_result.total_net_gex == pytest.approx(expected_gex, rel=1e-9)

    def test_delta_vega_theta_still_come_from_ticker_directly(self, frozen_clock):
        """
        delta/vega/theta are NOT quantized the way gamma is (live sample:
        803/707/750 distinct values out of 870 non-null tickers, vs. 31
        distinct gamma values) -- this fix must not touch them.
        """
        frozen_clock(_FROZEN_EPOCH)

        name = "BTC-31DEC26-50000-C"
        service = _make_service()
        service.api.get_ticker.return_value = {
            "greeks": {"gamma": 1e-05, "delta": 0.552317, "vega": 12.34567, "theta": -4.56789},
            "mark_iv": 60.0,
            "underlying_price": 95000.0,
            "best_bid_price": 0.1,
            "best_ask_price": 0.11,
        }
        analyzer = _FakeAnalyzer(
            currency="BTC", index_price=95000.0,
            parsed_data={"31DEC26": [_instrument(name, 50000.0, "C")]},
        )

        service._fetch_greeks_and_store_gex_dex(analyzer, progress_callback=lambda msg: None)

        enriched = analyzer.enriched_instruments["31DEC26"][0]
        assert enriched["delta"] == 0.552317
        assert enriched["vega"] == 12.34567
        assert enriched["theta"] == -4.56789

    def test_missing_mark_iv_yields_none_gamma_not_ticker_fallback(self, frozen_clock):
        """
        If mark_iv is missing, BS gamma cannot be computed -- the result
        must be None (a real "unknown", surfaced by Bug 2's completeness
        tracking), never a silent fallback to the untrustworthy ticker
        gamma.
        """
        frozen_clock(_FROZEN_EPOCH)

        name = "BTC-31DEC26-50000-C"
        service = _make_service()
        service.api.get_ticker.return_value = {
            "greeks": {"gamma": 1e-05, "delta": 0.55, "vega": 12.3, "theta": -4.5},
            "mark_iv": None,
            "underlying_price": 95000.0,
            "best_bid_price": 0.1,
            "best_ask_price": 0.11,
        }
        analyzer = _FakeAnalyzer(
            currency="BTC", index_price=95000.0,
            parsed_data={"31DEC26": [_instrument(name, 50000.0, "C")]},
        )

        service._fetch_greeks_and_store_gex_dex(analyzer, progress_callback=lambda msg: None)

        enriched = analyzer.enriched_instruments["31DEC26"][0]
        assert enriched["gamma"] is None


def _ticker_ok(**overrides):
    ticker = {
        "greeks": {"gamma": 1e-05, "delta": 0.5, "vega": 10.0, "theta": -3.0},
        "mark_iv": 60.0,
        "underlying_price": 95000.0,
        "best_bid_price": 0.1,
        "best_ask_price": 0.11,
    }
    ticker.update(overrides)
    return ticker


class TestDroppedInstrumentsFeedCompletenessSignal:
    """
    Wave G re-review, Important #1: a ticker fetch that raises (rate-
    limiting, API errors) drops the instrument entirely BEFORE it ever
    reaches ``GexDexCalculator._aggregate_by_strike`` -- so the original
    bug 2 fix (which only tracks a null greek on an instrument that
    ARRIVES) never saw it. Reproduces the exact scenario the task's own
    motivating audit cited: "31 of 830 instruments (3.7%) dropped due to
    rate-limiting; one expiry lost 34.49% of its OI-weighted
    representation, and the report still printed EVIDENCE: OI/GEX from
    full book."
    """

    def test_dropped_instrument_counted_in_completeness_signal(self, frozen_clock):
        frozen_clock(_FROZEN_EPOCH)

        def get_ticker(instrument_name):
            if instrument_name == "BTC-31DEC26-52000-C":
                raise Exception("429 Too Many Requests")
            return _ticker_ok()

        service = _make_service()
        service.api.get_ticker.side_effect = get_ticker

        analyzer = _FakeAnalyzer(
            currency="BTC", index_price=95000.0,
            parsed_data={"31DEC26": [
                _instrument("BTC-31DEC26-50000-C", 50000.0, "C", oi=500.0),
                _instrument("BTC-31DEC26-51000-C", 51000.0, "C", oi=300.0),
                _instrument("BTC-31DEC26-52000-C", 52000.0, "C", oi=200.0),  # dropped
            ]},
        )

        aggregate_result, _ = service._fetch_greeks_and_store_gex_dex(
            analyzer, progress_callback=lambda msg: None,
        )

        # Existing, correct behavior unchanged: a dropped instrument never
        # appears in the enriched list.
        assert len(analyzer.enriched_instruments["31DEC26"]) == 2

        assert aggregate_result is not None
        assert aggregate_result.instruments_missing_gamma == 1
        assert aggregate_result.oi_missing_gamma == pytest.approx(200.0)

    def test_dropped_instruments_defeat_full_book_claim(self, frozen_clock):
        """
        The actual motivating scenario, end to end: a partial chain (some
        instruments dropped to rate-limiting) must not let the report
        claim "OI/GEX from full book".
        """
        frozen_clock(_FROZEN_EPOCH)

        def get_ticker(instrument_name):
            if instrument_name == "BTC-31DEC26-52000-C":
                raise Exception("429 Too Many Requests")
            return _ticker_ok()

        service = _make_service()
        service.api.get_ticker.side_effect = get_ticker

        analyzer = _FakeAnalyzer(
            currency="BTC", index_price=95000.0,
            parsed_data={"31DEC26": [
                _instrument("BTC-31DEC26-50000-C", 50000.0, "C", oi=500.0),
                _instrument("BTC-31DEC26-51000-C", 51000.0, "C", oi=300.0),
                # Dropped OI is 200 out of 800 total (25%) -- comfortably
                # analogous to the audit's cited 34.49% and well above the
                # 5% disclosure threshold.
                _instrument("BTC-31DEC26-52000-C", 52000.0, "C", oi=200.0),
            ]},
        )

        aggregate_result, _ = service._fetch_greeks_and_store_gex_dex(
            analyzer, progress_callback=lambda msg: None,
        )

        from coding.core.analytics.reporting.report_formatter import OnChainReportFormatter

        claim = OnChainReportFormatter._book_completeness_claim(aggregate_result)
        assert "full book" not in claim
        assert "INCOMPLETE" in claim
        assert "1 instrument" in claim


class TestFullExpiryFailureStillDisclosable:
    """
    Wave G re-review, Important #2: if EVERY ticker fetch for an
    expiration fails, the old code left ``bundle.gex_dex`` as ``None`` --
    and ``report_formatter.py`` fell through to the legacy unconditional
    "OI/GEX from full book" claim for that case, the ONE scenario where
    the claim is KNOWN false (0% represented), not merely possibly
    incomplete.
    """

    def test_all_instruments_dropped_still_produces_disclosable_gex_dex(self, frozen_clock):
        frozen_clock(_FROZEN_EPOCH)

        service = _make_service()
        service.api.get_ticker.side_effect = Exception("429 Too Many Requests")

        analyzer = _FakeAnalyzer(
            currency="BTC", index_price=95000.0,
            parsed_data={"31DEC26": [
                _instrument("BTC-31DEC26-50000-C", 50000.0, "C", oi=500.0),
                _instrument("BTC-31DEC26-51000-C", 51000.0, "C", oi=300.0),
            ]},
        )

        aggregate_result, _ = service._fetch_greeks_and_store_gex_dex(
            analyzer, progress_callback=lambda msg: None,
        )

        # Nothing to enrich -- existing, correct behavior unchanged.
        assert "31DEC26" not in analyzer.enriched_instruments

        assert aggregate_result is not None
        assert aggregate_result.instruments_missing_gamma == 2
        assert aggregate_result.oi_missing_gamma == pytest.approx(800.0)
        assert aggregate_result.total_net_gex == pytest.approx(0.0)

        from coding.core.analytics.reporting.report_formatter import OnChainReportFormatter

        claim = OnChainReportFormatter._book_completeness_claim(aggregate_result)
        assert "full book" not in claim
        assert "100.0%" in claim

    def test_all_instruments_dropped_still_calls_builder_set_gex_dex(self, frozen_clock):
        """
        Proves the actual production wiring: ``bundle.gex_dex`` (built via
        ``builder.set_gex_dex``) is populated for this expiration, not
        left ``None`` -- the exact fix report_formatter.py's
        ``_book_completeness_claim`` relies on to tell "no data" apart
        from "100% failure".
        """
        frozen_clock(_FROZEN_EPOCH)

        service = _make_service()
        service.api.get_ticker.side_effect = Exception("429 Too Many Requests")

        analyzer = _FakeAnalyzer(
            currency="BTC", index_price=95000.0,
            parsed_data={"31DEC26": [
                _instrument("BTC-31DEC26-50000-C", 50000.0, "C", oi=500.0),
            ]},
        )
        builder = MagicMock()

        service._fetch_greeks_and_store_gex_dex(
            analyzer, progress_callback=lambda msg: None, builder=builder,
        )

        builder.set_gex_dex.assert_called_once()
        called_expiration, called_result = builder.set_gex_dex.call_args[0]
        assert called_expiration == "31DEC26"
        assert called_result is not None
        assert called_result.instruments_missing_gamma == 1
        assert called_result.oi_missing_gamma == pytest.approx(500.0)


class TestComputeBsGammaInputGuards:
    """
    Wave G re-review (Minor): ``_compute_bs_gamma``'s docstring claims it
    "never fabricates a 0.0" -- but the old truthiness guards
    (``not strike`` / ``not mark_iv``) let a NEGATIVE strike/mark_iv sail
    straight through (``not -50000`` is ``False`` in Python), reaching
    ``BlackScholesCalculator.calculate_greeks``'s blanket
    ``except Exception``, which silently returns an all-zero-greeks dict.
    These prove the fix: explicit numeric range checks, never truthiness.
    """

    def test_negative_strike_returns_none_not_zero(self):
        bs = BlackScholesCalculator()
        item = {"strike": -50000.0, "instrument_name": "BTC-31DEC26-50000-C"}
        result = OnChainAnalysisService._compute_bs_gamma(
            bs, item, 60.0, 95000.0, datetime.now(timezone.utc),
        )
        assert result is None

    def test_negative_mark_iv_returns_none_not_zero(self):
        bs = BlackScholesCalculator()
        item = {"strike": 50000.0, "instrument_name": "BTC-31DEC26-50000-C"}
        result = OnChainAnalysisService._compute_bs_gamma(
            bs, item, -60.0, 95000.0, datetime.now(timezone.utc),
        )
        assert result is None

    def test_absurd_mark_iv_returns_none_not_zero(self):
        bs = BlackScholesCalculator()
        item = {"strike": 50000.0, "instrument_name": "BTC-31DEC26-50000-C"}
        result = OnChainAnalysisService._compute_bs_gamma(
            bs, item, 1e10, 95000.0, datetime.now(timezone.utc),
        )
        assert result is None

    def test_sane_mark_iv_still_computes_normally(self):
        """Guardrail didn't break the ordinary case."""
        bs = BlackScholesCalculator()
        item = {"strike": 50000.0, "instrument_name": "BTC-31DEC26-50000-C"}
        result = OnChainAnalysisService._compute_bs_gamma(
            bs, item, 60.0, 95000.0, datetime.now(timezone.utc),
        )
        assert result is not None
        assert result > 0
