"""
Unit tests for IronCondorScanService. FakeApiService/FakeRepository stand
in for DeribitApiService/DatabaseRepository -- no live API/DB, same pattern
as tests/unit/test_straddle_scan_service.py.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List

from coding.service.scanner.iron_condor_scan_service import IronCondorScanService


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
    # real bid_usd = bid_price * index_price relationship.
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
    _sigma_sqrt_t degrades to None (see build_iron_condor_candidates'
    handling of sigma_sqrt_t=None). Keeps these tests DB-free, matching
    the module docstring and tests/unit/test_straddle_scan_service.py's
    own FakeRepository convention -- IronCondorScanService._sigma_sqrt_t
    would otherwise default-construct a real DatabaseRepository()."""

    def get_ohlcv_by_date_range(self, currency, start, end):
        return []


class TestIronCondorScan:
    def test_produces_candidates_and_ranks_by_ev(self):
        # (strike, call_price_usd, put_price_usd) -- call side must decay
        # monotonically as strike rises above F=65000, put side must decay
        # monotonically as strike falls below F, for the short/long legs to
        # net a positive credit (the value only ~66000+/~64000- on each
        # respective side actually participates in candidate construction --
        # see build_iron_condor_candidates, which only pairs k1>F calls and
        # k3<F puts).
        strikes = [(58000, 6800, 80), (60000, 4800, 200), (61000, 3800, 350),
                   (62000, 2900, 550), (63000, 2100, 800), (64000, 1400, 1200),
                   (65000, 1800, 1800), (66000, 1300, 700), (67000, 950, 450),
                   (68000, 700, 300), (70000, 400, 150), (72000, 220, 80), (78000, 60, 20)]
        snapshot = _basic_snapshot(strikes)
        regime = {"net_gex": 1000.0, "rv_10d": 40.0, "rv_30d": 45.0, "rv_ratio": 0.888, "gate_pass": True}

        service = IronCondorScanService(api_service=FakeApiService(snapshot), repository=FakeRepository())
        result = service.scan("BTC", regime=regime)

        assert result["currency"] == "BTC"
        assert len(result["expiries"]) == 1
        entry = result["expiries"][0]
        assert entry["expiry"] == "1SEP26"
        assert entry["regime"] == regime
        assert entry["best"]["structure_type"] == "iron_condor"
        assert len(entry["candidates"]) > 1
        # ranked by ev descending
        evs = [c["ev"] for c in entry["candidates"] if c["ev"] is not None]
        assert evs == sorted(evs, reverse=True)

    def test_no_regime_computes_its_own(self):
        strikes = [(60000, 200, 3000), (65000, 1800, 1800), (70000, 2500, 300)]
        snapshot = _basic_snapshot(strikes)

        class FakeRegimeGateService:
            def compute(self, currency, as_of=None):
                return {"net_gex": None, "rv_10d": None, "rv_30d": None, "rv_ratio": None, "gate_pass": False}

        service = IronCondorScanService(
            api_service=FakeApiService(snapshot),
            repository=FakeRepository(),
            regime_gate_service=FakeRegimeGateService(),
        )
        result = service.scan("BTC")
        # thin chain (few strikes) -> likely no candidates, but the call must not raise
        assert result["currency"] == "BTC"

    def test_dte_outside_window_excluded(self):
        contracts = [
            _contract(65000, "C", 0.01, 0.0102, 100, dte=2.0, expiry="22JUL26"),
            _contract(65000, "P", 0.01, 0.0102, 100, dte=2.0, expiry="22JUL26"),
        ]
        snapshot = {"as_of": datetime.now(timezone.utc), "index_price": 65000.0,
                    "contracts": contracts, "futures_by_expiry": {"22JUL26": 65000.0}}
        service = IronCondorScanService(api_service=FakeApiService(snapshot), repository=FakeRepository())
        result = service.scan("BTC", regime={"net_gex": 1.0, "rv_10d": 1.0, "rv_30d": 1.0, "rv_ratio": 1.0, "gate_pass": False})
        assert result["expiries"] == []
        assert any(e["expiry"] == "22JUL26" for e in result["excluded"])
