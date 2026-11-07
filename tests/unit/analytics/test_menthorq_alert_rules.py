"""
Urgent-alert rules: threshold boundaries ("more than" is strict), crossing
and sign-flip keys, expiry-roll label, cooldown. Spot is 100,000 throughout,
so 1% of spot = 1,000 USD.
"""

from datetime import timedelta

import pytest

from coding.core.analytics.menthorq_monitor.alert_rules import (
    ALERT_KEY_CALL_RESISTANCE_MOVE,
    ALERT_KEY_CROSSED_BELOW_HVL,
    ALERT_KEY_HVL_MOVE,
    ALERT_KEY_NET_GEX_TURNED_NEGATIVE,
    ALERT_KEY_NET_GEX_TURNED_POSITIVE,
    ALERT_KEY_PUT_SUPPORT_MOVE,
    AlertThresholds,
    apply_cooldown,
    evaluate_urgent_alerts,
)
from coding.core.analytics.menthorq_monitor.level_trend_calculator import compare_records
from tests.fakes.fake_menthorq_monitor import BASE_TIME, make_record

PREVIOUS_TIME = BASE_TIME - timedelta(hours=1)
DAY_AGO = BASE_TIME - timedelta(hours=24)


def _keys(latest, previous, day_ago, thresholds=AlertThresholds()):
    alerts = evaluate_urgent_alerts(compare_records(latest, previous, "previous"),
                                    compare_records(latest, day_ago, "24h"), thresholds)
    return {alert.key: alert for alert in alerts}


def test_hvl_move_of_exactly_three_percent_does_not_fire_just_above_does():
    latest = make_record(hvl=95000.0)
    previous = make_record(as_of=PREVIOUS_TIME, hvl=95000.0)
    # 92,000 -> 95,000 = 3,000 USD = 3.000% of 100,000: not MORE than 3%
    assert ALERT_KEY_HVL_MOVE not in _keys(latest, previous, make_record(as_of=DAY_AGO, hvl=92000.0))
    # 91,990 -> 95,000 = 3,010 USD = 3.01%: fires
    alerts = _keys(latest, previous, make_record(as_of=DAY_AGO, hvl=91990.0))
    assert alerts[ALERT_KEY_HVL_MOVE].change_usd == 3010.0
    assert alerts[ALERT_KEY_HVL_MOVE].change_percent_of_spot == pytest.approx(3.01)


def test_wall_moves_use_five_percent_both_directions():
    latest = make_record(call_resistance=110000.0, put_support=90000.0)
    previous = make_record(as_of=PREVIOUS_TIME)
    # CR 104,990 -> 110,000 = +5.01%: fires; PS 95,000 -> 90,000 = -5.00%: does not
    alerts = _keys(latest, previous, make_record(as_of=DAY_AGO, call_resistance=104990.0,
                                                 put_support=95000.0))
    assert ALERT_KEY_CALL_RESISTANCE_MOVE in alerts
    assert ALERT_KEY_PUT_SUPPORT_MOVE not in alerts
    # PS 95,010 -> 90,000 = -5.01%: fires
    alerts = _keys(latest, previous, make_record(as_of=DAY_AGO, put_support=95010.0))
    assert alerts[ALERT_KEY_PUT_SUPPORT_MOVE].change_usd == -5010.0


def test_thresholds_are_configurable():
    latest = make_record(hvl=95000.0)
    day_ago = make_record(as_of=DAY_AGO, hvl=93500.0)   # 1.5% of spot
    previous = make_record(as_of=PREVIOUS_TIME)
    assert ALERT_KEY_HVL_MOVE not in _keys(latest, previous, day_ago)
    assert ALERT_KEY_HVL_MOVE in _keys(latest, previous, day_ago,
                                       AlertThresholds(hvl_move_percent_of_spot=1.0))


def test_spot_cross_and_sign_flip_vs_previous_run_with_direction_keys():
    previous = make_record(as_of=PREVIOUS_TIME, spot=96000.0, hvl=95000.0, total_net_gex=2e6)
    latest = make_record(spot=94000.0, hvl=95000.0, total_net_gex=-1e6)
    alerts = _keys(latest, previous, None)
    assert ALERT_KEY_CROSSED_BELOW_HVL in alerts
    assert alerts[ALERT_KEY_NET_GEX_TURNED_NEGATIVE].change_usd == -3e6
    flipped_back = _keys(make_record(as_of=BASE_TIME + timedelta(hours=1), total_net_gex=1e6),
                         latest, None)
    assert ALERT_KEY_NET_GEX_TURNED_POSITIVE in flipped_back


def test_expiry_roll_is_attached_to_the_alert():
    latest = make_record(hvl=95000.0, expirations=["27NOV26"])
    day_ago = make_record(as_of=DAY_AGO, hvl=90000.0, expirations=["6NOV26", "27NOV26"])
    alert = _keys(latest, make_record(as_of=PREVIOUS_TIME, hvl=95000.0), day_ago)[ALERT_KEY_HVL_MOVE]
    assert alert.expiry_roll == ("6NOV26",)


def test_no_references_no_alerts():
    assert _keys(make_record(), None, None) == {}


def test_cooldown_blocks_within_twelve_hours_only_for_the_same_key():
    previous = make_record(as_of=PREVIOUS_TIME, spot=96000.0, hvl=95000.0, total_net_gex=2e6)
    latest = make_record(spot=94000.0, hvl=95000.0, total_net_gex=-1e6)
    alerts = list(_keys(latest, previous, None).values())
    now = BASE_TIME

    recent = {ALERT_KEY_CROSSED_BELOW_HVL: now - timedelta(hours=11, minutes=59)}
    kept = apply_cooldown(alerts, recent, now, timedelta(hours=12))
    assert [alert.key for alert in kept] == [ALERT_KEY_NET_GEX_TURNED_NEGATIVE]

    exactly_twelve = {ALERT_KEY_CROSSED_BELOW_HVL: now - timedelta(hours=12)}
    assert len(apply_cooldown(alerts, exactly_twelve, now, timedelta(hours=12))) == 2
    assert len(apply_cooldown(alerts, {}, now)) == 2
