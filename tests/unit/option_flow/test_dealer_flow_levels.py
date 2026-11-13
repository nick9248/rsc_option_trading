"""Flow-based dealer levels: hand-calculated gamma exposure, the dealer sign, the open-interest check and the gate."""

import math
from datetime import datetime, timedelta, timezone

import pytest

from coding.core.analytics.menthorq_replication_calculator import SECONDS_PER_YEAR
from coding.core.analytics.option_flow.dealer_levels_figure import bar_widths, build_dealer_levels_figure, visible_range
from coding.service.option_flow.dealer_flow_levels_service import DealerFlowLevelsService

SPOT = 100_000.0
EXPIRY = "27NOV26"
SETTLEMENT = datetime(2026, 11, 27, 8, 0, tzinfo=timezone.utc)
CAPTURED = SETTLEMENT - timedelta(seconds=0.25 * SECONDS_PER_YEAR)      # exactly a quarter of a year to expiry
NOW = CAPTURED + timedelta(minutes=5)
# Black-Scholes gamma at the money, r = 0, sigma = 0.5, T = 0.25:  phi(d1) / (S sigma sqrt(T)),  d1 = sigma sqrt(T) / 2 = 0.125
GAMMA = math.exp(-0.125 ** 2 / 2) / math.sqrt(2 * math.pi) / (SPOT * 0.5 * math.sqrt(0.25))
GEX_PER_CONTRACT = GAMMA * SPOT ** 2 * 0.01                              # USD per 1 % move per contract


class FakeRepository:
    def __init__(self, flow_rows, call_open_interest=100.0, put_open_interest=50.0, coverage=(95, 100), chain_expiries=(EXPIRY,)):
        self.flow_rows, self.coverage, self.chain_expiries = flow_rows, coverage, chain_expiries
        self.call_open_interest, self.put_open_interest = call_open_interest, put_open_interest
        self.flow_start = None

    def get_option_chain_snapshot_at_or_before(self, currency, as_of):
        rows = []
        for expiry in self.chain_expiries:
            for kind, interest in (("C", self.call_open_interest), ("P", self.put_open_interest)):
                rows.append({"instrument_name": f"{currency}-{expiry}-{SPOT:.0f}-{kind}", "expiration": expiry, "strike": SPOT,
                             "option_type": kind, "open_interest": interest, "mark_iv": 50.0, "underlying_price": SPOT,
                             "mark_price": 0.05})
        return CAPTURED, rows

    def get_index_price_at_or_before(self, currency, as_of):
        return SPOT, CAPTURED

    def get_first_trade_timestamp(self, currency, expiration):
        return int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp() * 1000)

    def get_signed_taker_flow_by_strike(self, currency, expiration, since_ts):
        self.flow_start = since_ts
        return self.flow_rows

    def get_trade_hour_coverage(self, currency, since_ts):
        return self.coverage


def flow(calls_taker_net, puts_taker_net):
    return [{"strike": SPOT, "option_type": "C", "taker_net": calls_taker_net, "gross_volume": abs(calls_taker_net), "trade_count": 3},
            {"strike": SPOT, "option_type": "P", "taker_net": puts_taker_net, "gross_volume": abs(puts_taker_net), "trade_count": 2}]


def service(repository):
    return DealerFlowLevelsService(repository, clock=lambda: NOW)


def test_dealer_position_is_minus_the_taker_flow_and_gex_follows_by_hand():
    # takers bought 10 calls and sold 4 puts -> dealers are short 10 calls and long 4 puts
    levels = service(FakeRepository(flow(10.0, -4.0))).levels("BTC", EXPIRY)
    row = levels.rows[0]
    assert (row.dealer_net_calls, row.dealer_net_puts) == (-10.0, 4.0)
    assert row.flow_gex == pytest.approx((-10 + 4) * GEX_PER_CONTRACT, rel=1e-3)                  # about -9,500 USD per 1 %
    assert levels.total_flow_gex == pytest.approx(row.flow_gex)
    assert levels.trades_used == 5 and levels.index_price == SPOT


def test_oi_based_view_is_computed_next_to_it_by_hand():
    levels = service(FakeRepository(flow(10.0, -4.0))).levels("BTC", EXPIRY)
    # standard view: call OI 100 and put OI 50 at the same gamma, dealers long calls and short puts -> (100 - 50) x gamma x S^2 x 1 %
    assert levels.rows[0].oi_gex == pytest.approx(50 * GEX_PER_CONTRACT, rel=1e-3)
    assert (levels.rows[0].call_open_interest, levels.rows[0].put_open_interest) == (100.0, 50.0)


def test_levels_are_read_from_the_gex_by_strike():
    levels = service(FakeRepository(flow(-10.0, 0.0))).levels("BTC", EXPIRY)     # takers sold 10 calls -> dealers long 10 calls
    assert levels.flow_call_wall == SPOT and levels.flow_put_wall is None and levels.flow_gamma_flip is None
    assert levels.rows[0].flow_gex > 0


def test_within_open_interest_and_full_coverage_passes_the_gate_at_the_boundary():
    levels = service(FakeRepository(flow(10.0, -4.0), coverage=(95, 100))).levels("BTC", EXPIRY)     # 95 % is allowed
    assert levels.gate_passed and levels.gate_reason is None
    assert (levels.violations, levels.options_checked) == (0, 2) and levels.coverage == pytest.approx(0.95)


def test_a_dealer_position_above_open_interest_fails_the_gate_and_says_why():
    levels = service(FakeRepository(flow(250.0, -4.0))).levels("BTC", EXPIRY)           # 250 calls > open interest 100
    assert (levels.violations, levels.options_checked) == (1, 2) and levels.violation_rate == 0.5
    assert not levels.gate_passed and "50.0% of options exceed their open interest" in levels.gate_reason


def test_low_trade_coverage_and_no_trades_fail_the_gate():
    low = service(FakeRepository(flow(10.0, -4.0), coverage=(90, 100))).levels("BTC", EXPIRY)
    assert not low.gate_passed and "coverage 90.0%" in low.gate_reason
    none = service(FakeRepository([])).levels("BTC", EXPIRY)
    assert not none.gate_passed and "no stored trades" in none.gate_reason and none.rows[0].flow_gex == 0.0


def test_the_flow_starts_at_the_first_trade_but_not_before_the_stable_coverage_date():
    repository = FakeRepository(flow(1.0, 1.0))
    service(repository).levels("BTC", EXPIRY)
    assert repository.flow_start == int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp() * 1000)
    repository.get_first_trade_timestamp = lambda currency, expiration: 1_000                  # 1970: older than the stable date
    levels = service(repository).levels("BTC", EXPIRY)
    assert levels.flow_start == datetime(2026, 4, 25, tzinfo=timezone.utc)


def test_unknown_expiry_is_an_error_and_expirations_exclude_expired_ones_soonest_first():
    repository = FakeRepository(flow(1.0, 1.0), chain_expiries=("25DEC26", "27NOV26", "1AUG26"))
    with pytest.raises(LookupError):
        service(repository).levels("BTC", "4DEC26")
    assert service(repository).expirations("BTC") == ["27NOV26", "25DEC26"]                   # 1AUG26 already expired


def test_expiry_label_is_case_insensitive():
    assert service(FakeRepository(flow(1.0, 1.0))).levels("BTC", "27nov26").expiry == EXPIRY


def test_bar_widths_follow_the_nearest_neighbour_and_one_strike_gets_a_default():
    assert bar_widths([60_000.0, 61_000.0, 63_000.0]) == [pytest.approx(800.0), pytest.approx(800.0), pytest.approx(1600.0)]
    assert bar_widths([70_000.0]) == [800.0]


def test_figure_has_a_bar_panel_per_view_and_leaves_missing_oi_out():
    levels = service(FakeRepository(flow(10.0, -4.0))).levels("BTC", EXPIRY)
    figure = build_dealer_levels_figure(levels)
    assert [bar.name for bar in figure.data] == ["Flow-based GEX", "OI-based GEX"]
    assert list(figure.data[0].x) == [pytest.approx(levels.rows[0].flow_gex / 1e6)]
    levels.rows[0].oi_gex = None
    assert len(build_dealer_levels_figure(levels).data[1].x) == 0                                # not drawn as zero
    assert "dealer position <= open interest" in figure.layout.annotations[-1].text


def test_visible_range_drops_tiny_far_strikes_but_keeps_spot_and_levels():
    levels = service(FakeRepository(flow(10.0, -4.0))).levels("BTC", EXPIRY)
    big, tiny, far = levels.rows[0], levels.rows[0].__class__(150_000.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0), None
    levels.rows = [big, tiny]
    low, high = visible_range(levels)
    assert low < SPOT < high < 150_000.0                                                       # the tiny 150k strike is not drawn
    levels.rows = []
    assert visible_range(levels) is None
