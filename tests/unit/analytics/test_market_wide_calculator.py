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
        # Task G2-C: now_utc is a required, explicit, timezone-aware
        # parameter (VRPCalculator.calculate_realized_volatility no longer
        # defaults it internally to a naive, non-deterministic
        # datetime.now()). Real "now" is correct here since _make_price_
        # history anchors its synthetic bars on time.time() (real "now").
        report, rv_values = calculator.calculate_realized_volatility_multi_window(
            prices, now_utc=datetime.now(timezone.utc)
        )

        assert "REALIZED VOLATILITY" in report
        assert 10 in rv_values
        assert 20 in rv_values
        assert 30 in rv_values
        # RV should be positive
        for rv in rv_values.values():
            assert rv > 0

    def test_realized_volatility_insufficient_data(self, calculator):
        prices = _make_price_history(5)
        report, rv_values = calculator.calculate_realized_volatility_multi_window(
            prices, now_utc=datetime.now(timezone.utc)
        )
        assert "Insufficient" in report

    def test_realized_volatility_partial_window_insufficiency_does_not_crash(self, calculator):
        """
        Wave H Task H-D: VRPCalculator.calculate_realized_volatility now
        returns None (never a fabricated 0.0) per-window when too few bars
        survive that window's filter. 11 bars clustered between 11 and 20
        days ago (plus one bar at 25 days ago, to satisfy the outer >= 11
        bars gate) leave the 10d window with ZERO qualifying bars while
        20d/30d still have plenty -- the report must render "insufficient
        data" for just the 10d slot, not crash on `None * 100`, and must
        NOT store a None-valued 10 key in rv_values (downstream .get(10,
        default) callers expect either a real float or a missing key).
        """
        now = time.time()
        prices = [
            {"timestamp": now - days * 86400, "close": 90000 * (1 + 0.01 * math.sin(days * 0.5))}
            for days in range(11, 21)
        ]
        prices.append({"timestamp": now - 25 * 86400, "close": 89000.0})

        report, rv_values = calculator.calculate_realized_volatility_multi_window(
            prices, now_utc=datetime.now(timezone.utc)
        )

        assert 10 not in rv_values
        assert 20 in rv_values
        assert 30 in rv_values
        assert "10d: insufficient data" in report
        assert "20d:" in report and "insufficient data" not in report.split("20d:")[1].split("|")[0]

    def test_realized_volatility_multi_window_is_deterministic_across_timezone_representations(self, calculator):
        """
        Task G2-C regression guard at the MarketWideCalculator layer (the
        actual live pipeline entry point the audit exercised): two now_utc
        values representing the exact same instant, expressed in different
        timezones, must produce identical rv_values -- proving the fix
        holds through this method's own threading of now_utc into
        VRPCalculator, not just inside VRPCalculator directly.
        """
        prices = _make_price_history(60)
        anchor = datetime.now(timezone.utc)
        _, rv_utc = calculator.calculate_realized_volatility_multi_window(prices, now_utc=anchor)
        _, rv_other_tz = calculator.calculate_realized_volatility_multi_window(
            prices, now_utc=anchor.astimezone(timezone(timedelta(hours=-7)))
        )
        assert rv_utc == rv_other_tz

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

    def test_vrp_non_positive_rv_30d_does_not_crash(self, calculator):
        """
        Wave H Task H-D defensive guard: VRPCalculator.calculate_vrp now
        returns None for a non-positive realized_vol instead of a
        self-contradicting NEUTRAL/0.0 dict. Every live caller
        (market_wide_orchestrator.py) already gates rv_30d > 0 before
        reaching this method, so this exercises the direct-call path a
        future/non-orchestrator caller could take -- it must render an
        honest message, not crash on `None["vrp_absolute"]`.
        """
        report, structured = calculator.calculate_vrp(0.0)
        assert "Insufficient data to compute VRP" in report
        assert structured["signal"] == "FAIR"

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

    def test_null_open_interest_falls_back_to_zero_not_none(self, calculator):
        """Independent review round 4 (Important #6): same M1/#5 bug class
        -- `perp_ticker.get("open_interest", 0)` only applies the 0 default
        when the key is ABSENT. `open_interest` is nullable per
        deribit_schemas.py's TICKER schema (the same ticker perp_ticker
        comes from); a present-but-null value made structured["perp_oi"]
        None, which chained through market_wide_orchestrator.py's own
        `funding_data_struct.get("perp_oi", 0.0)` (same trap, but that one
        is a no-op fix target -- the key IS present there too, just with a
        None value it can no longer receive once this fix lands) into
        PerpetualFundingResult.perp_open_interest, then crashed
        market_wide_formatter.py's `f"...{result.perp_open_interest:,.0f}"`
        -- and unlike the block-trades phase (Important #5), there is no
        try/except anywhere in render_market_wide_from_result, so this
        would abort the ENTIRE report render, not just one section."""
        report, structured = calculator.calculate_perpetual_funding_trend(
            {"data": []},
            {"open_interest": None, "current_funding": 0.0001, "funding_8h": 0.0001},
        )
        assert structured["perp_oi"] == 0
        assert "Perp OI: 0 USD" in report

    def test_large_print_detection_excludes_block_legs(self, calculator):
        """The notional-filter ("large prints") list must not include a
        trade that is part of a block, even if its own notional clears the
        threshold -- that trade is already counted in `blocks`."""
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
            {
                # part of a block AND above the notional threshold --
                # must appear in `blocks`, NOT in the large-prints list.
                "instrument_name": "BTC-28MAR26-95000-C",
                "amount": 10.0,
                "price": 0.05,
                "index_price": 90000,
                "direction": "buy",
                "timestamp": int(time.time() * 1000),
                "iv": 65.0,
                "block_trade_id": "BLOCK-1",
                "block_trade_leg_count": 1,
            },
        ]

        report, structured = calculator.detect_block_trades(trades, notional_threshold=100_000)

        assert "LARGE PRINTS" in report
        assert "screen prints" in report.lower()
        # 5.0 * 90000 = 450000 > threshold, no block_trade_id -> large print
        assert "BTC-28MAR26-90000-C" in report
        # 0.5 * 90000 = 45000 < threshold - should NOT appear
        assert "BTC-28MAR26-80000-P" not in report
        assert len(structured["large_prints"]) == 1
        assert structured["large_prints"][0]["instrument"] == "BTC-28MAR26-90000-C"
        # the block leg (95000-C) must NOT show up in large_prints, even
        # though 10.0 * 90000 = 900000 clears the threshold.
        large_print_instruments = [t["instrument"] for t in structured["large_prints"]]
        assert "BTC-28MAR26-95000-C" not in large_print_instruments
        assert len(structured["blocks"]) == 1

    def test_block_trade_no_data(self, calculator):
        report, structured = calculator.detect_block_trades([])
        assert "No recent trade data" in report
        assert structured["large_prints"] == []
        assert structured["blocks"] == []

    def test_t9_1_block_grouping_fixture(self, calculator):
        """institutional_metrics_spec.md T9.1: 3 legs sharing a
        block_trade_id (leg_count=3), 2 unrelated trades with
        block_trade_id=NULL and notional > $100k. Block section lists 1
        block (3 legs, combined premium = sum(price*amount*index_price)
        over the 3 legs); large-prints list shows 2 entries; no
        double-counting."""
        index_price = 90000
        block_legs = [
            {
                "instrument_name": "BTC-31JUL26-63000-C",
                "amount": 12.5,
                "price": 0.0008,
                "index_price": index_price,
                "direction": "buy",
                "timestamp": 1785546525278,
                "iv": 13.35,
                "block_trade_id": "BLOCK-281688",
                "block_trade_leg_count": 3,
                "combo_id": "BTC-STRD-31JUL26-63000",
            },
            {
                "instrument_name": "BTC-31JUL26-63000-P",
                "amount": 12.5,
                "price": 0.0033,
                "index_price": index_price,
                "direction": "buy",
                "timestamp": 1785546525279,
                "iv": 21.21,
                "block_trade_id": "BLOCK-281688",
                "block_trade_leg_count": 3,
                "combo_id": "BTC-STRD-31JUL26-63000",
            },
            {
                "instrument_name": "BTC-31JUL26-64000-C",
                "amount": 12.5,
                "price": 0.0015,
                "index_price": index_price,
                "direction": "sell",
                "timestamp": 1785546525280,
                "iv": 18.0,
                "block_trade_id": "BLOCK-281688",
                "block_trade_leg_count": 3,
                "combo_id": "BTC-STRD-31JUL26-63000",
            },
        ]
        large_prints = [
            {
                "instrument_name": "BTC-28MAR26-90000-C",
                "amount": 5.0,
                "price": 0.05,
                "index_price": index_price,
                "direction": "buy",
                "timestamp": int(time.time() * 1000),
                "iv": 65.0,
            },
            {
                "instrument_name": "BTC-28MAR26-95000-P",
                "amount": 3.0,
                "price": 0.04,
                "index_price": index_price,
                "direction": "sell",
                "timestamp": int(time.time() * 1000),
                "iv": 68.0,
            },
        ]
        trades = block_legs + large_prints

        report, structured = calculator.detect_block_trades(trades, notional_threshold=100_000)

        assert len(structured["blocks"]) == 1
        block = structured["blocks"][0]
        assert block["block_trade_id"] == "BLOCK-281688"
        assert block["leg_count"] == 3
        assert block["observed_leg_count"] == 3
        assert block["combo_id"] == "BTC-STRD-31JUL26-63000"
        expected_premium = sum(
            leg["price"] * leg["amount"] * leg["index_price"] for leg in block_legs
        )
        assert block["combined_premium_usd"] == pytest.approx(expected_premium)

        assert len(structured["large_prints"]) == 2
        large_print_instruments = {t["instrument"] for t in structured["large_prints"]}
        assert large_print_instruments == {"BTC-28MAR26-90000-C", "BTC-28MAR26-95000-P"}
        # no double-counting: none of the block's own instruments leak
        # into the large-prints list.
        block_instruments = {leg["instrument_name"] for leg in block_legs}
        assert not (block_instruments & large_print_instruments)

        assert "BLOCK TRADES" in report
        assert "BLOCK-281688" in report
        assert "LARGE PRINTS" in report

    def test_block_timestamp_rendered_in_utc_not_local(self, calculator):
        """Independent review round 2 (Important #2): the exact banned
        naive-local-datetime bug class (5 fix rounds this campaign).
        ts=1785546525278 is 01:08:45 UTC; on a UTC+2 dev machine the old
        naive datetime.fromtimestamp(ts/1000) rendered 03:08:45 instead --
        a direct in-report inconsistency against every other UTC-labelled
        section, and machine-timezone-dependent golden master output."""
        trades = [
            {
                "instrument_name": "BTC-1AUG26-63000-C",
                "amount": 12.5,
                "price": 0.0008,
                "index_price": 62892.69,
                "direction": "buy",
                "timestamp": 1785546525278,
                "iv": 13.35,
                "block_trade_id": "BLOCK-282155",
                "block_trade_leg_count": 1,
            },
        ]

        report, _ = calculator.detect_block_trades(trades, notional_threshold=100_000)

        assert "01:08:45" in report
        assert "03:08:45" not in report

    def test_no_blocks_in_window_states_start_date_not_no_data(self, calculator):
        """Gate exhaustiveness: trades exist but none share a
        block_trade_id -- must render as an empty block section that
        states the tracked-since date, not a generic "no data" message
        (that message is reserved for the "trades list itself is empty"
        case, already covered by test_block_trade_no_data)."""
        trades = [
            {
                "instrument_name": "BTC-28MAR26-90000-C",
                "amount": 1.0,
                "price": 0.05,
                "index_price": 90000,
                "direction": "buy",
                "timestamp": int(time.time() * 1000),
                "iv": 65.0,
            },
        ]

        report, structured = calculator.detect_block_trades(trades, notional_threshold=100_000)

        assert structured["blocks"] == []
        assert "tracked since" in report.lower()
        from coding.core.analytics.thresholds import BLOCK_TRADE_ID_TRACKED_SINCE
        assert BLOCK_TRADE_ID_TRACKED_SINCE in report

    def test_block_with_single_observed_leg(self, calculator):
        """Edge case: a block_trade_id appears on only one trade in the
        fetched window (e.g. the group's other legs fell outside the
        lookback window, or block_trade_leg_count genuinely is 1). Must
        not crash and must report the leg counts it actually has."""
        trades = [
            {
                "instrument_name": "BTC-28MAR26-90000-C",
                "amount": 1.0,
                "price": 0.05,
                "index_price": 90000,
                "direction": "buy",
                "timestamp": int(time.time() * 1000),
                "iv": 65.0,
                "block_trade_id": "BLOCK-LONE",
                "block_trade_leg_count": 1,
            },
        ]

        report, structured = calculator.detect_block_trades(trades, notional_threshold=100_000)

        assert len(structured["blocks"]) == 1
        block = structured["blocks"][0]
        assert block["leg_count"] == 1
        assert block["observed_leg_count"] == 1
        assert "BLOCK-LONE" in report

    def test_block_with_missing_companion_fields(self, calculator):
        """Edge case: block_trade_leg_count and combo_id are null/missing
        on the legs (companion fields are not guaranteed). Must fall back
        to the observed leg count and render without a combo name, not
        raise."""
        trades = [
            {
                "instrument_name": "BTC-28MAR26-90000-C",
                "amount": 1.0,
                "price": 0.05,
                "index_price": 90000,
                "direction": "buy",
                "timestamp": int(time.time() * 1000),
                "iv": 65.0,
                "block_trade_id": "BLOCK-NOMETA",
                "block_trade_leg_count": None,
                "combo_id": None,
            },
            {
                "instrument_name": "BTC-28MAR26-91000-P",
                "amount": 1.0,
                "price": 0.03,
                "index_price": 90000,
                "direction": "sell",
                "timestamp": int(time.time() * 1000) + 1,
                "iv": 60.0,
                "block_trade_id": "BLOCK-NOMETA",
            },
        ]

        report, structured = calculator.detect_block_trades(trades, notional_threshold=100_000)

        assert len(structured["blocks"]) == 1
        block = structured["blocks"][0]
        assert block["observed_leg_count"] == 2
        assert block["leg_count"] == 2  # falls back to observed count
        assert block["combo_id"] is None
        assert "BLOCK-NOMETA" in report

    def test_block_leg_with_null_index_price_falls_back_to_spot(self, calculator):
        """Independent review round 2 (M1): `leg.get("index_price",
        self.spot_price) or 0` only applies the spot_price default when
        the key is ABSENT -- a leg with the key present but null (a real,
        if rare, API shape) fell through the `or 0` instead, silently
        zeroing that leg's contribution to combined_premium_usd. Fixture
        calculator has spot_price=90000 -- the correct premium uses that,
        not zero."""
        trades = [
            {
                "instrument_name": "BTC-31JUL26-63000-C",
                "amount": 10.0,
                "price": 0.001,
                "index_price": None,  # key present, value null
                "direction": "buy",
                "timestamp": int(time.time() * 1000),
                "iv": 20.0,
                "block_trade_id": "BLOCK-NULLIDX",
                "block_trade_leg_count": 1,
            },
        ]

        _, structured = calculator.detect_block_trades(trades, notional_threshold=100_000)

        assert len(structured["blocks"]) == 1
        block = structured["blocks"][0]
        expected_premium = 0.001 * 10.0 * 90000  # spot_price fallback, not 0
        assert block["combined_premium_usd"] == pytest.approx(expected_premium)

    def test_large_print_leg_with_null_index_price_falls_back_to_spot_not_typeerror(
        self, calculator
    ):
        """Independent review round 3 (Important #5): the M1 bug class
        survives 60+ lines below the M1 fix, in the large-prints loop --
        and here it's worse: `amount * index_price` with index_price=None
        raises TypeError (a hard crash), not a silent wrong value. Same
        one-arg `.get()` default pattern, same fix."""
        trades = [
            {
                "instrument_name": "BTC-28MAR26-90000-C",
                "amount": 5.0,
                "price": 0.05,
                "index_price": None,  # key present, value null
                "direction": "buy",
                "timestamp": int(time.time() * 1000),
                "iv": 65.0,
            },
        ]

        # must not raise TypeError
        report, structured = calculator.detect_block_trades(trades, notional_threshold=100_000)

        assert len(structured["large_prints"]) == 1
        # notional falls back to spot_price=90000, not None/crash:
        # 5.0 * 90000 = 450000
        assert structured["large_prints"][0]["notional"] == pytest.approx(450_000.0)

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
        # midnight) — the integer calculate_dte is now floor(exact fractional
        # days), which floors 30.3333 -> 30, matching the pre-fix value here.
        now = datetime(2026, 2, 26, tzinfo=timezone.utc)
        dte = MarketWideCalculator.calculate_dte("28MAR26", now)
        assert dte == 30

    def test_dte_invalid(self):
        dte = MarketWideCalculator.calculate_dte("INVALID", datetime.now(timezone.utc))
        assert dte is None

    def test_dte_past(self):
        # Past expiration should return 0 (floor(-3.667) = -4, clamped to 0)
        now = datetime(2026, 4, 1, tzinfo=timezone.utc)
        dte = MarketWideCalculator.calculate_dte("28MAR26", now)
        assert dte == 0

    def test_dte_calculation_backward_compat_alias(self):
        """Task G2-C: _calculate_dte (the pre-rename private name) must
        still work as a backward-compatible alias for calculate_dte."""
        now = datetime(2026, 2, 26, tzinfo=timezone.utc)
        assert MarketWideCalculator._calculate_dte("28MAR26", now) == 30


class TestDvolCorrelationLogChanges:
    """
    bugfix_spec.md Item 11: DVOL correlation must be computed on log CHANGES
    of the raw DVOL levels, not the levels themselves (correlating levels of
    two trending, persistent series measures shared trend, not co-movement
    -- a textbook spurious-regression setup). Acceptance tests T11.1-T11.4,
    verbatim from bugfix_spec.md section 11.5.
    """

    def test_perfect_log_linear_comovement_is_zero_variance_guarded(self, calculator):
        """T11.1 - every log change is identical in both series -> zero
        variance -> corrcoef is nan, must be guarded to "Insufficient data"."""
        own = [10.0 * (1.01 ** i) for i in range(31)]
        other = [20.0 * (1.01 ** i) for i in range(31)]

        report, structured = calculator.calculate_cross_asset_correlation(
            own_prices=[], other_prices=[], own_dvol_history=own,
            other_dvol_history=other, other_currency="ETH",
        )

        assert structured["btc_eth_dvol_corr"] is None
        assert "Insufficient data" in report

    def test_anti_correlated_changes_give_negative_one(self, calculator):
        """T11.2 - hand-computed: alternating +10%/-10% log changes in
        opposite phase -> correlation exactly -1.0."""
        own = [10.0, 11.0, 10.0, 11.0, 10.0, 11.0, 10.0, 11.0, 10.0, 11.0, 10.0, 11.0]
        other = [20.0, 18.0, 20.0, 18.0, 20.0, 18.0, 20.0, 18.0, 20.0, 18.0, 20.0, 18.0]

        report, structured = calculator.calculate_cross_asset_correlation(
            own_prices=[], other_prices=[], own_dvol_history=own,
            other_dvol_history=other, other_currency="ETH",
        )

        assert structured["btc_eth_dvol_corr"] == pytest.approx(-1.0, abs=1e-9)
        assert "log changes, 11d" in report

    def test_levels_correlation_no_longer_produced_regression_guard(self, calculator):
        """T11.3 - two series that trend together (shared upward drift) but
        whose CHANGES are independent noise: levels correlation is ~0.999
        (would fool the OLD levels-based code); the FIXED log-changes code
        must report something close to zero, not a spurious near-1.0.
        Noise generated via numpy.random.default_rng(42), hardcoded here for
        a deterministic test."""
        noise_a = [
            0.0914151239263294, -0.31199523187214867, 0.22513535874193716, 0.28216941491736414,
            -0.5853105565961509, -0.3906538520586954, 0.03835212095018561, -0.09487277770307466,
            -0.005040347251286639, -0.255913178272074, 0.26381939245884856, 0.23333758062868448,
            0.019809209268364814, 0.33817236209040985, 0.14025280267561369, -0.25778773886497147,
            0.11062523522474965, -0.2876647802486997, 0.26353509039218176, -0.014977773295875869,
            -0.055458709063578165, -0.20427886332118242, 0.3667624016022091, -0.046358844620640646,
            -0.12849834664893217, -0.10564006514646887, 0.1596927556660046, 0.1096332193092235,
            0.12381978347879652, 0.1292463009023648, 0.6424942802611383,
        ]
        noise_b = [
            -0.12192450491538467, -0.15367281872146119, -0.2441318184743633, 0.1847938267726487,
            0.33869168781626746, -0.03418423729646252, -0.2520469430887584, -0.24734436470737187,
            0.19517783634741032, 0.2229762513610327, 0.1629462804915585, -0.19965291218660827,
            0.06964839692001593, 0.03500574274221847, 0.06560657901870388, 0.26142863338445693,
            0.06707866463240468, 0.20367406892156845, 0.020273720846667436, 0.08673581960699524,
            0.18938646775156212, -0.43714674595669994, -0.0959013649071904, -0.14111179628783865,
            -0.19166335447300256, -0.08254267536800512, 0.44848239337031875, -0.25974933470797296,
            0.29048350637744424, -0.5048609314847414, -0.10046550899573245,
        ]
        own = [100.0 + i + noise_a[i] for i in range(31)]
        other = [200.0 + i + noise_b[i] for i in range(31)]

        levels_corr = pytest.approx(0.999, abs=0.01)
        import numpy as np
        assert np.corrcoef(own, other)[0, 1] == levels_corr  # would fool the OLD code

        report, structured = calculator.calculate_cross_asset_correlation(
            own_prices=[], other_prices=[], own_dvol_history=own,
            other_dvol_history=other, other_currency="ETH",
        )

        assert abs(structured["btc_eth_dvol_corr"]) < 0.5  # old code returned > 0.98

    def test_insufficient_data(self, calculator):
        """T11.4 - fewer than 11 aligned changes -> Insufficient data, None."""
        report, structured = calculator.calculate_cross_asset_correlation(
            own_prices=[], other_prices=[], own_dvol_history=[10.0] * 5,
            other_dvol_history=[20.0] * 5, other_currency="ETH",
        )

        assert structured["btc_eth_dvol_corr"] is None
        assert "Insufficient data" in report

    def test_mismatched_history_lengths_logs_warning_and_aligns_from_right(self, calculator, caplog):
        """Edge case (bugfix_spec.md section 11.4): mismatched lengths must
        log a warning and align from the most recent point on each side,
        not silently misalign the two series."""
        own = [10.0 * (1.01 ** i) * (1 + 0.02 * math.sin(i)) for i in range(31)]
        other = [20.0 * (1.01 ** i) * (1 + 0.02 * math.cos(i)) for i in range(20)]

        with caplog.at_level(logging.WARNING):
            report, structured = calculator.calculate_cross_asset_correlation(
                own_prices=[], other_prices=[], own_dvol_history=own,
                other_dvol_history=other, other_currency="ETH",
            )

        assert any("DVOL history length mismatch" in rec.message for rec in caplog.records)
        assert structured["btc_eth_dvol_corr"] is not None

    def test_non_positive_value_on_one_side_drops_step_from_both_not_just_one(self, calculator):
        """
        bugfix_spec.md section 11.4 edge case 1 (task A7 review fix): a
        non-positive DVOL reading on ONE side must drop that step from
        BOTH change series, not just the corrupted one -- otherwise the
        two change lists desynchronize by index and the correlation
        silently pairs up changes from different dates.

        Fixture: ``other[i] = other[i-1] * (own_true[i]/own_true[i-1])**2``
        for every step, i.e. other's log return is exactly double own's at
        every date -- a perfect (nonlinear but monotonic-in-rank) log-
        linear relationship, corr == 1.0 when the two series are paired by
        date. own[5] is corrupted to 0.0 (a bad reading), which must drop
        exactly the two steps touching index 5 (i=5 and i=6) from BOTH
        series before correlating.

        Hand-verified (see task A7 fix-report): the CORRECT jointly-aligned
        computation gives corr == 1.0 over the remaining 13 aligned points.
        The bug this test guards against -- computing each series' log
        changes independently, then slicing both to min(len) elements from
        the right -- pairs mismatched dates and gives corr ~= 0.765 for
        this exact fixture (own's change list is short two entries from the
        MIDDLE, not the end, so a tail-aligned slice does not restore
        alignment).
        """
        own = [
            10.0, 10.5, 10.8, 11.5, 11.9, 0.0, 12.6, 13.3,
            13.6, 14.1, 14.6, 14.9, 15.5, 15.8, 16.4, 16.9,
        ]
        other = [
            20.0, 22.05, 23.328, 26.45, 28.322, 30.752, 31.752, 35.378,
            36.992, 39.762, 42.632, 44.402, 48.05, 49.928, 53.792, 57.122,
        ]

        report, structured = calculator.calculate_cross_asset_correlation(
            own_prices=[], other_prices=[], own_dvol_history=own,
            other_dvol_history=other, other_currency="ETH",
        )

        assert structured["btc_eth_dvol_corr"] == pytest.approx(1.0, abs=1e-6)
        assert structured["btc_eth_dvol_corr_n"] == 13
        assert "log changes, 13d" in report


class TestExactFractionalDaysToExpiry:
    """
    bugfix_spec.md Item 5 (F5.3.1): exact fractional DTE to 08:00 UTC
    settlement, replacing the naive-datetime/local-midnight/integer-truncation
    triple bug. Hand-computed numbers verbatim from section 5.5, T5.1.
    """

    def test_exact_fractional_dte_at_settlement_hour(self):
        now = datetime(2026, 7, 25, 8, 0, 0, tzinfo=timezone.utc)
        assert MarketWideCalculator.calculate_days_to_expiry("27JUL26", now) == pytest.approx(2.0)
        assert MarketWideCalculator.calculate_days_to_expiry("26JUL26", now) == pytest.approx(1.0)
        assert MarketWideCalculator.calculate_days_to_expiry("25JUL26", now) == pytest.approx(0.0)

    def test_exact_fractional_dte_mid_day(self):
        now2 = datetime(2026, 7, 25, 20, 0, 0, tzinfo=timezone.utc)
        assert MarketWideCalculator.calculate_days_to_expiry("26JUL26", now2) == pytest.approx(0.5)

    def test_invalid_expiration_returns_none(self):
        now = datetime(2026, 7, 25, 8, 0, 0, tzinfo=timezone.utc)
        assert MarketWideCalculator.calculate_days_to_expiry("INVALID", now) is None

    def test_calculate_days_to_expiry_backward_compat_alias(self):
        """Task G2-C: _calculate_days_to_expiry (the pre-rename private
        name) must still work as a backward-compatible alias."""
        now = datetime(2026, 7, 25, 8, 0, 0, tzinfo=timezone.utc)
        assert MarketWideCalculator._calculate_days_to_expiry("27JUL26", now) == pytest.approx(2.0)


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
        assert MarketWideCalculator.calculate_days_to_expiry("25DEC26", now_utc) == pytest.approx(152.5992, abs=1e-4)

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

    def test_null_index_price_falls_back_to_spot_not_typeerror(self, calculator):
        """Independent review round 3 (Important #5): the same M1 bug
        class survives here -- `future.get("index_price", self.spot_price)`
        only applies the spot_price default when the key is ABSENT. A
        future dict with the key present but null (the real API shape M1's
        own fix's premise asserts exists) previously made `spot` None,
        then `if spot <= 0` raised TypeError -- a hard crash, not a silent
        wrong value. Fixture calculator has spot_price=90000."""
        result = calculator.calculate_futures_basis(
            [{"instrument_name": "BTC-27JUL26", "mark_price": 100_200.0, "index_price": None}],
            now_utc=datetime(2026, 7, 25, 8, 0, tzinfo=timezone.utc),
        )
        assert len(result.entries) == 1
        # spot falls back to self.spot_price=90000, matching the calculator
        # fixture -- not a crash, and not left as None/0.
        assert result.entries[0].index_price == 90000
        basis_pct = ((100_200.0 - 90000) / 90000) * 100.0
        expected_annualized = basis_pct * (365.0 / 2)
        assert result.to_dict()["futures_basis"]["27JUL26"] == pytest.approx(
            expected_annualized, rel=1e-6
        )
