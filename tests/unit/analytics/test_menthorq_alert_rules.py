"""
Urgent-alert rules: default ON/OFF set, threshold boundaries ("more than" is
strict, "within" is inclusive), crossing and sign-flip keys, near-wall rule,
expiry-roll label, cooldown. Spot is 100,000 throughout, so 1% of spot =
1,000 USD.
"""

from datetime import timedelta

import pytest

from coding.core.analytics.menthorq_monitor.alert_rules import (
    ALERT_KEY_CALL_RESISTANCE_MOVE,
    ALERT_KEY_CROSSED_ABOVE_HVL,
    ALERT_KEY_CROSSED_BELOW_HVL,
    ALERT_KEY_DATA_STALE,
    ALERT_KEY_HVL_MOVE,
    ALERT_KEY_NET_GEX_TURNED_NEGATIVE,
    ALERT_KEY_NET_GEX_TURNED_POSITIVE,
    ALERT_KEY_PUT_SUPPORT_MOVE,
    ALERT_KEY_SPOT_NEAR_CALL_RESISTANCE,
    ALERT_KEY_SPOT_NEAR_PUT_SUPPORT,
    AlertThresholds,
    apply_cooldown,
    cooldown_for_key,
    evaluate_urgent_alerts,
)
from coding.core.analytics.menthorq_monitor.level_trend_calculator import compare_records
from tests.fakes.fake_menthorq_monitor import BASE_TIME, make_record

PREVIOUS_TIME = BASE_TIME - timedelta(hours=1)
DAY_AGO = BASE_TIME - timedelta(hours=24)
ALL_ON = AlertThresholds(spot_crosses_hvl=True, spot_near_wall_percent=1.0)


def _keys(latest, previous, day_ago, thresholds=AlertThresholds()):
    alerts = evaluate_urgent_alerts(latest, compare_records(latest, previous, "previous"),
                                    compare_records(latest, day_ago, "24h"), thresholds)
    return {alert.key: alert for alert in alerts}


def test_default_rules_are_the_owner_choice():
    thresholds = AlertThresholds()
    assert thresholds.hvl_move_percent_of_spot == 5.0
    assert thresholds.call_resistance_move_percent_of_spot == 10.0
    assert thresholds.put_support_move_percent_of_spot == 5.0
    assert thresholds.net_gex_sign_change is True
    assert thresholds.spot_crosses_hvl is False
    assert thresholds.spot_near_wall_percent is None
    assert thresholds.cooldown == timedelta(hours=12)


def test_hvl_move_of_exactly_five_percent_does_not_fire_just_above_does():
    latest = make_record(hvl=95000.0)
    previous = make_record(as_of=PREVIOUS_TIME, hvl=95000.0)
    # 90,000 -> 95,000 = 5,000 USD = 5.000% of 100,000: not MORE than 5%
    assert ALERT_KEY_HVL_MOVE not in _keys(latest, previous, make_record(as_of=DAY_AGO, hvl=90000.0))
    # 89,990 -> 95,000 = 5,010 USD = 5.01%: fires
    alerts = _keys(latest, previous, make_record(as_of=DAY_AGO, hvl=89990.0))
    assert alerts[ALERT_KEY_HVL_MOVE].change_usd == 5010.0
    assert alerts[ALERT_KEY_HVL_MOVE].change_percent_of_spot == pytest.approx(5.01)
    assert alerts[ALERT_KEY_HVL_MOVE].title == "HVL moved more than 5% of spot in 24h"


def test_call_resistance_uses_ten_percent_put_support_five_percent():
    latest = make_record(call_resistance=110000.0, put_support=90000.0)
    previous = make_record(as_of=PREVIOUS_TIME)
    # CR 100,000 -> 110,000 = +10.00%: no; PS 95,000 -> 90,000 = -5.00%: no
    alerts = _keys(latest, previous, make_record(as_of=DAY_AGO, call_resistance=100000.0,
                                                 put_support=95000.0))
    assert ALERT_KEY_CALL_RESISTANCE_MOVE not in alerts and ALERT_KEY_PUT_SUPPORT_MOVE not in alerts
    # CR 99,990 -> 110,000 = +10.01%: fires; PS 95,010 -> 90,000 = -5.01%: fires
    alerts = _keys(latest, previous, make_record(as_of=DAY_AGO, call_resistance=99990.0,
                                                 put_support=95010.0))
    assert alerts[ALERT_KEY_CALL_RESISTANCE_MOVE].change_usd == 10010.0
    assert alerts[ALERT_KEY_PUT_SUPPORT_MOVE].change_usd == -5010.0
    # A CR move of 7% (old 5% threshold) no longer fires
    alerts = _keys(latest, previous, make_record(as_of=DAY_AGO, call_resistance=103000.0))
    assert ALERT_KEY_CALL_RESISTANCE_MOVE not in alerts


def test_thresholds_are_configurable_and_none_switches_a_rule_off():
    latest = make_record(hvl=95000.0)
    day_ago = make_record(as_of=DAY_AGO, hvl=93500.0)   # 1.5% of spot
    previous = make_record(as_of=PREVIOUS_TIME)
    assert ALERT_KEY_HVL_MOVE not in _keys(latest, previous, day_ago)
    assert ALERT_KEY_HVL_MOVE in _keys(latest, previous, day_ago,
                                       AlertThresholds(hvl_move_percent_of_spot=1.0))
    huge_move = make_record(as_of=DAY_AGO, hvl=50000.0)   # 45% of spot
    assert ALERT_KEY_HVL_MOVE not in _keys(latest, previous, huge_move,
                                           AlertThresholds(hvl_move_percent_of_spot=None))


def test_spot_crossing_is_off_by_default_sign_flip_is_on():
    previous = make_record(as_of=PREVIOUS_TIME, spot=96000.0, hvl=95000.0, total_net_gex=2e6)
    latest = make_record(spot=94000.0, hvl=95000.0, total_net_gex=-1e6)
    alerts = _keys(latest, previous, None)
    assert set(alerts) == {ALERT_KEY_NET_GEX_TURNED_NEGATIVE}
    assert alerts[ALERT_KEY_NET_GEX_TURNED_NEGATIVE].change_usd == -3e6
    assert _keys(latest, previous, None, AlertThresholds(net_gex_sign_change=False)) == {}


def test_spot_cross_when_enabled_with_direction_keys():
    previous = make_record(as_of=PREVIOUS_TIME, spot=96000.0, hvl=95000.0, total_net_gex=2e6)
    latest = make_record(spot=94000.0, hvl=95000.0, total_net_gex=-1e6)
    thresholds = AlertThresholds(spot_crosses_hvl=True)
    assert ALERT_KEY_CROSSED_BELOW_HVL in _keys(latest, previous, None, thresholds)
    back = make_record(as_of=BASE_TIME + timedelta(hours=1), spot=96000.0, total_net_gex=1e6)
    flipped_back = _keys(back, latest, None, thresholds)
    assert ALERT_KEY_CROSSED_ABOVE_HVL in flipped_back
    assert ALERT_KEY_NET_GEX_TURNED_POSITIVE in flipped_back


def test_spot_near_wall_when_enabled_is_inclusive():
    # CR 101,000 - 100,000 = +1.00% of spot (within 1%); PS 98,990 = -1.01% (not within)
    latest = make_record(call_resistance=101000.0, put_support=98990.0, hvl=95000.0)
    assert _keys(latest, None, None) == {}                          # off by default
    alerts = _keys(latest, None, None, AlertThresholds(spot_near_wall_percent=1.0))
    assert set(alerts) == {ALERT_KEY_SPOT_NEAR_CALL_RESISTANCE}
    assert alerts[ALERT_KEY_SPOT_NEAR_CALL_RESISTANCE].title == \
        "Spot within 1% of Call Resistance 101,000 (distance +1.00% of spot)"
    # PS 99,000 = -1.00%: within
    alerts = _keys(make_record(put_support=99000.0), None, None, AlertThresholds(spot_near_wall_percent=1.0))
    assert set(alerts) == {ALERT_KEY_SPOT_NEAR_PUT_SUPPORT}
    # Missing level: no alert (not a guess)
    assert _keys(make_record(call_resistance=None, put_support=None), None, None, ALL_ON) == {}


def test_describe_lists_every_rule_with_its_state():
    assert AlertThresholds().describe() == [
        "HVL move > 5% of spot vs 24h: ON",
        "Call Resistance move > 10% of spot vs 24h: ON",
        "Put Support move > 5% of spot vs 24h: ON",
        "Total net GEX sign change: ON",
        "Spot crosses HVL: OFF",
        "Spot near Call Resistance / Put Support: OFF",
        "Cooldown per alert: 12h (24h moves: 24h)",
    ]
    described = AlertThresholds(hvl_move_percent_of_spot=None, spot_crosses_hvl=True,
                                spot_near_wall_percent=1.5).describe()
    assert described[0] == "HVL move vs 24h: OFF"
    assert described[4] == "Spot crosses HVL: ON"
    assert described[5] == "Spot within 1.5% of Call Resistance / Put Support: ON"


def test_expiry_roll_is_attached_to_the_alert():
    latest = make_record(hvl=95000.0, expirations=["27NOV26"])
    day_ago = make_record(as_of=DAY_AGO, hvl=89000.0, expirations=["6NOV26", "27NOV26"])
    alert = _keys(latest, make_record(as_of=PREVIOUS_TIME, hvl=95000.0), day_ago)[ALERT_KEY_HVL_MOVE]
    assert alert.expiry_roll == ("6NOV26",)


def test_no_references_no_alerts():
    assert _keys(make_record(), None, None, ALL_ON) == {}


def test_cooldown_blocks_within_twelve_hours_only_for_the_same_key():
    previous = make_record(as_of=PREVIOUS_TIME, spot=96000.0, hvl=95000.0, total_net_gex=2e6)
    latest = make_record(spot=94000.0, hvl=95000.0, total_net_gex=-1e6)
    alerts = list(_keys(latest, previous, None, AlertThresholds(spot_crosses_hvl=True)).values())
    now = BASE_TIME

    recent = {ALERT_KEY_CROSSED_BELOW_HVL: now - timedelta(hours=11, minutes=59)}
    kept = apply_cooldown(alerts, recent, now, timedelta(hours=12))
    assert [alert.key for alert in kept] == [ALERT_KEY_NET_GEX_TURNED_NEGATIVE]

    exactly_twelve = {ALERT_KEY_CROSSED_BELOW_HVL: now - timedelta(hours=12)}
    assert len(apply_cooldown(alerts, exactly_twelve, now, timedelta(hours=12))) == 2
    assert len(apply_cooldown(alerts, {}, now)) == 2


def test_level_move_alerts_are_held_for_the_whole_24h_comparison_window():
    """A move vs 24 h ago stays true for 24 h; with the 12 h cooldown it alerted twice.
    HVL 89,000 -> 95,000 = 6% (fires), PS 95,010 -> 90,000 = -5.01% (fires),
    CR 99,990 -> 110,000 = +10.01% (fires), plus the net GEX sign flip (12 h key)."""
    latest = make_record(hvl=95000.0, call_resistance=110000.0, put_support=90000.0, total_net_gex=-1e6)
    previous = make_record(as_of=PREVIOUS_TIME, total_net_gex=2e6)
    day_ago = make_record(as_of=DAY_AGO, hvl=89000.0, call_resistance=99990.0, put_support=95010.0)
    alerts = list(_keys(latest, previous, day_ago).values())
    move_keys = {ALERT_KEY_HVL_MOVE, ALERT_KEY_CALL_RESISTANCE_MOVE, ALERT_KEY_PUT_SUPPORT_MOVE}
    assert {alert.key for alert in alerts} == move_keys | {ALERT_KEY_NET_GEX_TURNED_NEGATIVE}
    now = BASE_TIME

    def kept_after(age):
        delivered = {alert.key: now - age for alert in alerts}
        return {alert.key for alert in apply_cooldown(alerts, delivered, now, timedelta(hours=12))}

    assert kept_after(timedelta(hours=11, minutes=59)) == set()
    assert kept_after(timedelta(hours=12)) == {ALERT_KEY_NET_GEX_TURNED_NEGATIVE}  # 12 h key repeats
    assert kept_after(timedelta(hours=23, minutes=59)) == {ALERT_KEY_NET_GEX_TURNED_NEGATIVE}
    assert kept_after(timedelta(hours=24)) == move_keys | {ALERT_KEY_NET_GEX_TURNED_NEGATIVE}


def test_cooldown_for_key_uses_the_longer_of_configured_and_comparison_window():
    assert cooldown_for_key(ALERT_KEY_HVL_MOVE, timedelta(hours=12)) == timedelta(hours=24)
    assert cooldown_for_key(ALERT_KEY_PUT_SUPPORT_MOVE, timedelta(hours=30)) == timedelta(hours=30)
    for key in (ALERT_KEY_CROSSED_BELOW_HVL, ALERT_KEY_NET_GEX_TURNED_POSITIVE,
                ALERT_KEY_SPOT_NEAR_CALL_RESISTANCE, ALERT_KEY_DATA_STALE):
        assert cooldown_for_key(key, timedelta(hours=12)) == timedelta(hours=12)


def test_event_rules_against_the_last_reported_state():
    """Pure rule: with a reported state the current state fires while it differs; without one,
    the change since the previous run. Previous run -1M, latest -1M (no flip)."""
    previous = make_record(as_of=BASE_TIME - timedelta(hours=1), total_net_gex=-1e6)
    latest = make_record(total_net_gex=-1e6)
    previous_comparison = compare_records(latest, previous, "previous")
    day_comparison = compare_records(latest, None, "24h")
    sign_only = AlertThresholds(hvl_move_percent_of_spot=None)

    assert evaluate_urgent_alerts(latest, previous_comparison, day_comparison, sign_only) == []
    fired = evaluate_urgent_alerts(latest, previous_comparison, day_comparison, sign_only,
                                   {"net_gex_sign": "positive"})
    assert [alert.key for alert in fired] == ["net_gex_turned_negative"]
    assert fired[0].change_usd is None and fired[0].expiry_roll == ()
    assert evaluate_urgent_alerts(latest, previous_comparison, day_comparison, sign_only,
                                  {"net_gex_sign": "negative"}) == []
    # a zero net GEX has no sign: nothing to report
    zero = make_record(total_net_gex=0.0)
    assert evaluate_urgent_alerts(zero, compare_records(zero, previous, "previous"),
                                  day_comparison, sign_only, {"net_gex_sign": "positive"}) == []
