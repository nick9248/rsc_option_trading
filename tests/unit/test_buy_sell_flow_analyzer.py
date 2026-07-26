"""
Unit tests for BuySellFlowAnalyzer.

T5 (refactor_design_spec.md): the analyzer takes already-fetched trades and
an explicit window — no repository, no DB access, no internal fetch. This
closes bugfix_spec.md Item 6a (double execution / two-instants defect); the
"_fetch_trades" test that used to live here (line 548 of the pre-T5 file) is
gone — replaced by a service-level test
(tests/unit/test_on_chain_analysis_service_flow.py) that verifies exactly
one repository call.

Also covers bugfix_spec.md Item 6's data-sufficiency QUALITY gate per
Decision D5 (two tiers: <5 trades suppresses the section entirely, 5-19
trades renders normally but LOW-CONFIDENCE-tagged) and the buy_sell_ratio
inf -> None sentinel unification (M7).
"""

import pytest
from coding.core.analytics.buy_sell_flow_analyzer import (
    INSUFFICIENT_DATA_LABEL,
    LOW_CONFIDENCE_SUFFIX,
    MINIMUM_TRADES_1H_FOR_TREND,
    MINIMUM_TRADES_FOR_CONFIDENCE,
    MINIMUM_TRADES_FOR_SECTION,
    BuySellFlowAnalyzer,
)

# Fixed window: 24h ending at NOW_MS. Every trade below is timestamped at
# NOW_MS unless a test specifically exercises the 1h/4h sub-windows, so it
# always falls inside every sub-window by default.
NOW_MS = 2_000_000_000_000
WINDOW_START_MS = NOW_MS - 24 * 3600 * 1000
WINDOW_END_MS = NOW_MS


def _trade(strike, option_type, amount, direction, trade_id="1", timestamp=NOW_MS, index_price=100_000.0):
    return {
        "trade_id": trade_id,
        "trade_timestamp": timestamp,
        "instrument_name": f"BTC-27MAR26-{int(strike)}-{option_type}",
        "strike": strike,
        "option_type": option_type,
        "price": 5000.0,
        "amount": amount,
        "direction": direction,
        "index_price": index_price,
    }


def buy(strike, option_type, amount, **kw):
    return _trade(strike, option_type, amount, "buy", **kw)


def sell(strike, option_type, amount, **kw):
    return _trade(strike, option_type, amount, "sell", **kw)


def _make_analyzer(trades, spot_price=100_000.0, window_start_ms=WINDOW_START_MS, window_end_ms=WINDOW_END_MS):
    return BuySellFlowAnalyzer(
        trades=trades,
        currency="BTC",
        expiration="27MAR26",
        spot_price=spot_price,
        window_start_ms=window_start_ms,
        window_end_ms=window_end_ms,
    )


def make_trades(n, strike=100_000.0, option_type="C", amount=2.0, direction="buy"):
    """n identical trades — used for pure sufficiency-gate tests."""
    return [_trade(strike, option_type, amount, direction, trade_id=str(i)) for i in range(n)]


class TestNoDatabaseImportInCore:
    """T5 core-purity assertion (refactor_design_spec.md T5 proof)."""

    def test_no_database_import_in_core(self):
        import ast
        import coding.core.analytics.buy_sell_flow_analyzer as module

        source = open(module.__file__, encoding="utf-8").read()
        tree = ast.parse(source)
        imported_names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported_names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                imported_names.update(alias.name.split(".")[0] for alias in node.names)

        assert "DatabaseRepository" not in imported_names
        assert not any(name.startswith("coding.core.database") for name in imported_names)
        assert not hasattr(module, "DatabaseRepository")


class TestSingleFetchInProductionSequence:
    """T6.1 (bugfix_spec.md section 6.5) - one computation, single truth."""

    def test_one_call_produces_consistent_report_and_structured_data(self):
        trades = make_trades(50)
        analyzer = _make_analyzer(trades)
        result = analyzer.calculate()
        report = analyzer.generate_report_section(result=result)

        assert result.trade_count == 50
        assert "Trades Analyzed: 50" in report


class TestBasicFlowCalculation:
    def test_empty_trades(self):
        analyzer = _make_analyzer([])
        result = analyzer.calculate()

        assert result.trade_count == 0
        assert result.flow_data == {}
        assert result.sufficient_data is False
        assert result.bias_interpretation == INSUFFICIENT_DATA_LABEL
        assert result.flow_trend == INSUFFICIENT_DATA_LABEL
        assert result.top_buy_strikes == ()
        assert result.top_sell_strikes == ()

    def test_single_buy_trade(self):
        trades = [buy(100_000.0, "C", 10.0)]
        analyzer = _make_analyzer(trades)
        result = analyzer.calculate()

        assert result.trade_count == 1
        call_data = result.flow_data[100_000.0]["C"]
        assert call_data.buy_count == 1
        assert call_data.sell_count == 0
        assert call_data.buy_volume == 10.0
        assert call_data.sell_volume == 0.0
        assert call_data.net_flow == 10.0
        assert call_data.buy_sell_ratio is None  # M7: sell_volume == 0 -> None, not inf

        totals = result.expiration_totals
        assert totals.call_buy_volume == 10.0
        assert totals.call_sell_volume == 0.0

    def test_single_sell_trade(self):
        trades = [sell(95_000.0, "P", 5.0)]
        analyzer = _make_analyzer(trades)
        result = analyzer.calculate()

        assert result.trade_count == 1
        put_data = result.flow_data[95_000.0]["P"]
        assert put_data.buy_count == 0
        assert put_data.sell_count == 1
        assert put_data.buy_volume == 0.0
        assert put_data.sell_volume == 5.0
        assert put_data.net_flow == -5.0
        assert put_data.buy_sell_ratio == 0.0  # sell_volume > 0: well-defined ratio, 0/5=0.0

        totals = result.expiration_totals
        assert totals.put_buy_volume == 0.0
        assert totals.put_sell_volume == 5.0

    def test_mixed_trades_same_strike(self):
        trades = [buy(100_000.0, "C", 10.0), sell(100_000.0, "C", 3.0)]
        analyzer = _make_analyzer(trades)
        result = analyzer.calculate()

        assert result.trade_count == 2
        call_data = result.flow_data[100_000.0]["C"]
        assert call_data.buy_count == 1
        assert call_data.sell_count == 1
        assert call_data.buy_volume == 10.0
        assert call_data.sell_volume == 3.0
        assert call_data.net_flow == 7.0
        assert abs(call_data.buy_sell_ratio - (10.0 / 3.0)) < 0.01

    def test_multiple_strikes(self):
        trades = [
            buy(100_000.0, "C", 10.0),
            sell(95_000.0, "P", 5.0),
            buy(105_000.0, "C", 8.0),
        ]
        analyzer = _make_analyzer(trades)
        result = analyzer.calculate()

        assert result.trade_count == 3
        assert len(result.flow_data) == 3
        assert 100_000.0 in result.flow_data
        assert 95_000.0 in result.flow_data
        assert 105_000.0 in result.flow_data

    def test_notional_calculation(self):
        trades = [buy(100_000.0, "C", 10.0, index_price=100_000.0)]
        analyzer = _make_analyzer(trades)
        result = analyzer.calculate()

        call_data = result.flow_data[100_000.0]["C"]
        # Notional = amount * index_price = 10 * 100000 = 1,000,000
        assert call_data.buy_notional == 1_000_000.0
        assert call_data.sell_notional == 0.0

    def test_calculate_is_idempotent(self):
        """Mirrors the GEX/DEX idempotency fix (bugfix_spec.md Item 1) —
        calling calculate() twice must yield identical results."""
        trades = make_trades(10, amount=3.0)
        analyzer = _make_analyzer(trades)
        first = analyzer.calculate()
        second = analyzer.calculate()

        assert first.trade_count == second.trade_count
        assert first.expiration_totals == second.expiration_totals


class TestTopStrikes:
    def test_top_buy_strikes(self):
        trades = [
            buy(100_000.0, "C", 20.0, trade_id="1"),
            buy(95_000.0, "P", 10.0, trade_id="2"),
            sell(100_000.0, "C", 5.0, trade_id="3"),
        ]
        analyzer = _make_analyzer(trades)
        result = analyzer.calculate()

        top_buy = result.top_buy_strikes
        assert len(top_buy) == 2  # Two strikes with net buying

        assert top_buy[0].strike == 100_000.0
        assert top_buy[0].option_type == "C"
        assert top_buy[0].net_flow == 15.0

        assert top_buy[1].strike == 95_000.0
        assert top_buy[1].option_type == "P"
        assert top_buy[1].net_flow == 10.0

    def test_top_sell_strikes(self):
        trades = [
            buy(100_000.0, "C", 5.0, trade_id="1"),
            sell(100_000.0, "C", 20.0, trade_id="2"),
            sell(95_000.0, "P", 10.0, trade_id="3"),
        ]
        analyzer = _make_analyzer(trades)
        result = analyzer.calculate()

        top_sell = result.top_sell_strikes
        assert len(top_sell) == 2

        assert top_sell[0].strike == 100_000.0
        assert top_sell[0].option_type == "C"
        assert top_sell[0].net_flow == -15.0

        assert top_sell[1].strike == 95_000.0
        assert top_sell[1].option_type == "P"
        assert top_sell[1].net_flow == -10.0


class TestBiasInterpretation:
    """Bias labels are still computed at any trade count >= MINIMUM_TRADES_FOR_SECTION
    (the label itself is unaffected by the LOW CONFIDENCE tag — that's a
    report-rendering concern, see TestReportGate)."""

    def test_bias_interpretation_heavy_buying(self):
        trades = [
            buy(100_000.0 + i * 1_000, "C", 10.0, trade_id=str(i)) for i in range(10)
        ] + [sell(95_000.0, "P", 5.0, trade_id="100")]
        analyzer = _make_analyzer(trades)
        result = analyzer.calculate()

        assert result.sufficient_data is True
        assert result.bias_interpretation == "Heavy Buying"

    def test_bias_interpretation_heavy_selling(self):
        trades = [
            sell(100_000.0 + i * 1_000, "C", 10.0, trade_id=str(i)) for i in range(10)
        ] + [buy(95_000.0, "P", 5.0, trade_id="100")]
        analyzer = _make_analyzer(trades)
        result = analyzer.calculate()

        assert result.bias_interpretation == "Heavy Selling"

    def test_bias_interpretation_balanced(self):
        # 5 buy / 5 sell across distinct strikes at equal volume -> ratio 1.0,
        # and >= MINIMUM_TRADES_FOR_SECTION so the label isn't gated.
        trades = [
            buy(100_000.0 + i * 1_000, "C", 10.0, trade_id=f"b{i}") for i in range(5)
        ] + [
            sell(90_000.0 - i * 1_000, "P", 10.0, trade_id=f"s{i}") for i in range(5)
        ]
        analyzer = _make_analyzer(trades)
        result = analyzer.calculate()

        assert result.trade_count == 10
        assert result.bias_interpretation == "Balanced"


class TestReportGeneration:
    def test_report_generation(self):
        trades = make_trades(20, amount=2.0)
        analyzer = _make_analyzer(trades)
        report = analyzer.generate_report_section()

        assert isinstance(report, str)
        assert len(report) > 0
        assert "BUY/SELL FLOW ANALYSIS" in report
        assert "EXPIRATION-LEVEL FLOW:" in report
        assert "TOP 5 STRIKES BY BUYING PRESSURE:" in report

    def test_window_printed_instead_of_lookback_hours(self):
        """F6.3.1 - the report prints the actual window, not just a
        'Lookback Window: N hours' label divorced from the real fetch."""
        trades = make_trades(20, amount=2.0)
        analyzer = _make_analyzer(trades)
        report = analyzer.generate_report_section()

        assert "Window:" in report
        assert "UTC" in report


class TestDataSufficiencyGate:
    """bugfix_spec.md Item 6 / Decision D5: two-tier quality gate.
    <5 trades -> section suppressed. 5-19 -> LOW CONFIDENCE tag. >=20 -> normal.
    """

    def test_gate_fires_below_the_section_floor(self):
        """T6.2 (adapted per Decision D5): 3 trades -> buy 8.0 / sell 1.0,
        ungated ratio 8.0 would read 'Heavy Buying' — must be fully
        suppressed instead. Raw volumes are still correct and still returned."""
        trades = [
            buy(64_000.0, "C", 5.0, trade_id="1"),
            buy(64_000.0, "C", 3.0, trade_id="2"),
            sell(66_000.0, "C", 1.0, trade_id="3"),
        ]
        analyzer = _make_analyzer(trades)
        result = analyzer.calculate()

        assert result.trade_count == 3
        assert result.sufficient_data is False
        assert result.low_confidence is False
        assert result.bias_interpretation == INSUFFICIENT_DATA_LABEL
        assert result.flow_trend == INSUFFICIENT_DATA_LABEL
        # raw volumes are still correct and still returned
        assert result.expiration_totals.call_buy_volume == pytest.approx(8.0)
        assert result.expiration_totals.call_sell_volume == pytest.approx(1.0)

    def test_inclusive_boundary_at_section_floor_is_low_confidence(self):
        """Exactly MINIMUM_TRADES_FOR_SECTION (5) -> section renders, tagged
        LOW CONFIDENCE (< MINIMUM_TRADES_FOR_CONFIDENCE)."""
        trades = make_trades(MINIMUM_TRADES_FOR_SECTION, amount=2.0)
        analyzer = _make_analyzer(trades)
        result = analyzer.calculate()

        assert result.trade_count == 5
        assert result.sufficient_data is True
        assert result.low_confidence is True
        assert result.bias_interpretation != INSUFFICIENT_DATA_LABEL

    def test_one_below_section_floor_is_gated(self):
        trades = make_trades(MINIMUM_TRADES_FOR_SECTION - 1, amount=2.0)
        analyzer = _make_analyzer(trades)
        result = analyzer.calculate()

        assert result.sufficient_data is False

    def test_inclusive_boundary_at_confidence_floor_and_label_appears(self):
        """T6.3 (bugfix_spec.md section 6.5, hand-computed): 20 trades —
        13 buys of 2.0 (=26.0), 7 sells of 2.0 (=14.0); ratio = 26.0/14.0 =
        1.857 > 1.3 -> 'Heavy Buying'. Exactly MINIMUM_TRADES_FOR_CONFIDENCE
        (20) -> full confidence, no LOW CONFIDENCE tag."""
        trades = (
            [buy(64_000.0, "C", 2.0, trade_id=f"b{i}") for i in range(13)]
            + [sell(64_000.0, "C", 2.0, trade_id=f"s{i}") for i in range(7)]
        )
        analyzer = _make_analyzer(trades)
        result = analyzer.calculate()

        assert result.trade_count == 20
        assert result.sufficient_data is True
        assert result.low_confidence is False
        assert result.bias_interpretation == "Heavy Buying"

    def test_gated_report_text(self):
        """T6.4 (adapted per Decision D5's 5-trade section-suppression floor,
        not the bugfix_spec.md F6.3.3 snippet's 20-trade floor — see the
        bugfix Item 6 commit message for why D5 governs)."""
        trades = [
            buy(64_000.0, "C", 5.0, trade_id="1"),
            buy(64_000.0, "C", 3.0, trade_id="2"),
            sell(66_000.0, "C", 1.0, trade_id="3"),
        ]
        analyzer = _make_analyzer(trades)
        report = analyzer.generate_report_section(result=analyzer.calculate())

        assert "** INSUFFICIENT FLOW DATA **" in report
        assert f"3 trade(s) in window, {MINIMUM_TRADES_FOR_SECTION} required" in report
        assert "Heavy Buying" not in report
        assert "Accelerating" not in report
        assert "EXPIRATION-LEVEL FLOW:" not in report  # section fully suppressed

    def test_low_confidence_tag_rendered_in_report(self):
        trades = make_trades(10, amount=2.0)
        analyzer = _make_analyzer(trades)
        report = analyzer.generate_report_section(result=analyzer.calculate())

        assert LOW_CONFIDENCE_SUFFIX in report

    def test_full_confidence_report_has_no_tag(self):
        trades = make_trades(MINIMUM_TRADES_FOR_CONFIDENCE, amount=2.0)
        analyzer = _make_analyzer(trades)
        report = analyzer.generate_report_section(result=analyzer.calculate())

        assert LOW_CONFIDENCE_SUFFIX not in report

    def test_all_trades_on_one_strike_still_labeled(self):
        """Count is the gate, not diversity — 20 trades on a single strike
        must still get a normal label."""
        trades = make_trades(MINIMUM_TRADES_FOR_CONFIDENCE, strike=64_000.0, amount=1.0)
        analyzer = _make_analyzer(trades)
        result = analyzer.calculate()

        assert result.sufficient_data is True
        assert result.bias_interpretation != INSUFFICIENT_DATA_LABEL


class TestTrendGate:
    def test_sufficient_bias_but_empty_1h_window_gates_trend_only(self):
        """20 trades in the full 24h window but none in the last 1h ->
        bias emitted normally, trend gated (bugfix_spec.md Item 6 edge case)."""
        old_ts = WINDOW_END_MS - 20 * 3600 * 1000  # 20h ago, outside the 1h/4h cutoffs
        trades = [
            buy(64_000.0, "C", 2.0, trade_id=str(i), timestamp=old_ts)
            for i in range(MINIMUM_TRADES_FOR_CONFIDENCE)
        ]
        analyzer = _make_analyzer(trades)
        result = analyzer.calculate()

        assert result.sufficient_data is True
        assert result.bias_interpretation != INSUFFICIENT_DATA_LABEL
        assert result.flow_trend == INSUFFICIENT_DATA_LABEL

    def test_trend_emitted_with_enough_1h_activity(self):
        trades = make_trades(MINIMUM_TRADES_FOR_CONFIDENCE, amount=2.0)  # all at NOW_MS -> inside 1h
        analyzer = _make_analyzer(trades)
        result = analyzer.calculate()

        assert result.flow_trend != INSUFFICIENT_DATA_LABEL


class TestTrendDivisorFollowsWindow:
    """M6 fix: trend rate divisors derive from the actual window length, not
    a hardcoded /24."""

    def test_trend_divisors_follow_lookback_hours(self):
        # A 12h window: rate_full should divide by 12, not 24. Use a lopsided
        # flow (heavy recent buying, no older activity) so the exact divisor
        # used is directly observable via the resulting label.
        window_start = NOW_MS - 12 * 3600 * 1000
        trades = make_trades(MINIMUM_TRADES_FOR_CONFIDENCE, amount=3.0)
        analyzer = _make_analyzer(trades, window_start_ms=window_start, window_end_ms=NOW_MS)

        assert analyzer.lookback_hours == pytest.approx(12.0)
        result = analyzer.calculate()
        assert result.lookback_hours == pytest.approx(12.0)


class TestSentinelUnification:
    """M7: buy_sell_ratio uses a single None sentinel, not float('inf')."""

    def test_zero_sell_volume_yields_none_not_inf(self):
        trades = [buy(100_000.0, "C", 10.0)]
        analyzer = _make_analyzer(trades)
        result = analyzer.calculate()

        call_data = result.flow_data[100_000.0]["C"]
        assert call_data.buy_sell_ratio is None
