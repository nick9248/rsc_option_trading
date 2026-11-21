"""
The parts of report.txt that the Expiries tab did not show before: put/call by moneyness, the share in the money, the
delta-adjusted flow of the expiry, the top five strikes by buying and selling pressure and the large open interest changes.
The inputs are the real result classes (the flow comes from the real BuySellFlowAnalyzer on a few trades); a missing part is
never a zero.
"""

import dataclasses
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from coding.core.analytics.buy_sell_flow_analyzer import BuySellFlowAnalyzer
from coding.core.analytics.onchain_dashboard.expiry_section import (
    build_expiries_section, delta_flow_window_text, oi_change_table, ordered_bundles, statistic_rows, strike_pressure_tables)
from coding.core.analytics.onchain_dashboard.formatting import number_text
from coding.core.analytics.results.analysis_result import OiChangeRow, OiChangesResult
from coding.core.analytics.results.delta_flow_results import FlowBucket

WINDOW_START_MS, WINDOW_END_MS = 1_700_000_000_000, 1_700_086_400_000


def trade(strike, option_type, direction, amount, index_price=60000.0):
    return {"strike": strike, "option_type": option_type, "direction": direction, "amount": amount, "index_price": index_price,
            "trade_id": "t", "trade_timestamp": WINDOW_END_MS - 1000, "instrument_name": "x", "price": 0.01}


@pytest.fixture(scope="module")
def flow_result():
    """A real FlowResult: 8 trades, enough for the data-sufficiency gate."""
    trades = [trade(55000.0, "P", "buy", 100.0), trade(55000.0, "P", "sell", 20.0), trade(60000.0, "C", "sell", 80.0),
              trade(60000.0, "C", "buy", 10.0), trade(62000.0, "C", "buy", 30.0), trade(50000.0, "P", "sell", 40.0),
              trade(52000.0, "P", "buy", 15.0), trade(66000.0, "C", "sell", 25.0)]
    result = BuySellFlowAnalyzer(trades, "BTC", "27NOV26", 60000.0, WINDOW_START_MS, WINDOW_END_MS).calculate()
    assert result.sufficient_data and result.top_buy_strikes and result.top_sell_strikes
    return result


@pytest.fixture(scope="module")
def bundle_with_surface(real_result):
    """A real expiry bundle with a volatility surface (the offline fixture records no trades, so its flow is empty)."""
    bundles = [b for b in ordered_bundles(real_result) if b.vol_surface is not None]
    assert bundles, "the fixture has no expiry with a volatility surface: the test would prove nothing"
    return bundles[0]


def make_bucket(expiration):
    return FlowBucket(expiration=expiration, hiro_usd=-8596074.0, premium_usd=-797153.0, gross_delta_usd=52029476.0, net_contracts=-120.5,
                      gross_contracts=900.0, trade_count=810, buy_count=400, sell_count=410, skipped_count=0)


def rows_of(bundle, bucket=None, window=""):
    return {row[0]: row[1] for row in statistic_rows(bundle, bucket, window)}


def test_every_view_of_every_expiry_shows_the_added_sections(real_result):
    section = build_expiries_section(real_result)
    views = next(iter(section.switchers.values()))["views"]
    assert views
    for view in views.values():
        caption = view["caption"]
        assert "In the money, share of open interest value" in caption
        assert "Delta-adjusted flow" in caption
        assert "Put/call by moneyness" in caption or "Volatility surface" in caption
        assert "Top 5 strikes" in caption or "Top strikes by flow" in caption         # the tables, or the note saying why there are none
        assert "Large open interest changes" in caption


def test_top_strike_tables_show_the_numbers_of_the_real_flow_result(flow_result):
    html = strike_pressure_tables(SimpleNamespace(flow=flow_result))
    assert "Top 5 strikes by buying pressure, 24 h" in html and "Top 5 strikes by selling pressure, 24 h" in html
    best_buy, best_sell = flow_result.top_buy_strikes[0], flow_result.top_sell_strikes[0]
    # hand-calculated from the trades above: 55,000 put net +80 (bought 100, sold 20), 60,000 call net -70 (bought 10, sold 80)
    assert (best_buy.strike, best_buy.option_type, round(best_buy.net_flow, 6)) == (55000.0, "P", 80.0)
    assert (best_sell.strike, best_sell.option_type, round(best_sell.net_flow, 6)) == (60000.0, "C", -70.0)
    for text in ("55,000", "Put", "+80.0", "100.0", "6,000,000", "60,000", "Call", "-70.0", "80.0", "4,800,000"):
        assert text in html, text
    assert "contracts x index" in html                                   # the definition of notional is written next to the number


def test_statistic_rows_carry_moneyness_buckets_and_the_delta_flow_of_the_expiry(bundle_with_surface):
    rows = rows_of(bundle_with_surface, make_bucket(bundle_with_surface.expiration), "24 h, 24 hourly rows persisted")
    atm = bundle_with_surface.vol_surface.pc_by_moneyness.atm
    key = [k for k in rows if k.startswith("Put/call by moneyness, at the money")][0]
    assert atm.range_label in key
    assert number_text(bundle_with_surface.analysis.moneyness.calls.itm_pct, 1) in rows["In the money, share of open interest value (notional)"]
    assert rows["Delta-adjusted flow, signed (HIRO)"] == "-8,596,074 USD"
    assert rows["Delta-adjusted flow, signed premium"] == "-797,153 USD"
    assert rows["Delta-adjusted flow, gross |delta| notional"] == "52,029,476 USD"
    assert rows["Trades behind the delta-adjusted flow"] == "810 (skipped 0)"
    assert rows["Window of the delta-adjusted flow"] == "24 h, 24 hourly rows persisted"


def test_the_bucket_of_an_expiry_reaches_its_captions_by_the_same_expiry_label(real_result):
    first = ordered_bundles(real_result)[0]
    result = dataclasses.replace(real_result, delta_flow_buckets=(make_bucket(first.expiration), make_bucket("ALL")))
    views = next(iter(build_expiries_section(result).switchers.values()))["views"]
    own = [v["caption"] for key, v in views.items() if key.startswith(first.expiration)]
    others = [v["caption"] for key, v in views.items() if not key.startswith(first.expiration)]
    assert own and all("-8,596,074 USD" in caption for caption in own)
    assert others and not any("-8,596,074 USD" in caption for caption in others)      # no row of another expiry, and "ALL" is not used


def test_a_missing_delta_flow_is_not_computed_and_not_zero(bundle_with_surface):
    rows = rows_of(bundle_with_surface, None)
    assert rows["Delta-adjusted flow, report window"].startswith("not computed")
    assert not any("HIRO" in key for key in rows)


def test_the_delta_flow_window_says_how_much_data_there_is_and_when_it_is_stale(real_result):
    assert delta_flow_window_text(dataclasses.replace(real_result, delta_flow_lookback_hours=24.0, delta_flow_hours_present=3,
                                                      delta_flow_stale_since=None)) == "24 h, 3 hourly rows persisted"
    # the service stores the newest hour as a NAIVE UTC datetime (a real run failed on this); an aware one must work too
    for stale in (datetime(2026, 11, 20, 12, 0), datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)):
        text = delta_flow_window_text(dataclasses.replace(real_result, delta_flow_stale_since=stale))
        assert "STALE" in text and "2026-11-20 12:00 UTC" in text


def test_an_empty_moneyness_bucket_is_said_to_be_empty_not_zero(bundle_with_surface):
    surface = bundle_with_surface.vol_surface
    empty = dataclasses.replace(surface.pc_by_moneyness.near_otm, call_oi=0.0, put_oi=0.0, ratio=None)
    patched = dataclasses.replace(bundle_with_surface, vol_surface=dataclasses.replace(
        surface, pc_by_moneyness=dataclasses.replace(surface.pc_by_moneyness, near_otm=empty)))
    key = [k for k in rows_of(patched) if "near out of the money" in k][0]
    assert rows_of(patched)[key] == "no instruments in this range"


@pytest.mark.parametrize("flow, expected", [
    (None, "Top strikes by flow: not computed"),
    (SimpleNamespace(sufficient_data=False, trade_count=3), "not enough trades (3)")])
def test_missing_or_thin_flow_gives_a_sentence_and_no_table(flow, expected):
    html = strike_pressure_tables(SimpleNamespace(flow=flow))
    assert expected in html and "<table" not in html


@pytest.mark.parametrize("changes, expected", [
    (None, "not computed"),
    (OiChangesResult(rows=(), total_significant=0, has_previous_snapshot=False), "no previous snapshot"),
    (OiChangesResult(rows=(), total_significant=0, has_previous_snapshot=True), "none of at least 20% and 10 coins")])
def test_oi_changes_say_why_when_there_is_nothing_to_show(changes, expected):
    html = oi_change_table(SimpleNamespace(oi_changes=changes))
    assert expected in html and "<table" not in html


def test_oi_changes_table_shows_the_rows_and_the_thresholds_of_the_service():
    row = OiChangeRow(strike=64000.0, option_type="P", previous_oi=100.0, current_oi=150.0, change=50.0, change_pct=50.0)
    html = oi_change_table(SimpleNamespace(oi_changes=OiChangesResult(rows=(row,), total_significant=1, has_previous_snapshot=True)))
    for text in ("64,000", "Put", "100", "150", "+50", "+50.0%", "1 of at least 20% and 10 coins"):
        assert text in html, text
