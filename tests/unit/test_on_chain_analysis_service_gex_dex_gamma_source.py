"""
Greeks of the report/GUI path (``OnChainAnalysisService._fetch_greeks_and_store_gex_dex``). Every Greek is computed from the
book summary's ``mark_iv`` with ``BlackScholesCalculator`` (the same way the VPS collector does it): delta, vega and theta on the
expiry's forward, gamma on the index. No ticker is fetched per instrument any more: about 1,000 calls per run were answered with
HTTP 429 for 83% of them in the run of 2026-11-21, which left most of the book without Greeks.

History: Deribit's ticker gamma is quantized (only 31 distinct values over 870 recorded tickers), so gamma was already computed
locally; delta, vega and theta are now too. On 24 live tickers (3 expiries) the local delta differed by at most 0.13%, vega by 0.07%.
"""

import math
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

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


def _instrument(name, strike, option_type, oi=100.0, mark_iv=60.0, forward=95000.0, bid=0.1, ask=0.11):
    """An instrument as the book summary delivers it: it carries mark_iv, the expiry's forward and the quotes."""
    return {
        "instrument_name": name,
        "strike": strike,
        "option_type": option_type,
        "open_interest": oi,
        "mark_iv": mark_iv,
        "underlying_price": forward,
        "bid_price": bid,
        "ask_price": ask,
    }


def _normal_pdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _normal_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


class TestGreeksAreComputedLocally:
    def test_gex_uses_bs_gamma_from_mark_iv(self, frozen_clock):
        frozen_clock(_FROZEN_EPOCH)
        name = "BTC-31DEC26-50000-C"
        underlying_price = 95000.0
        mark_iv = 60.0  # percent

        service = _make_service()
        analyzer = _FakeAnalyzer(
            currency="BTC",
            index_price=underlying_price,
            parsed_data={"31DEC26": [_instrument(name, 50000.0, "C", oi=100.0, mark_iv=mark_iv, forward=underlying_price)]},
        )

        aggregate_result, _ = service._fetch_greeks_and_store_gex_dex(analyzer, progress_callback=lambda msg: None)

        enriched = analyzer.enriched_instruments["31DEC26"]
        assert len(enriched) == 1
        actual_gamma = enriched[0]["gamma"]

        # Independently recompute the expected BS gamma the exact same way ProspectiveCollector._enrich_with_greeks does.
        bs = BlackScholesCalculator()
        parsed = bs.parse_instrument_name(name)
        tte = bs.calculate_time_to_expiry(datetime(2026, 1, 1), parsed["expiry_time"])      # the frozen clock
        expected_gamma = bs.calculate_greeks(
            spot_price=underlying_price, strike_price=50000.0,
            time_to_expiry=tte, implied_volatility=mark_iv / 100.0,
            option_type=parsed["option_type"],
        )["gamma"]

        assert actual_gamma == pytest.approx(expected_gamma, rel=1e-9)
        assert aggregate_result is not None
        expected_gex = expected_gamma * 100.0 * underlying_price ** 2 * 0.01
        assert aggregate_result.total_net_gex == pytest.approx(expected_gex, rel=1e-9)

    def test_no_request_is_made_per_instrument(self):
        """The reason for the change: one ticker call per instrument was answered with HTTP 429 for most of the book."""
        service = _make_service()
        analyzer = _FakeAnalyzer(
            currency="BTC", index_price=95000.0,
            parsed_data={"31DEC26": [_instrument(f"BTC-31DEC26-{strike}-C", float(strike), "C") for strike in range(50000, 60000, 1000)]},
        )

        service._fetch_greeks_and_store_gex_dex(analyzer, progress_callback=lambda msg: None)

        service.api.get_ticker.assert_not_called()
        assert len(analyzer.enriched_instruments["31DEC26"]) == 10

    def test_delta_vega_theta_match_an_independent_black_scholes_calculation(self, frozen_clock):
        """
        Hand calculation (not the production calculator): call, S = 95,000 (the expiry's forward), K = 50,000,
        sigma = 60%, T = time from 2026-01-01 00:00 UTC to the 08:00 UTC settlement of 2026-12-31, rate 0.
        delta = N(d1); vega per volatility point = S * pdf(d1) * sqrt(T) / 100; theta per day = -S * pdf(d1) * sigma / (2 sqrt(T)) / 365.
        """
        frozen_clock(_FROZEN_EPOCH)
        service = _make_service()
        analyzer = _FakeAnalyzer(
            currency="BTC", index_price=95000.0,
            parsed_data={"31DEC26": [_instrument("BTC-31DEC26-50000-C", 50000.0, "C", mark_iv=60.0, forward=95000.0)]},
        )

        service._fetch_greeks_and_store_gex_dex(analyzer, progress_callback=lambda msg: None)

        enriched = analyzer.enriched_instruments["31DEC26"][0]
        time_to_expiry = (datetime(2026, 12, 31, 8) - datetime(2026, 1, 1)).total_seconds() / (365 * 86400)
        sigma, spot, strike = 0.60, 95000.0, 50000.0
        d1 = (math.log(spot / strike) + 0.5 * sigma ** 2 * time_to_expiry) / (sigma * math.sqrt(time_to_expiry))
        assert enriched["delta"] == pytest.approx(_normal_cdf(d1), abs=1e-6)
        assert enriched["vega"] == pytest.approx(spot * _normal_pdf(d1) * math.sqrt(time_to_expiry) / 100.0, rel=1e-6)
        assert enriched["theta"] == pytest.approx(-spot * _normal_pdf(d1) * sigma / (2.0 * math.sqrt(time_to_expiry)) / 365.0, rel=1e-6)

    def test_quotes_and_forward_come_from_the_book_summary(self, frozen_clock):
        frozen_clock(_FROZEN_EPOCH)
        service = _make_service()
        analyzer = _FakeAnalyzer(
            currency="BTC", index_price=94000.0,
            parsed_data={"31DEC26": [_instrument("BTC-31DEC26-50000-C", 50000.0, "C", forward=95500.0, bid=0.12, ask=0.13)]},
        )

        service._fetch_greeks_and_store_gex_dex(analyzer, progress_callback=lambda msg: None)

        enriched = analyzer.enriched_instruments["31DEC26"][0]
        assert (enriched["bid_price"], enriched["ask_price"], enriched["underlying_price"]) == (0.12, 0.13, 95500.0)

    def test_missing_mark_iv_keeps_the_instrument_with_none_greeks_not_zero(self, frozen_clock):
        """
        Without mark_iv no Greek can be computed: the instrument stays, with None (a real "unknown", counted as missing
        by GexDexCalculator's completeness tracking), never a 0 and never a made-up value.
        """
        frozen_clock(_FROZEN_EPOCH)
        service = _make_service()
        analyzer = _FakeAnalyzer(
            currency="BTC", index_price=95000.0,
            parsed_data={"31DEC26": [_instrument("BTC-31DEC26-50000-C", 50000.0, "C", mark_iv=None),
                                     _instrument("BTC-31DEC26-51000-C", 51000.0, "C", oi=300.0)]},
        )

        aggregate_result, _ = service._fetch_greeks_and_store_gex_dex(analyzer, progress_callback=lambda msg: None)

        enriched = {item["instrument_name"]: item for item in analyzer.enriched_instruments["31DEC26"]}
        broken = enriched["BTC-31DEC26-50000-C"]
        assert (broken["gamma"], broken["delta"], broken["vega"], broken["theta"]) == (None, None, None, None)
        assert enriched["BTC-31DEC26-51000-C"]["gamma"] is not None
        assert aggregate_result.instruments_missing_gamma == 1
        assert aggregate_result.oi_missing_gamma == pytest.approx(100.0)


    def test_an_instrument_without_its_own_forward_uses_the_expiry_forward_then_the_index(self, frozen_clock):
        frozen_clock(_FROZEN_EPOCH)
        service = _make_service()
        analyzer = _FakeAnalyzer(
            currency="BTC", index_price=95000.0,
            parsed_data={"31DEC26": [_instrument("BTC-31DEC26-50000-C", 50000.0, "C", forward=None),
                                     _instrument("BTC-31DEC26-51000-C", 51000.0, "C", forward=None)]},
        )
        analyzer.forward_price_by_expiration = {"31DEC26": 95800.0}

        service._fetch_greeks_and_store_gex_dex(analyzer, progress_callback=lambda msg: None)
        with_expiry_forward = analyzer.enriched_instruments["31DEC26"]
        assert all(item["delta"] is not None and item["underlying_price"] == 95800.0 for item in with_expiry_forward)

        analyzer.forward_price_by_expiration = {}
        analyzer.enriched_instruments = {}
        service._fetch_greeks_and_store_gex_dex(analyzer, progress_callback=lambda msg: None)
        assert all(item["delta"] is not None and item["underlying_price"] == 95000.0 for item in analyzer.enriched_instruments["31DEC26"])

    def test_an_all_zero_answer_of_the_calculator_is_not_computed_not_four_zeros(self, frozen_clock):
        frozen_clock(_FROZEN_EPOCH)
        service = _make_service()
        analyzer = _FakeAnalyzer(currency="BTC", index_price=95000.0,
                                 parsed_data={"31DEC26": [_instrument("BTC-31DEC26-50000-C", 50000.0, "C", oi=100.0)]})
        zeros = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}   # what its blanket except returns

        with patch.object(BlackScholesCalculator, "calculate_greeks", return_value=zeros):
            aggregate_result, _ = service._fetch_greeks_and_store_gex_dex(analyzer, progress_callback=lambda msg: None)

        enriched = analyzer.enriched_instruments["31DEC26"][0]
        assert (enriched["delta"], enriched["vega"], enriched["theta"], enriched["gamma"]) == (None, None, None, None)
        assert aggregate_result.instruments_missing_gamma == 1

    def test_a_settled_instrument_is_not_part_of_the_book_and_not_counted_missing(self, frozen_clock):
        frozen_clock(_FROZEN_EPOCH)                              # 2026-01-01 00:00 UTC: 31DEC25 08:00 UTC has passed
        service = _make_service()
        analyzer = _FakeAnalyzer(
            currency="BTC", index_price=95000.0,
            parsed_data={"31DEC25": [_instrument("BTC-31DEC25-50000-C", 50000.0, "C", oi=500.0)],
                         "31DEC26": [_instrument("BTC-31DEC26-50000-C", 50000.0, "C", oi=100.0)]},
        )

        aggregate_result, _ = service._fetch_greeks_and_store_gex_dex(analyzer, progress_callback=lambda msg: None)

        assert "31DEC25" not in analyzer.enriched_instruments
        assert len(analyzer.enriched_instruments["31DEC26"]) == 1
        assert aggregate_result.instruments_missing_gamma == 0


def _fail_for(*names):
    """A replacement for _compute_bs_gamma that raises for the named instruments (an unexpected error while processing one)."""
    real = OnChainAnalysisService._compute_bs_gamma

    def compute(bs_calculator, item, mark_iv, underlying_price, now_utc):
        if item["instrument_name"] in names:
            raise RuntimeError("unexpected failure")
        return real(bs_calculator, item, mark_iv, underlying_price, now_utc)

    return patch.object(OnChainAnalysisService, "_compute_bs_gamma", staticmethod(compute))


class TestDroppedInstrumentsFeedCompletenessSignal:
    """
    An instrument that raises while it is processed is dropped before it reaches ``GexDexCalculator._aggregate_by_strike``,
    so the drop must feed the same completeness fields a null Greek does (Wave G re-review, Important #1): the report may
    never claim "OI/GEX from full book" for a partial chain.
    """

    def test_dropped_instrument_counted_in_completeness_signal(self, frozen_clock):
        frozen_clock(_FROZEN_EPOCH)
        service = _make_service()
        analyzer = _FakeAnalyzer(
            currency="BTC", index_price=95000.0,
            parsed_data={"31DEC26": [
                _instrument("BTC-31DEC26-50000-C", 50000.0, "C", oi=500.0),
                _instrument("BTC-31DEC26-51000-C", 51000.0, "C", oi=300.0),
                _instrument("BTC-31DEC26-52000-C", 52000.0, "C", oi=200.0),  # dropped
            ]},
        )

        with _fail_for("BTC-31DEC26-52000-C"):
            aggregate_result, _ = service._fetch_greeks_and_store_gex_dex(analyzer, progress_callback=lambda msg: None)

        assert len(analyzer.enriched_instruments["31DEC26"]) == 2
        assert aggregate_result is not None
        assert aggregate_result.instruments_missing_gamma == 1
        assert aggregate_result.oi_missing_gamma == pytest.approx(200.0)

    def test_dropped_instruments_defeat_full_book_claim(self, frozen_clock):
        frozen_clock(_FROZEN_EPOCH)
        service = _make_service()
        analyzer = _FakeAnalyzer(
            currency="BTC", index_price=95000.0,
            parsed_data={"31DEC26": [
                _instrument("BTC-31DEC26-50000-C", 50000.0, "C", oi=500.0),
                _instrument("BTC-31DEC26-51000-C", 51000.0, "C", oi=300.0),
                # Dropped OI is 200 out of 800 total (25%): well above the 5% disclosure threshold.
                _instrument("BTC-31DEC26-52000-C", 52000.0, "C", oi=200.0),
            ]},
        )

        with _fail_for("BTC-31DEC26-52000-C"):
            aggregate_result, _ = service._fetch_greeks_and_store_gex_dex(analyzer, progress_callback=lambda msg: None)

        from coding.core.analytics.reporting.report_formatter import OnChainReportFormatter

        claim = OnChainReportFormatter._book_completeness_claim(aggregate_result)
        assert "full book" not in claim
        assert "INCOMPLETE" in claim
        assert "1 instrument" in claim


class TestFullExpiryFailureStillDisclosable:
    """
    If EVERY instrument of an expiration fails, ``bundle.gex_dex`` must still be a disclosable result (100% missing), not
    ``None``: the report would otherwise fall through to "OI/GEX from full book", the one case where that claim is KNOWN false.
    """

    def test_all_instruments_dropped_still_produces_disclosable_gex_dex(self, frozen_clock):
        frozen_clock(_FROZEN_EPOCH)
        service = _make_service()
        analyzer = _FakeAnalyzer(
            currency="BTC", index_price=95000.0,
            parsed_data={"31DEC26": [
                _instrument("BTC-31DEC26-50000-C", 50000.0, "C", oi=500.0),
                _instrument("BTC-31DEC26-51000-C", 51000.0, "C", oi=300.0),
            ]},
        )

        with _fail_for("BTC-31DEC26-50000-C", "BTC-31DEC26-51000-C"):
            aggregate_result, _ = service._fetch_greeks_and_store_gex_dex(analyzer, progress_callback=lambda msg: None)

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
        """Proves the production wiring: ``bundle.gex_dex`` is populated for this expiration, not left ``None``."""
        frozen_clock(_FROZEN_EPOCH)
        service = _make_service()
        analyzer = _FakeAnalyzer(
            currency="BTC", index_price=95000.0,
            parsed_data={"31DEC26": [_instrument("BTC-31DEC26-50000-C", 50000.0, "C", oi=500.0)]},
        )
        builder = MagicMock()

        with _fail_for("BTC-31DEC26-50000-C"):
            service._fetch_greeks_and_store_gex_dex(analyzer, progress_callback=lambda msg: None, builder=builder)

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
