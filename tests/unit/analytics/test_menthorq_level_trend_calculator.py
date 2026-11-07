"""
Trend comparison of MenthorQ-style records. Every expected value is worked
out by hand in the comments; "% of spot" uses the LATEST spot.
"""

from datetime import datetime, timedelta, timezone

import pytest

from coding.core.analytics.menthorq_monitor.level_trend_calculator import (
    CROSSED_ABOVE_HVL,
    CROSSED_BELOW_HVL,
    compare_records,
    settled_expiries_between,
    spot_distances,
)
from tests.fakes.fake_menthorq_monitor import BASE_TIME, make_record

DAY_AGO = BASE_TIME - timedelta(hours=24)   # 2026-11-05 09:10 UTC


def test_level_changes_in_usd_and_percent_of_latest_spot():
    reference = make_record(as_of=DAY_AGO, spot=98000.0, call_resistance=105000.0,
                            put_support=90000.0, hvl=97000.0, total_net_gex=-2e6)
    latest = make_record(spot=100000.0, call_resistance=110000.0, put_support=90000.0,
                         hvl=95000.0, total_net_gex=5e6)

    comparison = compare_records(latest, reference, "24h")

    # CR 105,000 -> 110,000: +5,000 USD = 5,000 / 100,000 = +5.0% of spot
    assert comparison.level_changes["call_resistance"].change_usd == 5000.0
    assert comparison.level_changes["call_resistance"].change_percent_of_spot == pytest.approx(5.0)
    # HVL 97,000 -> 95,000: -2,000 USD = -2.0% of spot
    assert comparison.level_changes["hvl"].change_usd == -2000.0
    assert comparison.level_changes["hvl"].change_percent_of_spot == pytest.approx(-2.0)
    # PS unchanged
    assert comparison.level_changes["put_support"].change_usd == 0.0
    # spot 98,000 -> 100,000: +2,000 = 2,000 / 98,000 = +2.0408% (of the OLD spot)
    assert comparison.spot_change_usd == 2000.0
    assert comparison.spot_change_percent == pytest.approx(2.040816, rel=1e-6)
    # net GEX -2M -> +5M: +7M and a sign flip
    assert comparison.net_gex_change == 7e6
    assert comparison.net_gex_sign_flipped is True
    # 98,000 > 97,000 and 100,000 > 95,000: positive both times, no crossing
    assert comparison.regime_before == "positive" and comparison.regime_after == "positive"
    assert comparison.hvl_crossing is None


def test_expiry_settled_in_between_is_listed_and_new_listing_reported():
    # 6NOV26 settles 2026-11-06 08:00 UTC, inside (11-05 09:10, 11-06 09:10]
    reference = make_record(as_of=DAY_AGO, expirations=["6NOV26", "27NOV26", "25DEC26"])
    latest = make_record(expirations=["27NOV26", "25DEC26", "29JAN27"])

    comparison = compare_records(latest, reference, "24h")

    assert comparison.settled_expiries == ["6NOV26"]
    assert comparison.has_expiry_roll is True
    assert comparison.new_expiries == ["29JAN27"]


def test_settlement_window_is_open_at_start_and_closed_at_end():
    settlement = datetime(2026, 11, 6, 8, 0, tzinfo=timezone.utc)
    assert settled_expiries_between(["6NOV26"], settlement, settlement + timedelta(hours=1)) == []
    assert settled_expiries_between(["6NOV26"], settlement - timedelta(hours=1), settlement) == ["6NOV26"]
    # unparseable labels are ignored, output is chronological
    assert settled_expiries_between(["6NOV26", "bad", "5NOV26"],
                                    settlement - timedelta(days=2), settlement) == ["5NOV26", "6NOV26"]


def test_spot_crossing_below_and_above_hvl():
    above = make_record(as_of=BASE_TIME - timedelta(hours=1), spot=96000.0, hvl=95000.0)
    below = make_record(spot=94000.0, hvl=95000.0)
    assert compare_records(below, above, "previous").hvl_crossing == CROSSED_BELOW_HVL
    back_above = make_record(as_of=BASE_TIME + timedelta(hours=1), spot=96000.0, hvl=95000.0)
    assert compare_records(back_above, below, "previous").hvl_crossing == CROSSED_ABOVE_HVL


def test_hvl_moving_over_a_still_spot_is_also_a_crossing():
    before = make_record(as_of=BASE_TIME - timedelta(hours=1), spot=100000.0, hvl=99000.0)
    after = make_record(spot=100000.0, hvl=101000.0)
    assert compare_records(after, before, "previous").hvl_crossing == CROSSED_BELOW_HVL


def test_missing_values_stay_none_never_zero():
    reference = make_record(as_of=DAY_AGO, hvl=None, total_net_gex=None)
    latest = make_record()
    comparison = compare_records(latest, reference, "24h")
    assert comparison.level_changes["hvl"].change_usd is None
    assert comparison.level_changes["hvl"].change_percent_of_spot is None
    assert comparison.net_gex_change is None
    assert comparison.net_gex_sign_flipped is None
    assert comparison.hvl_crossing is None


def test_no_reference_gives_an_empty_comparison():
    comparison = compare_records(make_record(), None, "7d")
    assert comparison.reference is None
    assert comparison.level_changes == {}
    assert comparison.settled_expiries == []


def test_reference_must_be_earlier():
    with pytest.raises(ValueError):
        compare_records(make_record(), make_record(), "previous")


def test_zero_net_gex_is_not_a_sign_flip():
    reference = make_record(as_of=DAY_AGO, total_net_gex=0.0)
    assert compare_records(make_record(total_net_gex=-1.0), reference, "24h").net_gex_sign_flipped is False


def test_spot_distances_signed_level_minus_spot():
    record = make_record(spot=100000.0, call_resistance=110000.0, put_support=90000.0, hvl=95000.0)
    distances = spot_distances(record)
    assert distances["call_resistance"].distance_usd == 10000.0
    assert distances["call_resistance"].distance_percent_of_spot == pytest.approx(10.0)
    assert distances["put_support"].distance_percent_of_spot == pytest.approx(-10.0)
    assert distances["hvl"].distance_usd == -5000.0
