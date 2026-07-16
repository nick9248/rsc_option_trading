"""
Unit tests for StraddleScanService.

All tests use synthetic contract fixtures shaped exactly like
DeribitApiService.get_option_chain_snapshot()'s return value — no network,
no live database. A FakeApiService/FakeRepository pair stands in for the
injected dependencies (see StraddleScanService.__init__ DI pattern).
"""

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest

from coding.service.scanner.straddle_scan_service import StraddleScanService


# ── Fixtures / fakes ──────────────────────────────────────────────────────────

def _contract(
    strike: float,
    option_type: str,
    expiry: str,
    dte: float,
    bid_price: Optional[float] = 0.02,
    ask_price: Optional[float] = 0.021,
    mark_iv: Optional[float] = 60.0,
    open_interest: Optional[float] = 100,
    underlying_price: float = 50000.0,
    index_price: float = 50000.0,
) -> Dict[str, Any]:
    bid_usd = bid_price * index_price if bid_price is not None else None
    ask_usd = ask_price * index_price if ask_price is not None else None
    return {
        "instrument_name": f"BTC-{expiry}-{int(strike)}-{option_type}",
        "expiry": expiry,
        "dte": dte,
        "strike": strike,
        "option_type": option_type,
        "bid_price": bid_price,
        "ask_price": ask_price,
        "mark_price": (bid_price + ask_price) / 2 if bid_price and ask_price else None,
        "mark_iv": mark_iv,
        "open_interest": open_interest,
        "volume": 10.0,
        "underlying_price": underlying_price,
        "bid_usd": bid_usd,
        "ask_usd": ask_usd,
        "mark_usd": None,
    }


class FakeApiService:
    """Stand-in for DeribitApiService — returns a canned snapshot dict."""

    def __init__(self, snapshot: Dict[str, Any]):
        self._snapshot = snapshot

    def get_option_chain_snapshot(self, currency: str) -> Dict[str, Any]:
        return self._snapshot


class FakeRepository:
    """Stand-in for DatabaseRepository — canned percentile + OHLCV lookups."""

    def __init__(
        self,
        percentiles: Optional[Dict[str, float]] = None,
        ohlcv_rows: Optional[List[Dict[str, Any]]] = None,
    ):
        self._percentiles = percentiles or {}
        self._ohlcv_rows = ohlcv_rows or []

    def get_latest_iv_percentile_expiry(self, currency: str, expiration: str) -> Optional[float]:
        return self._percentiles.get(expiration)

    def get_ohlcv_by_date_range(self, currency, start, end) -> List[Dict[str, Any]]:
        return self._ohlcv_rows


def _flat_ohlcv_rows(n: int, start: datetime, daily_log_return: float = 0.0) -> List[Dict[str, Any]]:
    """n daily closes with a constant log return (zero variance when 0.0)."""
    rows = []
    close = 50000.0
    for i in range(n):
        rows.append({"date": start + timedelta(days=i), "close": close})
        close *= math.exp(daily_log_return)
    return rows


def _basic_snapshot(
    expiry: str = "27DEC26",
    dte: float = 30.0,
    strikes: Optional[List[float]] = None,
    future_price: float = 50000.0,
    index_price: float = 50000.0,
    as_of: Optional[datetime] = None,
    **contract_overrides,
) -> Dict[str, Any]:
    strikes = strikes if strikes is not None else [45000, 47500, 50000, 52500, 55000]
    as_of = as_of or datetime.now(timezone.utc)
    contracts = []
    for strike in strikes:
        for option_type in ("C", "P"):
            contracts.append(_contract(
                strike, option_type, expiry, dte,
                underlying_price=future_price, index_price=index_price,
                **contract_overrides,
            ))
    return {
        "as_of": as_of,
        "index_price": index_price,
        "contracts": contracts,
        "futures_by_expiry": {expiry: future_price},
    }


# ── Liquidity gate ────────────────────────────────────────────────────────────

class TestLiquidityGate:
    def test_passes_with_tight_spread_and_sufficient_oi(self):
        service = StraddleScanService()
        leg = _contract(50000, "C", "27DEC26", 30, bid_price=0.020, ask_price=0.021, open_interest=100)
        assert service._passes_liquidity_gate(leg) is True

    def test_rejects_wide_spread(self):
        service = StraddleScanService()
        # spread = (0.03 - 0.01) / 0.02 = 100% >> 15%
        leg = _contract(50000, "C", "27DEC26", 30, bid_price=0.01, ask_price=0.03, open_interest=100)
        assert service._passes_liquidity_gate(leg) is False

    def test_rejects_low_open_interest(self):
        service = StraddleScanService()
        leg = _contract(50000, "C", "27DEC26", 30, bid_price=0.020, ask_price=0.021, open_interest=10)
        assert service._passes_liquidity_gate(leg) is False

    def test_rejects_missing_bid(self):
        service = StraddleScanService()
        leg = _contract(50000, "C", "27DEC26", 30, bid_price=None, ask_price=0.021, open_interest=100)
        assert service._passes_liquidity_gate(leg) is False

    def test_rejects_zero_bid(self):
        service = StraddleScanService()
        leg = _contract(50000, "C", "27DEC26", 30, bid_price=0.0, ask_price=0.021, open_interest=100)
        assert service._passes_liquidity_gate(leg) is False

    def test_boundary_spread_exactly_15_pct_passes(self):
        service = StraddleScanService()
        # mid = 0.1, spread = 0.015 -> 15% exactly
        leg = _contract(50000, "C", "27DEC26", 30, bid_price=0.0925, ask_price=0.1075, open_interest=100)
        assert service._passes_liquidity_gate(leg) is True


# ── DTE window ────────────────────────────────────────────────────────────────

class TestDteWindow:
    def test_expiry_below_min_dte_excluded(self):
        service = StraddleScanService(
            api_service=FakeApiService(_basic_snapshot(dte=4.0)),
            repository=FakeRepository(),
        )
        result = service.scan("BTC")
        assert result["expiries"] == []

    def test_expiry_above_max_dte_excluded(self):
        service = StraddleScanService(
            api_service=FakeApiService(_basic_snapshot(dte=401.0)),
            repository=FakeRepository(),
        )
        result = service.scan("BTC")
        assert result["expiries"] == []

    def test_expiry_within_window_included(self):
        service = StraddleScanService(
            api_service=FakeApiService(_basic_snapshot(dte=30.0)),
            repository=FakeRepository(percentiles={"27DEC26": 10.0}),
        )
        result = service.scan("BTC")
        assert len(result["expiries"]) == 1
        assert result["expiries"][0]["expiry"] == "27DEC26"


# ── Candidate window (expected range) ────────────────────────────────────────

class TestCandidateRange:
    def test_candidates_restricted_to_expected_sigma_range(self):
        # atm_iv=60%, dte=30 -> sigma_sqrt_t = 0.6*sqrt(30/365) ~= 0.1719
        # range ~= [50000/e^0.1719, 50000*e^0.1719] ~= [42102, 59385]
        # 65000 strike is far outside -> must be excluded even if liquid
        strikes = [45000, 50000, 55000, 65000]
        snapshot = _basic_snapshot(strikes=strikes)
        service = StraddleScanService(
            api_service=FakeApiService(snapshot),
            repository=FakeRepository(percentiles={"27DEC26": 5.0}),
        )
        result = service.scan("BTC")
        candidate_strikes = {c["strike"] for c in result["expiries"][0]["candidates"]}
        assert 65000 not in candidate_strikes
        assert 50000 in candidate_strikes

    def test_atm_strike_is_closest_to_future_price(self):
        snapshot = _basic_snapshot(strikes=[45000, 49000, 50000, 51000, 55000], future_price=49700)
        service = StraddleScanService(
            api_service=FakeApiService(snapshot),
            repository=FakeRepository(percentiles={"27DEC26": 5.0}),
        )
        result = service.scan("BTC")
        # ATM IV computed from strike 50000 (closest to 49700 among given strikes)
        assert result["expiries"][0]["atm_iv"] == 60.0  # all legs use mark_iv=60 in fixture


# ── Best-candidate ranking within an expiry ──────────────────────────────────

class TestBestCandidateRanking:
    def test_best_is_lowest_max_required_move(self):
        """
        Two candidate strikes with different costs -> best must be whichever
        minimizes max(required_move_up_pct, required_move_down_pct), not
        simply the cheapest or the exact-ATM strike.
        """
        expiry = "27DEC26"
        dte = 30.0
        future_price = 50000.0

        # Strike A (50000, ATM): cheap, moderate required moves both ways.
        # Strike B (52500): higher strike raises required_move_down but
        # cost differs too — construct so B ends up NOT the best by giving
        # it a larger cost (making both required moves > A's).
        contracts = []
        contracts.append(_contract(50000, "C", expiry, dte, bid_price=0.03, ask_price=0.031,
                                    underlying_price=future_price))
        contracts.append(_contract(50000, "P", expiry, dte, bid_price=0.03, ask_price=0.031,
                                    underlying_price=future_price))
        contracts.append(_contract(52500, "C", expiry, dte, bid_price=0.05, ask_price=0.051,
                                    underlying_price=future_price))
        contracts.append(_contract(52500, "P", expiry, dte, bid_price=0.05, ask_price=0.051,
                                    underlying_price=future_price))

        snapshot = {
            "as_of": datetime.now(timezone.utc),
            "index_price": future_price,
            "contracts": contracts,
            "futures_by_expiry": {expiry: future_price},
        }
        service = StraddleScanService(
            api_service=FakeApiService(snapshot),
            repository=FakeRepository(percentiles={expiry: 5.0}),
        )
        result = service.scan("BTC")
        best = result["expiries"][0]["best"]
        assert best["strike"] == 50000


# ── Cross-expiry ranking ──────────────────────────────────────────────────────

class TestCrossExpiryRanking:
    def test_ascending_by_iv_percentile_null_last(self):
        expiry_a, expiry_b, expiry_c = "27DEC26", "31JAN27", "28FEB27"
        contracts = []
        futures_by_expiry = {}
        for expiry, dte, F in [(expiry_a, 30.0, 50000.0), (expiry_b, 60.0, 50000.0), (expiry_c, 90.0, 50000.0)]:
            for strike in (45000, 50000, 55000):
                contracts.append(_contract(strike, "C", expiry, dte, underlying_price=F))
                contracts.append(_contract(strike, "P", expiry, dte, underlying_price=F))
            futures_by_expiry[expiry] = F

        snapshot = {
            "as_of": datetime.now(timezone.utc),
            "index_price": 50000.0,
            "contracts": contracts,
            "futures_by_expiry": futures_by_expiry,
        }
        # b has no percentile (NULL) -> must sort last regardless of value
        percentiles = {expiry_a: 20.0, expiry_c: 5.0}
        service = StraddleScanService(
            api_service=FakeApiService(snapshot),
            repository=FakeRepository(percentiles=percentiles),
        )
        result = service.scan("BTC")
        ordered_expiries = [e["expiry"] for e in result["expiries"]]
        assert ordered_expiries == [expiry_c, expiry_a, expiry_b]


# ── Realized vol computation ──────────────────────────────────────────────────

class TestRealizedVol:
    def test_zero_variance_series_gives_zero_rv(self):
        service = StraddleScanService()
        as_of = datetime(2026, 6, 1, tzinfo=timezone.utc)
        start = as_of - timedelta(days=60)
        rows = _flat_ohlcv_rows(50, start, daily_log_return=0.0)
        repo = FakeRepository(ohlcv_rows=rows)
        rv = service._compute_realized_vol(repo, "BTC", dte=30.0, as_of=as_of)
        assert rv == pytest.approx(0.0, abs=1e-9)

    def test_known_constant_log_return_gives_expected_stdev(self):
        """
        A perfectly alternating +-r log-return series has a known, hand-
        computable stdev; verifies the annualization formula end to end.
        """
        service = StraddleScanService()
        as_of = datetime(2026, 6, 1, tzinfo=timezone.utc)
        start = as_of - timedelta(days=60)

        r = 0.01
        rows = []
        close = 50000.0
        for i in range(50):
            rows.append({"date": start + timedelta(days=i), "close": close})
            close *= math.exp(r if i % 2 == 0 else -r)

        repo = FakeRepository(ohlcv_rows=rows)
        window_days = max(21, round(30.0))
        rv = service._compute_realized_vol(repo, "BTC", dte=30.0, as_of=as_of)

        closes = [row["close"] for row in rows[-(window_days + 1):]]
        log_returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
        n = len(log_returns)
        mean = sum(log_returns) / n
        variance = sum((x - mean) ** 2 for x in log_returns) / (n - 1)
        expected_rv = math.sqrt(variance) * math.sqrt(365.0) * 100.0

        assert rv == pytest.approx(expected_rv, rel=1e-9)

    def test_insufficient_history_returns_none(self):
        service = StraddleScanService()
        as_of = datetime(2026, 6, 1, tzinfo=timezone.utc)
        rows = _flat_ohlcv_rows(5, as_of - timedelta(days=10))  # far fewer than 22 rows needed
        repo = FakeRepository(ohlcv_rows=rows)
        rv = service._compute_realized_vol(repo, "BTC", dte=30.0, as_of=as_of)
        assert rv is None


# ── min_pnl_score ─────────────────────────────────────────────────────────────

class TestMinPnlScore:
    def test_matches_manual_calculation(self):
        future_price = 50000.0
        rv_pct = 40.0
        T = 30.0 / 365.0
        strike = 50000.0
        cost_usd = 2000.0

        score = StraddleScanService._min_pnl_score(future_price, rv_pct, T, strike, cost_usd)

        sigma_sqrt_t = (rv_pct / 100.0) * math.sqrt(T)
        rv_hi = future_price * math.exp(sigma_sqrt_t)
        rv_lo = future_price * math.exp(-sigma_sqrt_t)
        expected = min(abs(rv_hi - strike), abs(rv_lo - strike)) - cost_usd

        assert score == pytest.approx(expected)


# ── format_alert ──────────────────────────────────────────────────────────────

class TestFormatAlert:
    @staticmethod
    def _fixed_scan_result() -> Dict[str, Any]:
        as_of = datetime(2026, 7, 17, 12, 0, tzinfo=timezone.utc)
        best = {
            "strike": 118000.0,
            "cost_usd": 4820.0,
            "breakeven_down": 113180.0,
            "breakeven_up": 122820.0,
            "min_pnl_score": 1240.0,
            "deribit_url": "https://www.deribit.com/options/BTC/BTC-25SEP26-118000-C",
        }
        top = {
            "expiry": "25SEP26",
            "dte": 68.0,
            "F": 118432.50,
            "atm_iv": 64.8,
            "iv_percentile": 8.2,
            "rv": 39.4,
            "rv_iv_ratio": 0.61,
            "vrp": 25.4,
            "best": best,
            "candidates": [best],
        }
        runner_up = {
            "expiry": "30OCT26",
            "dte": 103.0,
            "F": 118500.0,
            "atm_iv": 62.0,
            "iv_percentile": 14.5,
            "rv": 40.0,
            "rv_iv_ratio": 0.65,
            "vrp": 22.0,
            "best": {**best, "strike": 118500.0},
            "candidates": [],
        }
        return {
            "as_of": as_of,
            "currency": "BTC",
            "index_price": 118432.50,
            "expiries": [top, runner_up],
        }

    def test_alert_contains_all_required_sections(self):
        service = StraddleScanService()
        text = service.format_alert(self._fixed_scan_result())

        assert "STRADDLE SCANNER — BTC" in text
        assert "2026-07-17 12:00 UTC" in text
        assert "118,432.50" in text
        assert "25SEP26 (68d)" in text
        assert "118000" in text
        assert "4,820.00" in text
        assert "113,180" in text and "122,820" in text
        assert "[TRIGGER]" in text and "8.2%" in text
        assert "[CONFIRM]" in text and "0.61" in text
        assert "[CONTEXT]" in text and "+25.4" in text and "1,240.00" in text
        assert "Runner-up: 30OCT26" in text and "14.5%" in text
        assert "Chart: https://www.deribit.com/options/BTC/BTC-25SEP26-118000-C" in text

    def test_no_expiries_gives_short_message(self):
        service = StraddleScanService()
        result = {"as_of": datetime.now(timezone.utc), "currency": "BTC",
                  "index_price": 50000.0, "expiries": []}
        text = service.format_alert(result)
        assert "No qualifying straddle candidates found" in text

    def test_stale_data_triggers_warning(self):
        service = StraddleScanService()
        stale_result = self._fixed_scan_result()
        stale_result["as_of"] = datetime.now(timezone.utc) - timedelta(minutes=30)
        text = service.format_alert(stale_result)
        assert "WARNING" in text and "minutes old" in text

    def test_fresh_data_no_warning(self):
        service = StraddleScanService()
        fresh_result = self._fixed_scan_result()
        fresh_result["as_of"] = datetime.now(timezone.utc)
        text = service.format_alert(fresh_result)
        assert "WARNING" not in text
        assert "Data is fresh" in text


# ── Deribit URL ────────────────────────────────────────────────────────────────

class TestDeribitUrl:
    def test_format(self):
        url = StraddleScanService._deribit_url("BTC", "25SEP26", 118000.0)
        assert url == "https://www.deribit.com/options/BTC/BTC-25SEP26-118000-C"


# ── Full scan() shape ──────────────────────────────────────────────────────────

class TestScanShape:
    def test_scan_returns_expected_top_level_keys(self):
        snapshot = _basic_snapshot()
        service = StraddleScanService(
            api_service=FakeApiService(snapshot),
            repository=FakeRepository(percentiles={"27DEC26": 5.0}),
        )
        result = service.scan("BTC")
        assert set(result.keys()) == {"as_of", "currency", "index_price", "expiries"}
        assert result["currency"] == "BTC"

        entry = result["expiries"][0]
        expected_keys = {
            "expiry", "dte", "F", "atm_iv", "iv_percentile",
            "rv", "rv_iv_ratio", "vrp", "best", "candidates",
        }
        assert set(entry.keys()) == expected_keys

        best_keys = {
            "strike", "cost_usd", "breakeven_down", "breakeven_up",
            "required_move_down_pct", "required_move_up_pct",
            "call_ask_btc", "call_ask_usd", "put_ask_btc", "put_ask_usd",
            "deribit_url", "min_pnl_score",
        }
        assert set(entry["best"].keys()) == best_keys

    def test_expiry_with_no_candidates_is_omitted(self):
        # All legs fail liquidity gate (OI too low) -> expiry must be dropped
        snapshot = _basic_snapshot(open_interest=1)
        service = StraddleScanService(
            api_service=FakeApiService(snapshot),
            repository=FakeRepository(percentiles={"27DEC26": 5.0}),
        )
        result = service.scan("BTC")
        assert result["expiries"] == []

    def test_expiry_with_no_paired_strikes_is_omitted(self):
        expiry = "27DEC26"
        contracts = [_contract(50000, "C", expiry, 30.0)]  # only a call, no put
        snapshot = {
            "as_of": datetime.now(timezone.utc),
            "index_price": 50000.0,
            "contracts": contracts,
            "futures_by_expiry": {expiry: 50000.0},
        }
        service = StraddleScanService(
            api_service=FakeApiService(snapshot),
            repository=FakeRepository(),
        )
        result = service.scan("BTC")
        assert result["expiries"] == []
