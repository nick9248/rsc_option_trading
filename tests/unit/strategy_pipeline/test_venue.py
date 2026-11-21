"""Derive's fees and minimum order size: hand-calculated numbers, the size search, and the pipeline's use of them."""

import pytest

from coding.core.analytics.candidates.distribution import LognormalDistribution
from coding.core.analytics.candidates.generator import generate_candidates
from coding.core.analytics.candidates.metrics import compute_metrics, metrics_at_venue_size
from coding.core.analytics.risk.venue import DERIVE_TAKER_FEES, DERIVE_VENUES, Venue
from coding.service.strategy_pipeline.strategy_pick_service import MODE_LIVE, MODE_REPLAY
from tests.unit.backtest.test_backtest_service import NOW
from tests.unit.strategy_pipeline.test_shortlist_and_report import DAYS, FORWARD, VOL, chain
from tests.unit.strategy_pipeline.test_strategy_pick_service import AS_OF, make_service

SPOT = 60_000.0
BTC = DERIVE_VENUES["BTC"]


def test_the_derive_taker_fee_of_one_order_is_the_base_fee_plus_the_capped_rate_hand_calculated():
    # 0.01 BTC of an option priced 1,000 at spot 60,000: 0.5 + min(0.03% x 60,000 x 0.01 = 0.18, 12.5% x 1,000 x 0.01 = 1.25) = 0.68 USD
    assert DERIVE_TAKER_FEES.fee_per_coin_usd(SPOT, 1_000.0, 0.01) * 0.01 == pytest.approx(0.68)
    # a cheap option: the cap binds: 0.5 + 12.5% x 40 x 0.01 = 0.55 USD
    assert DERIVE_TAKER_FEES.fee_per_coin_usd(SPOT, 40.0, 0.01) * 0.01 == pytest.approx(0.55)
    with pytest.raises(ValueError):
        DERIVE_TAKER_FEES.fee_per_coin_usd(SPOT, 1_000.0, 0.0)


def test_the_venues_carry_the_minimum_orders_read_from_the_derive_api():
    assert (BTC.minimum_amount, BTC.amount_step) == (0.01, 0.00001)
    assert (DERIVE_VENUES["ETH"].minimum_amount, DERIVE_VENUES["ETH"].amount_step) == (0.1, 0.01)
    assert BTC.floor_to_step(0.0123456789) == pytest.approx(0.01234) and BTC.floor_to_step(0.01) == 0.01
    assert DERIVE_VENUES["ETH"].floor_to_step(0.159) == pytest.approx(0.15)               # never rounded up


def test_a_candidate_is_sized_to_the_risk_limit_with_fees_at_that_size():
    distributions = {"implied": LognormalDistribution(FORWARD, VOL, DAYS)}
    spread = next(c for c in generate_candidates(chain(), FORWARD) if c.family == "put_credit_spread")
    metrics = metrics_at_venue_size(spread, FORWARD, distributions, BTC, risk_per_trade_usd=50.0)
    assert metrics is not None and metrics.quantity >= BTC.minimum_amount
    assert metrics.quantity * metrics.max_loss_usd <= 50.0 + 1e-9 and metrics.quantity * metrics.max_loss_usd > 50.0 * 0.99     # as large as allowed
    expected_fees = sum(0.5 / metrics.quantity + min(0.0003 * FORWARD, 0.125 * leg.entry_price_usd) for leg in spread.legs)
    assert metrics.trading_fees_usd == pytest.approx(expected_fees)
    assert round(metrics.quantity / BTC.amount_step) * BTC.amount_step == pytest.approx(metrics.quantity)        # a multiple of the step
    assert compute_metrics(spread, FORWARD, distributions).quantity is None                                       # the research default has no size


def test_a_candidate_whose_minimum_order_risks_more_than_the_limit_is_not_placeable():
    spread = next(c for c in generate_candidates(chain(), FORWARD) if c.family == "put_credit_spread")
    assert metrics_at_venue_size(spread, FORWARD, {}, BTC, risk_per_trade_usd=0.01) is None


def test_a_live_shortlist_is_sized_for_the_venue_and_a_replay_one_is_not():
    service, _, _ = make_service()
    service.venues = {"BTC": Venue("test venue", minimum_amount=0.0001, amount_step=0.0001, taker_fees=DERIVE_TAKER_FEES)}
    entry, _ = service._entry_for("BTC", AS_OF, MODE_REPLAY, 7.0, 2.0, None, NOW)
    _, live = service._shortlist_for("BTC", AS_OF, entry, NOW, MODE_LIVE)
    _, replay = service._shortlist_for("BTC", AS_OF, entry, NOW, MODE_REPLAY)
    assert all(item.metrics.quantity >= 0.0001 and item.metrics.quantity * item.metrics.max_loss_usd <= service.risk_budget.risk_per_trade_usd + 1e-9
               for item in live)
    assert replay and all(item.metrics.quantity is None for item in replay)


def test_a_venue_whose_minimum_cannot_be_afforded_leaves_no_candidates():
    service, _, _ = make_service()
    service.venues = {"BTC": Venue("huge minimum", minimum_amount=1_000.0, amount_step=1.0, taker_fees=DERIVE_TAKER_FEES)}
    entry, _ = service._entry_for("BTC", AS_OF, MODE_REPLAY, 7.0, 2.0, None, NOW)
    _, live = service._shortlist_for("BTC", AS_OF, entry, NOW, MODE_LIVE)
    assert live == []


def test_a_live_shortlist_keeps_only_candidates_with_a_positive_expected_result_so_the_model_is_not_paid_to_choose_among_losers():
    service, _, _ = make_service()
    service.venues = {"BTC": Venue("test venue", minimum_amount=0.0001, amount_step=0.0001, taker_fees=DERIVE_TAKER_FEES)}
    entry, _ = service._entry_for("BTC", AS_OF, MODE_REPLAY, 7.0, 2.0, None, NOW)
    _, live = service._shortlist_for("BTC", AS_OF, entry, NOW, MODE_LIVE)
    _, replay = service._shortlist_for("BTC", AS_OF, entry, NOW, MODE_REPLAY)
    assert all(item.metrics.by_distribution["realised"].expected_pnl_usd > 0 for item in live)
    assert any(item.metrics.by_distribution["realised"].expected_pnl_usd <= 0 for item in replay)       # the replay keeps them: the filter is the venue path's


def test_the_venue_check_reports_a_changed_minimum_and_nothing_when_all_agree():
    from scripts.check_derive_venue import differences
    same = {"minimum_amount": "0.01", "amount_step": "0.00001", "base_fee": "0.5", "taker_fee_rate": "0.0003", "mark_price_fee_rate_cap": "0.125"}
    assert differences({"BTC": BTC}, lambda currency: same) == []
    changed = dict(same, minimum_amount="0.1", base_fee="1")
    assert differences({"BTC": BTC}, lambda currency: changed) == ["BTC minimum order: the code has 0.01, Derive says 0.1",
                                                                  "BTC base fee: the code has 0.5, Derive says 1"]


def test_a_crossed_quote_with_no_loss_is_dropped_instead_of_dividing_by_zero():
    from coding.core.analytics.backtest.structures import Leg, Quote
    from coding.core.analytics.candidates.generator import Candidate
    zero_loss = Candidate("put_credit_spread", (Leg("sell", "put", 60_000.0, "S", 500.0), Leg("buy", "put", 59_000.0, "B", 500.0)), 0.0, 0.0)
    assert metrics_at_venue_size(zero_loss, SPOT, {}, BTC, 10.0) is None
