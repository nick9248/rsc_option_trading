"""
Unit tests for ButterflyScanService. FakeApiService/FakeRepository stand
in for DeribitApiService/DatabaseRepository -- no live API/DB, same pattern
as tests/unit/test_iron_condor_scan_service.py.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List

from coding.service.scanner.butterfly_scan_service import ButterflyScanService


def _contract(strike, option_type, bid, ask, oi, dte=39.0, expiry="1SEP26"):
    return {
        "instrument_name": f"BTC-{expiry}-{int(strike)}-{option_type}", "expiry": expiry,
        "dte": dte, "strike": strike, "option_type": option_type,
        "bid_price": bid, "ask_price": ask, "open_interest": oi,
        "bid_usd": bid * 65000.0, "ask_usd": ask * 65000.0,
    }


def _basic_snapshot(strikes_and_prices: List[tuple]) -> Dict[str, Any]:
    # strikes_and_prices entries are (strike, call_price_usd, put_price_usd) --
    # plausible USD premiums, converted to BTC-native bid/ask (Deribit's
    # actual quoting convention: bid_price/ask_price are in the base
    # currency) via / index_price, matching get_option_chain_snapshot's
    # real bid_usd = bid_price * index_price relationship. See
    # test_iron_condor_scan_service.py's own _basic_snapshot for the same
    # fix -- feeding USD-scale premiums directly into bid_price/ask_price
    # would double-scale bid_usd/ask_usd (bid_price * index_price again).
    index_price = 65000.0
    contracts = []
    for strike, call_price, put_price in strikes_and_prices:
        call_btc = call_price / index_price
        put_btc = put_price / index_price
        contracts.append(_contract(strike, "C", call_btc * 0.98, call_btc * 1.02, 100))
        contracts.append(_contract(strike, "P", put_btc * 0.98, put_btc * 1.02, 100))
    return {
        "as_of": datetime.now(timezone.utc), "index_price": index_price,
        "contracts": contracts, "futures_by_expiry": {"1SEP26": index_price},
    }


class FakeApiService:
    def __init__(self, snapshot):
        self._snapshot = snapshot

    def get_option_chain_snapshot(self, currency):
        return self._snapshot


class FakeRepository:
    """Stand-in for DatabaseRepository -- no OHLCV history, so
    realized_vol.compute_realized_vol always returns None and
    _sigma_sqrt_t degrades to None (see build_butterfly_candidates'
    handling of sigma_sqrt_t=None). Keeps these tests DB-free --
    ButterflyScanService._sigma_sqrt_t would otherwise default-construct
    a real DatabaseRepository() and attempt a live Postgres connection."""

    def get_ohlcv_by_date_range(self, currency, start, end):
        return []


class TestButterflyScan:
    def test_produces_candidates_around_atm(self):
        # (strike, call_price_usd, put_price_usd) -- call side must decay
        # monotonically (and roughly convexly) as strike rises, since
        # build_butterfly_candidates only uses the call side
        # (liquid_call) to price k1/k2/k3: cost = call_ask[k1] +
        # call_ask[k3] - 2*call_bid[k2]. Put prices here are unused by the
        # candidate math -- only present so each strike has both legs
        # quoted (build_liquid_chain requires both C and P per strike).
        strikes = [
            (58000, 8000, 800), (60000, 6300, 1300), (61000, 5500, 1600),
            (62000, 4750, 1950), (63000, 4050, 2350), (64000, 3400, 2800),
            (64500, 3100, 3050), (65000, 2800, 3300), (65500, 2500, 3600),
            (66000, 2250, 3900), (67000, 1800, 4500), (68000, 1400, 5100),
            (70000, 850, 6400), (72000, 500, 7800),
        ]
        snapshot = _basic_snapshot(strikes)
        regime = {"net_gex": -1000.0, "rv_10d": 45.0, "rv_30d": 40.0, "rv_ratio": 1.125, "gate_pass": False}

        service = ButterflyScanService(api_service=FakeApiService(snapshot), repository=FakeRepository())
        result = service.scan("BTC", regime=regime)

        assert len(result["expiries"]) == 1
        entry = result["expiries"][0]
        assert entry["regime"] == regime
        assert entry["best"]["structure_type"] == "butterfly"
        assert entry["best"]["k1"] < entry["best"]["k2"] < entry["best"]["k3"]
        assert len(entry["candidates"]) > 1

    def test_no_regime_computes_its_own(self):
        strikes = [
            (60000, 6300, 1300), (63000, 4050, 2350), (64000, 3400, 2800),
            (65000, 2800, 3300), (66000, 2250, 3900), (67000, 1800, 4500),
            (70000, 850, 6400),
        ]
        snapshot = _basic_snapshot(strikes)

        class FakeRegimeGateService:
            def compute(self, currency, as_of=None):
                return {"net_gex": None, "rv_10d": None, "rv_30d": None, "rv_ratio": None, "gate_pass": False}

        service = ButterflyScanService(
            api_service=FakeApiService(snapshot),
            repository=FakeRepository(),
            regime_gate_service=FakeRegimeGateService(),
        )
        result = service.scan("BTC")
        # regime not passed -> service must use the injected
        # regime_gate_service rather than constructing a real one.
        assert result["currency"] == "BTC"
        if result["expiries"]:
            assert result["expiries"][0]["regime"]["gate_pass"] is False

    def test_dte_outside_window_excluded(self):
        contracts = [
            _contract(65000, "C", 0.01, 0.0102, 100, dte=2.0, expiry="22JUL26"),
            _contract(65000, "P", 0.01, 0.0102, 100, dte=2.0, expiry="22JUL26"),
        ]
        snapshot = {"as_of": datetime.now(timezone.utc), "index_price": 65000.0,
                    "contracts": contracts, "futures_by_expiry": {"22JUL26": 65000.0}}
        service = ButterflyScanService(api_service=FakeApiService(snapshot), repository=FakeRepository())
        result = service.scan("BTC", regime={"net_gex": 1.0, "rv_10d": 1.0, "rv_30d": 1.0, "rv_ratio": 1.0, "gate_pass": False})
        assert result["expiries"] == []
        assert any(e["expiry"] == "22JUL26" for e in result["excluded"])

    def test_thin_chain_excludes_expiry(self):
        contracts = [_contract(65000, "C", 0.02, 0.0204, 100), _contract(65000, "P", 0.02, 0.0204, 100)]
        snapshot = {"as_of": datetime.now(timezone.utc), "index_price": 65000.0,
                    "contracts": contracts, "futures_by_expiry": {"1SEP26": 65000.0}}
        service = ButterflyScanService(api_service=FakeApiService(snapshot), repository=FakeRepository())
        result = service.scan("BTC", regime={"net_gex": 1.0, "rv_10d": 1.0, "rv_30d": 1.0, "rv_ratio": 1.0, "gate_pass": False})
        assert result["expiries"] == []
        assert any(e["reason"] == "no butterfly candidates constructed" for e in result["excluded"])
