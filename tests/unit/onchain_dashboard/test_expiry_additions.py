"""
The parts of report.txt that the Expiries tab did not show before: put/call by moneyness, the share in the money, the
delta-adjusted flow of the expiry, the top five strikes by buying and selling pressure and the large open interest changes.
Values are compared with the typed result of the production pipeline (offline fixture), a missing part is never a zero.
"""

import dataclasses
from types import SimpleNamespace

import pytest

from coding.core.analytics.onchain_dashboard.expiry_section import (
    build_expiries_section, oi_change_table, statistic_rows, strike_pressure_tables)
from coding.core.analytics.onchain_dashboard.formatting import number_text
from coding.core.analytics.onchain_dashboard.expiry_section import ordered_bundles


@pytest.fixture(scope="module")
def bundle_with_surface(real_result):
    """A real expiry bundle with a volatility surface (the offline fixture records no trades, so its flow is empty)."""
    bundles = [b for b in ordered_bundles(real_result) if b.vol_surface is not None]
    assert bundles, "the fixture has no expiry with a volatility surface: the test would prove nothing"
    return bundles[0]


def make_flow():
    """A flow result with enough trades and one strike on each side (only the fields the table reads)."""
    buy = SimpleNamespace(strike=55000.0, option_type="P", net_flow=167.8, volume=196.9, notional=12093617.85)
    sell = SimpleNamespace(strike=60000.0, option_type="C", net_flow=-40.2, volume=61.0, notional=3811000.0)
    return SimpleNamespace(sufficient_data=True, trade_count=810, top_buy_strikes=(buy,), top_sell_strikes=(sell,))


def rows_of(bundle, bucket=None):
    return {row[0]: row[1] for row in statistic_rows(bundle, bucket)}


def test_every_view_of_every_expiry_shows_the_added_sections(real_result):
    section = build_expiries_section(real_result)
    views = next(iter(section.switchers.values()))["views"]
    assert views
    for view in views.values():
        caption = view["caption"]
        assert "In the money, share of open interest" in caption
        assert "Delta-adjusted flow" in caption
        assert "Put/call by moneyness" in caption or "Volatility surface" in caption
        assert "Top 5 strikes" in caption or "Top strikes by flow" in caption         # the tables, or the note saying why there are none
        assert "Large open interest changes" in caption


def test_top_strike_tables_show_the_numbers_of_the_result():
    html = strike_pressure_tables(SimpleNamespace(flow=make_flow()))
    assert "Top 5 strikes by buying pressure, 24 h" in html and "Top 5 strikes by selling pressure, 24 h" in html
    for text in ("55,000", "Put", "+167.8", "196.9", "12,093,618", "60,000", "Call", "-40.2", "61.0", "3,811,000"):
        assert text in html, text
    assert "contracts x index" in html                                   # the definition of notional is written next to the number


def test_statistic_rows_carry_moneyness_buckets_and_the_delta_flow_of_the_expiry(bundle_with_surface):
    bucket = SimpleNamespace(hiro_usd=-8596074.0, premium_usd=-797153.0, gross_delta_usd=52029476.0, trade_count=810, skipped_count=0)
    rows = rows_of(bundle_with_surface, bucket)
    atm = bundle_with_surface.vol_surface.pc_by_moneyness.atm
    key = [k for k in rows if k.startswith("Put/call by moneyness, at the money")][0]
    assert atm.range_label in key
    assert number_text(bundle_with_surface.analysis.moneyness.calls.itm_pct, 1) in rows["In the money, share of open interest"]
    assert rows["Delta-adjusted flow, signed (HIRO)"] == "-8,596,074 USD"
    assert rows["Delta-adjusted flow, signed premium"] == "-797,153 USD"
    assert rows["Delta-adjusted flow, gross |delta| notional"] == "52,029,476 USD"
    assert rows["Trades behind the delta-adjusted flow"] == "810 (skipped 0)"


def test_a_missing_delta_flow_is_not_computed_and_not_zero(bundle_with_surface):
    rows = rows_of(bundle_with_surface, None)
    assert rows["Delta-adjusted flow, report window"].startswith("not computed")
    assert not any("HIRO" in key for key in rows)


def test_an_empty_moneyness_bucket_is_said_to_be_empty_not_zero(bundle_with_surface):
    surface = bundle_with_surface.vol_surface
    empty = dataclasses.replace(surface.pc_by_moneyness.near_otm, call_oi=0.0, put_oi=0.0, ratio=None)
    patched = dataclasses.replace(bundle_with_surface, vol_surface=dataclasses.replace(
        surface, pc_by_moneyness=dataclasses.replace(surface.pc_by_moneyness, near_otm=empty)))
    rows = rows_of(patched)
    key = [k for k in rows if "near out of the money" in k][0]
    assert rows[key] == "no instruments in this range"


@pytest.mark.parametrize("flow, expected", [
    (None, "Top strikes by flow: not computed"),
    (SimpleNamespace(sufficient_data=False, trade_count=3), "not enough trades (3)")])
def test_missing_or_thin_flow_gives_a_sentence_and_no_table(flow, expected):
    html = strike_pressure_tables(SimpleNamespace(flow=flow))
    assert expected in html and "<table" not in html


@pytest.mark.parametrize("changes, expected", [
    (None, "not computed"),
    (SimpleNamespace(has_previous_snapshot=False, rows=(), total_significant=0), "no previous snapshot"),
    (SimpleNamespace(has_previous_snapshot=True, rows=(), total_significant=0), "none above 20%")])
def test_oi_changes_say_why_when_there_is_nothing_to_show(changes, expected):
    html = oi_change_table(SimpleNamespace(oi_changes=changes))
    assert expected in html and "<table" not in html


def test_oi_changes_table_shows_the_rows():
    row = SimpleNamespace(strike=64000.0, option_type="P", previous_oi=100.0, current_oi=150.0, change=50.0, change_pct=50.0)
    changes = SimpleNamespace(has_previous_snapshot=True, rows=(row,), total_significant=1)
    html = oi_change_table(SimpleNamespace(oi_changes=changes))
    assert "64,000" in html and "Put" in html and "+50" in html and "+50.0%" in html
