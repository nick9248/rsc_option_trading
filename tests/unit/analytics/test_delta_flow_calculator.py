"""
Tests for DeltaFlowCalculator (institutional_metrics_spec.md section 6 /
task C7 -- signed delta-weighted, premium-weighted taker flow, the HIRO
analog).

Exhaustive degenerate-data enumeration (task-C7-brief.md's gate-
exhaustiveness requirement, carried over from C3's 3-review-round lesson on
this same table): every way a trade's data could be empty/degenerate/
missing is covered by its own test below, not discovered by a later review
round.

    1.  iv is None                                    -> skip
    2.  iv <= 0 (e.g. 0)                               -> skip
    3.  strike is None                                 -> skip
    4.  option_type not in ("C", "P")                  -> skip
    5.  direction not in ("buy", "sell")                -> skip
    6.  amount is None or <= 0                          -> skip
    7.  index_price is None or <= 0                     -> skip
    8.  expiration is None/"" (falsy)                   -> skip
    9.  instrument_name missing or unparseable          -> skip
    10. trade_timestamp missing                         -> skip
    11. tau <= 0 (trade at/after 08:00 UTC expiry)      -> NOT a skip;
        intrinsic delta via BlackScholesCalculator._expired_option_greeks
    12. Empty trades list                               -> compute_hourly_
        buckets returns {} (no fabricated "ALL" entry -- the caller, not
        this pure calculator, decides whether to synthesize a zero-row)
    13. Every trade in the window fails enrichment (e.g. all bad iv)
        -> a bucket IS still created (skipped_count == N, trade_count == 0,
        every monetary aggregate == 0.0 -- never a fabricated nonzero
        number for zero enriched trades)
    14. skip_rate on a bucket with trade_count == skipped_count == 0
        -> None, never 0.0 (0.0 would misreport "checked, nothing wrong"
        for a bucket that was never populated at all)
"""

from datetime import datetime, timezone

import pytest

from coding.core.analytics.black_scholes_calculator import BlackScholesCalculator
from coding.core.analytics.delta_flow_calculator import DeltaFlowCalculator
from coding.core.analytics.results.delta_flow_results import EnrichedTrade, FlowBucket

_FAR_FUTURE_EXPIRY_STR = "27MAR27"
_FAR_FUTURE_EXPIRY_DT = datetime(2027, 3, 27, 8, 0, 0)
_SPOT = 64_000.0
_STRIKE = 64_000.0
_IV_PCT = 80.0
# Comfortably inside the far-future expiry so tau > 0 for every "valid" fixture trade.
_TRADE_TS_MS = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp() * 1000)


def _valid_trade(**overrides):
    trade = {
        "trade_id": "T1",
        "trade_timestamp": _TRADE_TS_MS,
        "instrument_name": f"BTC-{_FAR_FUTURE_EXPIRY_STR}-{int(_STRIKE)}-C",
        "expiration": _FAR_FUTURE_EXPIRY_STR,
        "strike": _STRIKE,
        "option_type": "C",
        "direction": "buy",
        "amount": 10.0,
        "price": 0.05,
        "index_price": _SPOT,
        "iv": _IV_PCT,
    }
    trade.update(overrides)
    return trade


def _expected_delta(option_type: str = "call") -> float:
    """Reference delta computed directly via BlackScholesCalculator, independent
    of DeltaFlowCalculator's own wiring -- the trusted primitive this test
    checks DeltaFlowCalculator against."""
    bs = BlackScholesCalculator()
    trade_time_utc = datetime.fromtimestamp(_TRADE_TS_MS / 1000.0, tz=timezone.utc).replace(tzinfo=None)
    tau = bs.calculate_time_to_expiry(trade_time_utc, _FAR_FUTURE_EXPIRY_DT)
    greeks = bs.calculate_greeks(
        spot_price=_SPOT, strike_price=_STRIKE, time_to_expiry=tau,
        implied_volatility=_IV_PCT / 100.0, option_type=option_type,
    )
    return greeks["delta"]


class TestEnrichTradeValidPath:
    def test_enrich_valid_buy_call_matches_black_scholes_delta(self):
        calc = DeltaFlowCalculator()
        enriched = calc.enrich_trade(_valid_trade())

        assert enriched is not None
        assert enriched.delta == pytest.approx(_expected_delta("call"), abs=1e-9)
        assert enriched.expiration == _FAR_FUTURE_EXPIRY_STR
        assert enriched.direction == "buy"
        assert enriched.amount == 10.0

    def test_enrich_valid_sell_put_matches_black_scholes_delta(self):
        calc = DeltaFlowCalculator()
        trade = _valid_trade(
            option_type="P", direction="sell",
            instrument_name=f"BTC-{_FAR_FUTURE_EXPIRY_STR}-{int(_STRIKE)}-P",
        )
        enriched = calc.enrich_trade(trade)

        assert enriched is not None
        assert enriched.delta == pytest.approx(_expected_delta("put"), abs=1e-9)
        assert enriched.delta < 0  # puts have negative delta

    def test_signed_hiro_usd_matches_formula(self):
        """hiro_usd = taker_sign * delta * amount * index_price, computed
        directly from BS delta -- not the T6.1 hand-picked-delta table
        (that is tested separately below), but the real recompute path."""
        calc = DeltaFlowCalculator()
        trade = _valid_trade()
        enriched = calc.enrich_trade(trade)

        expected = (+1.0) * enriched.delta * trade["amount"] * trade["index_price"]
        assert enriched.hiro_usd == pytest.approx(expected)

    def test_gross_delta_usd_is_unsigned(self):
        calc = DeltaFlowCalculator()
        trade = _valid_trade(option_type="P", direction="sell",
                              instrument_name=f"BTC-{_FAR_FUTURE_EXPIRY_STR}-{int(_STRIKE)}-P")
        enriched = calc.enrich_trade(trade)

        assert enriched.gross_delta_usd == pytest.approx(
            abs(enriched.delta) * trade["amount"] * trade["index_price"]
        )
        assert enriched.gross_delta_usd > 0


class TestEnrichTradeDegenerateCases:
    """Enumeration items 1-10 above -- each an independent skip path."""

    def test_iv_none_is_skipped(self):
        calc = DeltaFlowCalculator()
        assert calc.enrich_trade(_valid_trade(iv=None)) is None

    def test_iv_zero_is_skipped(self):
        calc = DeltaFlowCalculator()
        assert calc.enrich_trade(_valid_trade(iv=0)) is None

    def test_iv_negative_is_skipped(self):
        calc = DeltaFlowCalculator()
        assert calc.enrich_trade(_valid_trade(iv=-5)) is None

    def test_iv_invalid_never_falls_through_to_black_scholes_default(self):
        """BlackScholesCalculator.calculate_greeks defaults an invalid IV to
        0.01 internally (a live-chain-greeks convenience) -- enrich_trade
        must gate BEFORE calling it, or a missing-IV trade would silently
        produce a normal-looking (wrong) delta instead of being skipped."""
        calc = DeltaFlowCalculator()
        result = calc.enrich_trade(_valid_trade(iv=None))
        assert result is None  # not an EnrichedTrade with some fallback delta

    def test_strike_none_is_skipped(self):
        calc = DeltaFlowCalculator()
        assert calc.enrich_trade(_valid_trade(strike=None)) is None

    @pytest.mark.parametrize("bad_strike", [0, -1, -100.0])
    def test_strike_zero_or_negative_is_skipped(self, bad_strike):
        """Review fix, Important #5: strike <= 0 is a second, previously-
        unguarded door into the same 'silent bad-input -> plausible-looking
        clean zero' failure class as invalid iv. BlackScholesCalculator.
        calculate_greeks has a bare except that catches the resulting
        math.log(spot/strike) failure and returns all-zero greeks instead
        of raising -- without this explicit gate, enrich_trade would
        return an EnrichedTrade with delta=0.0/hiro_usd=0.0 (indistinguishable
        from a real zero-delta trade) instead of skipping."""
        calc = DeltaFlowCalculator()
        assert calc.enrich_trade(_valid_trade(strike=bad_strike)) is None

    def test_strike_zero_never_reaches_black_scholes_as_a_fabricated_zero_delta(self):
        """Direct proof the gate fires BEFORE calculate_greeks's bare
        except can silently produce a plausible-looking zero -- if this
        gate were missing, calculate_greeks(strike_price=0, ...) would
        raise inside math.log(spot/0) and its own except would swallow it,
        returning delta=0.0 as if that were a legitimate answer."""
        calc = DeltaFlowCalculator()
        # Sanity: confirm calculate_greeks itself DOES silently swallow a
        # zero-strike failure (the underlying hazard this gate closes).
        swallowed = calc.black_scholes.calculate_greeks(
            spot_price=_SPOT, strike_price=0.0, time_to_expiry=1.0,
            implied_volatility=0.8, option_type="call",
        )
        assert swallowed["delta"] == 0.0  # confirms the silent-swallow hazard exists

        result = calc.enrich_trade(_valid_trade(strike=0.0))
        assert result is None  # enrich_trade must never reach that swallow path

    @pytest.mark.parametrize("bad_option_type", [None, "", "X", "call"])
    def test_invalid_option_type_is_skipped(self, bad_option_type):
        calc = DeltaFlowCalculator()
        assert calc.enrich_trade(_valid_trade(option_type=bad_option_type)) is None

    @pytest.mark.parametrize("bad_direction", [None, "", "hold"])
    def test_invalid_direction_is_skipped(self, bad_direction):
        calc = DeltaFlowCalculator()
        assert calc.enrich_trade(_valid_trade(direction=bad_direction)) is None

    @pytest.mark.parametrize("bad_amount", [None, 0, -5])
    def test_invalid_amount_is_skipped(self, bad_amount):
        calc = DeltaFlowCalculator()
        assert calc.enrich_trade(_valid_trade(amount=bad_amount)) is None

    @pytest.mark.parametrize("bad_index_price", [None, 0, -1])
    def test_invalid_index_price_is_skipped(self, bad_index_price):
        calc = DeltaFlowCalculator()
        assert calc.enrich_trade(_valid_trade(index_price=bad_index_price)) is None

    @pytest.mark.parametrize("bad_expiration", [None, ""])
    def test_falsy_expiration_is_skipped(self, bad_expiration):
        calc = DeltaFlowCalculator()
        assert calc.enrich_trade(_valid_trade(expiration=bad_expiration)) is None

    @pytest.mark.parametrize("bad_instrument_name", [None, "", "not-a-valid-name", "BTC-BADEXP-100-C"])
    def test_unparseable_instrument_name_is_skipped(self, bad_instrument_name):
        calc = DeltaFlowCalculator()
        assert calc.enrich_trade(_valid_trade(instrument_name=bad_instrument_name)) is None

    def test_missing_trade_timestamp_is_skipped(self):
        calc = DeltaFlowCalculator()
        assert calc.enrich_trade(_valid_trade(trade_timestamp=None)) is None

    def test_null_price_is_skipped(self):
        """Review fix, Minor #1: a None price must be skipped, never
        coerced to 0.0 -- a silent coercion would understate premium_usd
        while leaving skipped_count unchanged, breaking the invariant that
        trade_count/skipped_count unambiguously describes every column in
        a FlowBucket row."""
        calc = DeltaFlowCalculator()
        assert calc.enrich_trade(_valid_trade(price=None)) is None

    def test_zero_price_is_not_skipped(self):
        """price == 0.0 is a legitimately possible value (a worthless
        option) -- distinct from a MISSING value. Only None is gated."""
        calc = DeltaFlowCalculator()
        enriched = calc.enrich_trade(_valid_trade(price=0.0))
        assert enriched is not None
        assert enriched.premium_usd == 0.0


class TestEnrichTradeExpiredOption:
    """Enumeration item 11 -- tau <= 0 is NOT a skip."""

    def test_trade_at_expiry_uses_intrinsic_delta_not_skipped(self):
        calc = DeltaFlowCalculator()
        expiry_ms = int(_FAR_FUTURE_EXPIRY_DT.replace(tzinfo=timezone.utc).timestamp() * 1000)
        trade = _valid_trade(trade_timestamp=expiry_ms, option_type="C", strike=50_000.0,
                              instrument_name="BTC-27MAR27-50000-C")

        enriched = calc.enrich_trade(trade)

        assert enriched is not None
        # Deep ITM call at expiry: intrinsic delta == 1.0 (spot 64000 > strike 50000)
        assert enriched.delta == 1.0

    def test_trade_after_expiry_otm_put_uses_zero_intrinsic_delta(self):
        calc = DeltaFlowCalculator()
        expiry_ms = int(_FAR_FUTURE_EXPIRY_DT.replace(tzinfo=timezone.utc).timestamp() * 1000)
        trade = _valid_trade(trade_timestamp=expiry_ms + 1000, option_type="P", strike=50_000.0,
                              instrument_name="BTC-27MAR27-50000-P")

        enriched = calc.enrich_trade(trade)

        assert enriched is not None
        # OTM put at expiry (spot 64000 > strike 50000): intrinsic delta == 0.0
        assert enriched.delta == 0.0


class TestSignedContributionAcceptanceT61:
    """
    Spec T6.1 -- three trades, signed delta, hand-computed. S = 64,000 for
    all. This is the discriminating test the spec calls out explicitly: an
    implementation using |delta| instead of signed delta returns
    +275,200 instead of +364,800 and must fail this test.
    """

    def test_three_trades_signed_delta_matches_spec_table(self):
        calc = DeltaFlowCalculator()
        spot = 64_000.0

        c1 = calc.compute_signed_contribution(direction="buy", delta=0.50, amount=10, index_price=spot, price=0.0)
        c2 = calc.compute_signed_contribution(direction="sell", delta=-0.30, amount=5, index_price=spot, price=0.0)
        c3 = calc.compute_signed_contribution(direction="buy", delta=-0.40, amount=2, index_price=spot, price=0.0)

        assert c1["hiro_usd"] == pytest.approx(320_000.0)
        assert c2["hiro_usd"] == pytest.approx(96_000.0)
        assert c3["hiro_usd"] == pytest.approx(-51_200.0)

        total_hiro = c1["hiro_usd"] + c2["hiro_usd"] + c3["hiro_usd"]
        assert total_hiro == pytest.approx(364_800.0)

        total_gross = c1["gross_delta_usd"] + c2["gross_delta_usd"] + c3["gross_delta_usd"]
        assert total_gross == pytest.approx(467_200.0)

    def test_unsigned_delta_formula_would_fail_this_discrimination(self):
        """Documents exactly what the spec warns against: |delta| makes
        trade 2 (selling a put) register negative and trade 3 (buying a
        put) register positive -- both backwards vs. the signed formula."""
        spot = 64_000.0
        # |delta| variant, computed by hand per the spec's own counter-example.
        wrong_total = (+1 * 0.50 * 10 * spot) + (-1 * 0.30 * 5 * spot) + (+1 * 0.40 * 2 * spot)
        assert wrong_total == pytest.approx(275_200.0)

        calc = DeltaFlowCalculator()
        c1 = calc.compute_signed_contribution(direction="buy", delta=0.50, amount=10, index_price=spot, price=0.0)
        c2 = calc.compute_signed_contribution(direction="sell", delta=-0.30, amount=5, index_price=spot, price=0.0)
        c3 = calc.compute_signed_contribution(direction="buy", delta=-0.40, amount=2, index_price=spot, price=0.0)
        correct_total = c1["hiro_usd"] + c2["hiro_usd"] + c3["hiro_usd"]

        assert correct_total != pytest.approx(wrong_total)
        assert correct_total == pytest.approx(364_800.0)


class TestComputeHourlyBucketsSkipAccountingT63:
    """Spec T6.3 -- 10 trades, 2 with iv=None, 1 with iv=0."""

    def test_skip_accounting_matches_spec(self):
        calc = DeltaFlowCalculator()
        trades = [_valid_trade(trade_id=f"T{i}") for i in range(7)]
        trades.append(_valid_trade(trade_id="T8", iv=None))
        trades.append(_valid_trade(trade_id="T9", iv=None))
        trades.append(_valid_trade(trade_id="T10", iv=0))

        buckets = calc.compute_hourly_buckets(trades)

        all_bucket = buckets["ALL"]
        assert all_bucket.trade_count == 7
        assert all_bucket.skipped_count == 3

        single = calc.enrich_trade(_valid_trade())
        expected_hiro = 7 * single.hiro_usd
        assert all_bucket.hiro_usd == pytest.approx(expected_hiro)
        # No zeros injected for the 3 skipped trades -- sum is over the 7 valid only.
        assert all_bucket.hiro_usd != 0.0

        exp_bucket = buckets[_FAR_FUTURE_EXPIRY_STR]
        assert exp_bucket.trade_count == 7
        assert exp_bucket.skipped_count == 3
        assert exp_bucket.hiro_usd == pytest.approx(expected_hiro)


class TestComputeHourlyBucketsDegenerateCases:
    """Enumeration items 12-14."""

    def test_empty_trades_list_returns_empty_dict_not_fabricated_all_row(self):
        calc = DeltaFlowCalculator()
        buckets = calc.compute_hourly_buckets([])
        assert buckets == {}

    def test_every_trade_skipped_still_creates_a_bucket_with_zero_aggregates(self):
        calc = DeltaFlowCalculator()
        trades = [_valid_trade(trade_id=f"T{i}", iv=None) for i in range(5)]

        buckets = calc.compute_hourly_buckets(trades)

        all_bucket = buckets["ALL"]
        assert all_bucket.trade_count == 0
        assert all_bucket.skipped_count == 5
        assert all_bucket.hiro_usd == 0.0
        assert all_bucket.premium_usd == 0.0
        assert all_bucket.gross_delta_usd == 0.0
        assert all_bucket.net_contracts == 0.0
        assert all_bucket.gross_contracts == 0.0

        exp_bucket = buckets[_FAR_FUTURE_EXPIRY_STR]
        assert exp_bucket.trade_count == 0
        assert exp_bucket.skipped_count == 5

    def test_skip_rate_none_when_bucket_never_populated(self):
        empty_bucket = FlowBucket(
            expiration="ALL", hiro_usd=0.0, premium_usd=0.0, gross_delta_usd=0.0,
            net_contracts=0.0, gross_contracts=0.0, trade_count=0, buy_count=0,
            sell_count=0, skipped_count=0,
        )
        assert empty_bucket.skip_rate is None

    def test_skip_rate_one_when_all_trades_skipped(self):
        all_skipped_bucket = FlowBucket(
            expiration="ALL", hiro_usd=0.0, premium_usd=0.0, gross_delta_usd=0.0,
            net_contracts=0.0, gross_contracts=0.0, trade_count=0, buy_count=0,
            sell_count=0, skipped_count=5,
        )
        assert all_skipped_bucket.skip_rate == pytest.approx(1.0)

    def test_skip_rate_normal_case(self):
        bucket = FlowBucket(
            expiration="ALL", hiro_usd=0.0, premium_usd=0.0, gross_delta_usd=0.0,
            net_contracts=0.0, gross_contracts=0.0, trade_count=7, buy_count=7,
            sell_count=0, skipped_count=3,
        )
        assert bucket.skip_rate == pytest.approx(0.3)

    def test_mixed_expirations_partition_correctly(self):
        """Two different expirations in the same trade batch each get their
        own bucket; ALL sums across both."""
        calc = DeltaFlowCalculator()
        other_expiry = "26JUN27"
        trades = [
            _valid_trade(trade_id="A1"),
            _valid_trade(trade_id="A2"),
            _valid_trade(
                trade_id="B1", expiration=other_expiry,
                instrument_name=f"BTC-{other_expiry}-{int(_STRIKE)}-C",
            ),
        ]

        buckets = calc.compute_hourly_buckets(trades)

        assert buckets[_FAR_FUTURE_EXPIRY_STR].trade_count == 2
        assert buckets[other_expiry].trade_count == 1
        assert buckets["ALL"].trade_count == 3
        assert set(buckets.keys()) == {_FAR_FUTURE_EXPIRY_STR, other_expiry, "ALL"}

    def test_never_traded_expiration_gets_no_fabricated_bucket(self):
        """An expiration that never appears in the input trades at all must
        never get a bucket -- unlike C3's currency-wide-coverage-proxy bug,
        there is no cross-reference to 'the current chain' here that could
        synthesize a false zero-trade entry for a listed-but-untraded
        expiry."""
        calc = DeltaFlowCalculator()
        buckets = calc.compute_hourly_buckets([_valid_trade()])
        assert "26JUN27" not in buckets
