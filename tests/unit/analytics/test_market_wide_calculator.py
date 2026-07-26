"""
Unit tests for MarketWideCalculator.
"""

import logging
import math
import time
import pytest
from datetime import datetime, timedelta, timezone

from coding.core.analytics.market_wide_calculator import MarketWideCalculator
from coding.core.analytics.reporting.market_wide_formatter import format_futures_basis_section
from coding.core.analytics.results.market_wide_results import FuturesBasisResult


def _make_price_history(days=60, base_price=90000):
    """Generate synthetic daily price history."""
    prices = []
    now = time.time()

    for i in range(days):
        ts = now - (days - i) * 86400
        # Add some volatility
        price = base_price * (1 + 0.01 * math.sin(i * 0.5))
        prices.append({"timestamp": ts, "close": price})

    return prices


@pytest.fixture
def calculator():
    return MarketWideCalculator(
        currency="BTC",
        spot_price=90000,
        dvol=65.0,
    )


class TestMarketWideCalculator:
    """Tests for MarketWideCalculator."""

    def test_iv_term_structure_contango(self, calculator):
        atm_ivs = {
            "28FEB26": 70.0,
            "28MAR26": 65.0,
            "27JUN26": 60.0,
        }
        report, structured = calculator.calculate_iv_term_structure(atm_ivs)

        assert "IV TERM STRUCTURE" in report
        assert "28FEB26" in report
        assert "28MAR26" in report
        # Front month (70) > back month (60) = backwardated
        assert "BACKWARDATED" in report
        assert structured["shape"] == "BACKWARDATION"
        assert isinstance(structured["iv_by_dte"], dict)

    def test_iv_term_structure_empty(self, calculator):
        report, structured = calculator.calculate_iv_term_structure({})
        assert "No ATM IV data available" in report
        assert structured["iv_by_dte"] == {}

    def test_futures_basis(self, calculator):
        """T6 carryover (A4 review): calculate_futures_basis returns a typed
        FuturesBasisResult, not a (text, dict) tuple. Text now comes from
        market_wide_formatter.format_futures_basis_section (the dormant
        formatter promoted to the live rendering path for this section)."""
        futures_data = [
            {
                "instrument_name": "BTC-28MAR26",
                "mark_price": 92000,
                "index_price": 90000,
            },
        ]
        result = calculator.calculate_futures_basis(futures_data)
        report = format_futures_basis_section(result)

        assert isinstance(result, FuturesBasisResult)
        assert "FUTURES BASIS" in report
        assert "BTC-28MAR26" in report
        assert "92,000" in report
        assert "futures_basis" in result.to_dict()

    def test_futures_basis_empty(self, calculator):
        result = calculator.calculate_futures_basis([])
        report = format_futures_basis_section(result)
        assert "No futures data available" in report
        assert result.to_dict()["futures_basis"] == {}

    def test_realized_volatility_multi_window(self, calculator):
        prices = _make_price_history(60)
        report, rv_values = calculator.calculate_realized_volatility_multi_window(prices)

        assert "REALIZED VOLATILITY" in report
        assert 10 in rv_values
        assert 20 in rv_values
        assert 30 in rv_values
        # RV should be positive
        for rv in rv_values.values():
            assert rv > 0

    def test_realized_volatility_insufficient_data(self, calculator):
        prices = _make_price_history(5)
        report, rv_values = calculator.calculate_realized_volatility_multi_window(prices)
        assert "Insufficient" in report

    def test_vrp(self, calculator):
        rv_30d = 0.50  # 50% realized vol
        report, structured = calculator.calculate_vrp(rv_30d)

        assert "VOLATILITY RISK PREMIUM" in report
        assert "DVOL: 65.0%" in report
        assert "30d RV: 50.0%" in report
        # VRP = 65 - 50 = +15 pts
        assert "+15.0 pts" in report
        assert "vrp" in structured
        assert structured["vrp"] == pytest.approx(15.0, abs=0.1)

    def test_vrp_no_dvol(self):
        calc = MarketWideCalculator("BTC", 90000, dvol=None)
        report, structured = calc.calculate_vrp(0.50)
        assert "DVOL not available" in report

    def test_volatility_cone(self, calculator):
        prices = _make_price_history(120)
        report, structured = calculator.calculate_volatility_cone(prices)

        assert "VOLATILITY CONE" in report
        assert "10d" in report
        assert "Current" in report
        assert "Median" in report
        assert "cone_10d_pctile" in structured
        assert "cone_30d_pctile" in structured

    def test_volatility_cone_insufficient_data(self, calculator):
        prices = _make_price_history(10)
        report, structured = calculator.calculate_volatility_cone(prices)
        assert "Insufficient" in report
        assert structured["cone_30d_pctile"] == 0.0

    def test_perpetual_funding_trend(self, calculator):
        """bugfix_spec.md Item 4: real Deribit funding_chart_data point shape
        is {"data": [...], ...} where each point is a dict keyed
        interest_8h (not the old funding_rate/value/list-pair shape)."""
        points = [
            {"timestamp": 1784916000000 + i * 3_600_000, "index_price": 64000.0, "interest_8h": r}
            for i, r in enumerate([4.0e-05] * 24 + [6.0e-06] * 8)
        ]
        funding_data = {"data": points}
        perp_ticker = {
            "open_interest": 125000,
            "current_funding": 0.0001,
            "funding_8h": 0.0003,
        }

        report, structured = calculator.calculate_perpetual_funding_trend(
            funding_data, perp_ticker
        )

        assert "PERPETUAL FUNDING" in report
        assert "125,000" in report
        assert "0.0100%" in report
        assert structured["perp_oi"] == 125000
        assert structured["funding_8h"] == 0.0003
        assert structured["perp_funding_trend"] == "Falling"


class TestFundingRateExtractionAndTrend:
    """
    bugfix_spec.md Item 4 (F4.3/F4.5): the perp funding trend was always
    "Stable" because the code read funding_rate/value keys that don't exist
    on a get_funding_chart_data point (real key is interest_8h), and
    annualization used the instantaneous current_funding instead of the
    realised funding_8h rate. Acceptance tests T4.1-T4.5 verbatim.
    """

    def test_real_deribit_point_shape_is_parsed(self, calculator):
        """T4.1"""
        rates = [6.458e-06, 4.181e-06, 1.414e-06, 8.094e-07, 4.441e-07,
                 4.417e-07, 3.779e-07, 2.118e-07, 1.167e-07, 7.387e-07]
        points = [
            {"timestamp": 1784916000000 + i * 3_600_000, "index_price": 64000.0, "interest_8h": r}
            for i, r in enumerate(rates)
        ]
        extracted = calculator._extract_funding_rates({"data": points})
        assert extracted == rates

    def test_falling_is_detected_on_a_decaying_series(self, calculator):
        """T4.2 - 32 points: first 24 (baseline) all 4.0e-05, last 8 (recent)
        all 6.0e-06. change = 6.0e-06 - 4.0e-05 = -3.4e-05; |change| >
        1.0e-05 -> "Falling"."""
        rates = [4.0e-05] * 24 + [6.0e-06] * 8
        assert calculator._classify_funding_trend(rates) == "Falling"

    def test_near_zero_wobble_is_stable(self, calculator):
        """T4.3 - old ratio rule said "Rising" (3.0e-06 > 1.0e-06 * 1.2);
        the additive rule correctly says "Stable" (change = +2.0e-06, below
        the 1.0e-05 threshold)."""
        rates = [1.0e-06] * 24 + [3.0e-06] * 8
        assert calculator._classify_funding_trend(rates) == "Stable"

    def test_key_absence_is_loud_not_silent(self, calculator, caplog):
        """T4.4 - a point without interest_8h must warn (naming the observed
        keys) and return [] rather than silently defaulting to 0."""
        points = [{"timestamp": 1, "index_price": 64000.0, "funding": 1e-5}] * 30
        with caplog.at_level(logging.WARNING):
            rates = calculator._extract_funding_rates({"data": points})
        assert rates == []
        assert "interest_8h" in caplog.text
        assert calculator._classify_funding_trend(rates) == "N/A"

    def test_annualization_uses_funding_8h(self, calculator):
        """T4.5 - 1.41e-06 * 3 * 365 * 100 = 0.154395%; must not use
        current_funding (0.0) for annualization, and "Instantaneous
        funding" must print the raw current_funding value."""
        points = [
            {"timestamp": 1784916000000 + i * 3_600_000, "index_price": 64000.0, "interest_8h": 1.41e-06}
            for i in range(12)
        ]
        report, structured = calculator.calculate_perpetual_funding_trend(
            {"data": points},
            {"open_interest": 737_684_240, "current_funding": 0.0, "funding_8h": 1.41e-06},
        )
        assert structured["funding_annualized_pct"] == pytest.approx(0.154395, rel=1e-9)
        assert "Annualized: 0.15%" in report
        assert "Instantaneous funding: 0.0000%" in report

    def test_fewer_than_minimum_points_is_not_available(self, calculator):
        """Edge case: fewer than FUNDING_TREND_MINIMUM_POINTS (12) -> "N/A",
        never "Stable"."""
        assert calculator._classify_funding_trend([1e-5] * 5) == "N/A"

    def test_current_funding_zero_is_not_treated_as_missing(self, calculator):
        """Edge case: current_funding == 0.0 is a legitimate value and must
        still render (not be suppressed as if missing)."""
        report, structured = calculator.calculate_perpetual_funding_trend(
            {"data": []},
            {"open_interest": 1_000_000, "current_funding": 0.0, "funding_8h": None},
        )
        assert "Instantaneous funding: 0.0000%" in report
        assert "Funding (8h): not available" in report

    def test_block_trade_detection(self, calculator):
        trades = [
            {
                "instrument_name": "BTC-28MAR26-90000-C",
                "amount": 5.0,
                "price": 0.05,
                "index_price": 90000,
                "direction": "buy",
                "timestamp": int(time.time() * 1000),
                "iv": 65.0,
            },
            {
                "instrument_name": "BTC-28MAR26-80000-P",
                "amount": 0.5,
                "price": 0.02,
                "index_price": 90000,
                "direction": "sell",
                "timestamp": int(time.time() * 1000),
                "iv": 70.0,
            },
        ]

        report, structured = calculator.detect_block_trades(trades, notional_threshold=100_000)

        assert "BLOCK TRADES" in report
        # 5.0 * 90000 = 450000 > threshold
        assert "BTC-28MAR26-90000-C" in report
        # 0.5 * 90000 = 45000 < threshold - should NOT appear
        assert "BTC-28MAR26-80000-P" not in report
        assert len(structured["block_trades"]) == 1

    def test_block_trade_no_data(self, calculator):
        report, structured = calculator.detect_block_trades([])
        assert "No recent trade data" in report
        assert structured["block_trades"] == []

    def test_cross_asset_correlation(self, calculator):
        own_prices = _make_price_history(35, base_price=90000)
        # ETH prices correlated with BTC
        other_prices = _make_price_history(35, base_price=3000)

        report, structured = calculator.calculate_cross_asset_correlation(
            own_prices=own_prices,
            other_prices=other_prices,
            own_dvol_history=[60 + i * 0.1 for i in range(30)],
            other_dvol_history=[55 + i * 0.15 for i in range(30)],
            other_currency="ETH",
        )

        assert "CROSS-ASSET CORRELATION" in report
        assert "Price Correlation" in report
        assert "DVOL Correlation" in report
        assert "btc_eth_price_corr" in structured
        assert "btc_eth_dvol_corr" in structured

    def test_dte_calculation(self):
        # Test a known future date. now must be timezone-aware UTC per
        # bugfix_spec.md Item 5 F5.3.1 (settlement is 08:00 UTC, not local
        # midnight) — the integer _calculate_dte is now floor(exact fractional
        # days), which floors 30.3333 -> 30, matching the pre-fix value here.
        now = datetime(2026, 2, 26, tzinfo=timezone.utc)
        dte = MarketWideCalculator._calculate_dte("28MAR26", now)
        assert dte == 30

    def test_dte_invalid(self):
        dte = MarketWideCalculator._calculate_dte("INVALID", datetime.now(timezone.utc))
        assert dte is None

    def test_dte_past(self):
        # Past expiration should return 0 (floor(-3.667) = -4, clamped to 0)
        now = datetime(2026, 4, 1, tzinfo=timezone.utc)
        dte = MarketWideCalculator._calculate_dte("28MAR26", now)
        assert dte == 0


class TestExactFractionalDaysToExpiry:
    """
    bugfix_spec.md Item 5 (F5.3.1): exact fractional DTE to 08:00 UTC
    settlement, replacing the naive-datetime/local-midnight/integer-truncation
    triple bug. Hand-computed numbers verbatim from section 5.5, T5.1.
    """

    def test_exact_fractional_dte_at_settlement_hour(self):
        now = datetime(2026, 7, 25, 8, 0, 0, tzinfo=timezone.utc)
        assert MarketWideCalculator._calculate_days_to_expiry("27JUL26", now) == pytest.approx(2.0)
        assert MarketWideCalculator._calculate_days_to_expiry("26JUL26", now) == pytest.approx(1.0)
        assert MarketWideCalculator._calculate_days_to_expiry("25JUL26", now) == pytest.approx(0.0)

    def test_exact_fractional_dte_mid_day(self):
        now2 = datetime(2026, 7, 25, 20, 0, 0, tzinfo=timezone.utc)
        assert MarketWideCalculator._calculate_days_to_expiry("26JUL26", now2) == pytest.approx(0.5)

    def test_invalid_expiration_returns_none(self):
        now = datetime(2026, 7, 25, 8, 0, 0, tzinfo=timezone.utc)
        assert MarketWideCalculator._calculate_days_to_expiry("INVALID", now) is None


class TestFuturesBasisAnnualization:
    """
    bugfix_spec.md Item 5 (F5.3.2): annualized simple basis to 08:00 UTC
    settlement, with sub-daily suppression. Hand-computed numbers verbatim
    from section 5.5, T5.2-T5.4.
    """

    def test_annualized_basis_at_two_days(self, calculator):
        """T5.2 - old code: dte=1 (naive local-midnight truncation) -> 73.00%
        (exactly 2x wrong). Fixed: T=2.0 exact days -> 36.50%.

        T6 carryover (A4 review): calculate_futures_basis now returns a
        typed FuturesBasisResult; report text comes from the promoted
        market_wide_formatter.format_futures_basis_section."""
        result = calculator.calculate_futures_basis(
            [{"instrument_name": "BTC-27JUL26", "mark_price": 100_200.0, "index_price": 100_000.0}],
            now_utc=datetime(2026, 7, 25, 8, 0, tzinfo=timezone.utc),
        )
        report = format_futures_basis_section(result)
        assert result.to_dict()["futures_basis"]["27JUL26"] == pytest.approx(36.50, rel=1e-6)
        assert "36.5" in report

    def test_sub_daily_basis_is_suppressed(self, calculator):
        """T5.3 - T=8h=0.33333 days; annualizing would give a meaningless
        219.00%. Must suppress to None and print 'n/a (<1d)', while still
        showing the raw (unannualized) basis."""
        result = calculator.calculate_futures_basis(
            [{"instrument_name": "BTC-26JUL26", "mark_price": 100_200.0, "index_price": 100_000.0}],
            now_utc=datetime(2026, 7, 26, 0, 0, tzinfo=timezone.utc),
        )
        report = format_futures_basis_section(result)
        assert result.to_dict()["futures_basis"]["26JUL26"] is None
        assert "n/a (<1d)" in report
        assert "0.2000" in report or "0.20" in report  # raw basis still shown

    def test_long_tenor_unchanged_by_the_fix(self, calculator):
        """T5.4 - regression guard: at 152.5992 days the naive-vs-exact
        difference is negligible (~0.036 pt); the fix must not disturb it.

        bugfix_spec.md section 5.1's live confirmation-evidence sweep was
        recorded 2026-07-25 17:37 UTC (front contract DTE 0.5992d to 26JUL26
        08:00 UTC settlement); 2026-07-25 17:37:09.12 UTC reproduces the
        spec's exact T=152.5992d for the 25DEC26 tenor bit-for-bit — the
        spec's T5.4 snippet doesn't show its now_utc explicitly, so this is
        derived rather than guessed.
        """
        now_utc = datetime(2026, 7, 25, 17, 37, 9, 120_000, tzinfo=timezone.utc)
        assert MarketWideCalculator._calculate_days_to_expiry("25DEC26", now_utc) == pytest.approx(152.5992, abs=1e-4)

        result = calculator.calculate_futures_basis(
            [{"instrument_name": "BTC-25DEC26", "mark_price": 103_876.1, "index_price": 100_000.0}],
            now_utc=now_utc,
        )
        assert result.to_dict()["futures_basis"]["25DEC26"] == pytest.approx(9.271192, rel=1e-6)

    def test_expired_future_still_listed_reports_none_annualized(self, calculator):
        """Edge case: days <= 0 (expired but still returned by the API) ->
        annualized None, raw basis still computed/persisted, no crash."""
        result = calculator.calculate_futures_basis(
            [{"instrument_name": "BTC-20JUL26", "mark_price": 100_100.0, "index_price": 100_000.0}],
            now_utc=datetime(2026, 7, 25, 8, 0, tzinfo=timezone.utc),
        )
        report = format_futures_basis_section(result)
        assert result.to_dict()["futures_basis"]["20JUL26"] is None
        assert "n/a (expired)" in report

    def test_dte_field_populated_on_entry(self, calculator):
        """FuturesBasisEntry.dte carries the floored fractional DTE, used by
        the F5.3.3 persistence writer's days_to_expiry column (deferred to
        Wave E — see task-A5-report.md) and available for programmatic use."""
        result = calculator.calculate_futures_basis(
            [{"instrument_name": "BTC-27JUL26", "mark_price": 100_200.0, "index_price": 100_000.0}],
            now_utc=datetime(2026, 7, 25, 8, 0, tzinfo=timezone.utc),
        )
        assert result.entries[0].dte == 2
