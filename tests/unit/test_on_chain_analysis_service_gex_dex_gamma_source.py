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
