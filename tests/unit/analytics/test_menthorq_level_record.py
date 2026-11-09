"""
build_level_record / classify_gamma_regime: regime boundaries and the
'not computed' reasons (missing IV, missing open interest, no GEX at all).
"""

import json
from datetime import timedelta
from pathlib import Path

import pytest

from coding.core.analytics.menthorq_monitor.alert_rules import (
    ALERT_KEY_CROSSED_ABOVE_HVL,
    ALERT_KEY_CROSSED_BELOW_HVL,
    ALERT_KEY_HVL_MOVE,
    AlertThresholds,
    evaluate_urgent_alerts,
)
from coding.core.analytics.menthorq_monitor.level_record import (
    HVL_AT_CHAIN_EDGE_REASON,
    build_level_record,
    build_not_computed_record,
    classify_gamma_regime,
    has_levels_sql_condition,
)
from coding.core.analytics.menthorq_monitor.level_trend_calculator import compare_records
from coding.core.analytics.menthorq_replication_calculator import replicate_menthorq
from tests.fakes.fake_menthorq_monitor import (
    BASE_TIME,
    FakeInputLoader,
    make_record,
    make_record_without_levels,
)


def test_has_levels_needs_one_level_or_total_net_gex_and_ignores_spot():
    assert make_record().has_levels
    assert not make_record_without_levels(spot=100000.0).has_levels   # spot alone: not computed
    assert not make_record_without_levels(spot=None).has_levels
    assert make_record_without_levels(call_resistance=110000.0).has_levels
    assert make_record_without_levels(put_support=90000.0).has_levels
    assert make_record_without_levels(hvl=95000.0).has_levels
    assert make_record_without_levels(total_net_gex=-1.0).has_levels
    # a zero is a computed value, not a missing one
    assert make_record_without_levels(total_net_gex=0.0).has_levels


def test_has_levels_sql_condition_names_the_four_columns():
    assert has_levels_sql_condition("EXCLUDED") == (
        "(EXCLUDED.call_resistance IS NOT NULL OR EXCLUDED.put_support IS NOT NULL "
        "OR EXCLUDED.hvl IS NOT NULL OR EXCLUDED.total_net_gex IS NOT NULL)")


def test_gamma_regime_boundaries():
    assert classify_gamma_regime(100.0, 99.0) == "positive"
    assert classify_gamma_regime(99.0, 100.0) == "negative"
    assert classify_gamma_regime(100.0, 100.0) == "at_hvl"
    assert classify_gamma_regime(None, 100.0) is None
    assert classify_gamma_regime(100.0, None) is None


def _record_from(inputs):
    replication = replicate_menthorq(inputs["chain_rows"], inputs["index_price"], BASE_TIME)
    return build_level_record(replication, inputs, "BTC", "live", BASE_TIME, BASE_TIME)


def test_rows_without_open_interest_and_iv_are_reported():
    inputs = FakeInputLoader().load_inputs("BTC", BASE_TIME)
    inputs["chain_rows"][0]["open_interest"] = None         # the call
    inputs["iv_source_counts"] = {"mark_iv": 1, "rebuilt_from_mark_price": 0, "missing": 1}
    record = _record_from(inputs)
    assert record.call_resistance is None                   # no positive net GEX left
    assert record.put_support == 90000.0
    assert "Call Resistance not computed: no strike with positive net GEX" in record.not_computed_reasons
    assert "1 chain rows have no open interest: left out of GEX" in record.not_computed_reasons
    assert any(reason.startswith("1 chain rows have no IV") for reason in record.not_computed_reasons)


def test_no_gamma_anywhere_gives_none_not_zero():
    inputs = FakeInputLoader().load_inputs("BTC", BASE_TIME)
    for row in inputs["chain_rows"]:
        row["mark_iv"] = None
    record = _record_from(inputs)
    assert (record.call_resistance, record.put_support, record.hvl, record.total_net_gex,
            record.gamma_regime) == (None, None, None, None, None)
    assert "total net GEX not computed: no leg has both gamma and open interest" in record.not_computed_reasons
    assert "27NOV26: GEX not computed (no leg has both gamma and open interest)" in record.not_computed_reasons


def test_interior_hvl_is_kept():
    # Fake chain: 80,000 call (no OI, net GEX 0), 90,000 put (-214k), 110,000 call (+254k).
    # Cumulative: 0, -214k, +40k -> minimum at 90,000, between the edges 80,000 and 110,000.
    record = _record_from(FakeInputLoader().load_inputs("BTC", BASE_TIME))
    assert record.hvl == 90000.0
    assert record.gamma_regime == "positive"               # spot 100,000 above HVL
    assert HVL_AT_CHAIN_EDGE_REASON not in record.not_computed_reasons


def _edge_hvl_inputs(only_puts: bool):
    """The fake chain without the 80,000 row: the HVL sits at a chain edge."""
    inputs = FakeInputLoader().load_inputs("BTC", BASE_TIME)
    inputs["chain_rows"] = inputs["chain_rows"][:2]          # 110,000 call, 90,000 put
    if only_puts:
        inputs["chain_rows"][0] = {**inputs["chain_rows"][0], "option_type": "P",
                                   "instrument_name": "BTC-27NOV26-110000-P"}
    return inputs


@pytest.mark.parametrize("only_puts, replicated_hvl", [
    # 90,000 put -214k then 110,000 call +254k: cumulative -214k, +40k -> minimum at the LOWEST strike
    (False, 90000.0),
    # two puts: cumulative only falls -> minimum at the HIGHEST strike
    (True, 110000.0),
])
def test_hvl_at_a_chain_edge_is_not_computed(only_puts, replicated_hvl):
    inputs = _edge_hvl_inputs(only_puts)
    replication = replicate_menthorq(inputs["chain_rows"], inputs["index_price"], BASE_TIME)
    record = build_level_record(replication, inputs, "BTC", "live", BASE_TIME, BASE_TIME)
    # the replication / chart output is unchanged
    assert replication["iv_times_open_interest"]["levels"]["hvl"]["cumulative_net_gex_argmin"] == replicated_hvl
    assert record.hvl is None and record.gamma_regime is None
    assert HVL_AT_CHAIN_EDGE_REASON in record.not_computed_reasons
    assert "HVL not computed: no net GEX by strike" not in record.not_computed_reasons
    assert record.put_support is not None and record.total_net_gex is not None   # others kept
    assert record.has_levels


def test_hvl_at_a_chain_edge_fires_no_hvl_alert():
    day_ago = BASE_TIME - timedelta(hours=24)
    interior_inputs = FakeInputLoader().load_inputs("BTC", BASE_TIME)
    previous = build_level_record(                                             # HVL 90,000, positive
        replicate_menthorq(interior_inputs["chain_rows"], 100000.0, BASE_TIME),
        interior_inputs, "BTC", "live", day_ago, day_ago)
    edge_inputs = _edge_hvl_inputs(True)
    latest = build_level_record(
        replicate_menthorq(edge_inputs["chain_rows"], 100000.0, BASE_TIME),
        edge_inputs, "BTC", "live", BASE_TIME, BASE_TIME)
    assert previous.hvl == 90000.0 and latest.hvl is None
    # Without the rule the edge HVL 110,000 would be a 20%-of-spot move and a crossing below.
    thresholds = AlertThresholds(spot_crosses_hvl=True)
    alerts = evaluate_urgent_alerts(latest, compare_records(latest, previous, "previous"),
                                    compare_records(latest, previous, "24h"), thresholds)
    hvl_keys = {ALERT_KEY_HVL_MOVE, ALERT_KEY_CROSSED_BELOW_HVL, ALERT_KEY_CROSSED_ABOVE_HVL}
    assert [alert.key for alert in alerts if alert.key in hvl_keys] == []


REFERENCE_DIRECTORY = Path(__file__).parents[3] / "outputs" / "menthorq_replicate"


@pytest.mark.parametrize("reference_name", [
    "menthorq_reference_20260824T2359Z.json",
    "menthorq_reference_20261012T2359Z.json",
    "menthorq_reference_20261106T2359Z.json",
])
def test_verified_reference_hvls_are_inside_the_chain(reference_name):
    # MenthorQ's HVL (68,000 / 83,000 / 79,000) lies strictly inside the strikes it
    # displays, which are a subset of the chain: the edge rule never drops it.
    reference = json.loads((REFERENCE_DIRECTORY / reference_name).read_text(encoding="utf-8"))
    displayed_strikes = [float(strike) for strike in reference["iv_times_open_interest"]["bars"]]
    hvl = reference["iv_times_open_interest"]["levels"]["hvl"]
    assert min(displayed_strikes) < hvl < max(displayed_strikes)


def test_not_computed_record_and_mode_validation():
    record = build_not_computed_record("BTC", "daily_close", BASE_TIME, BASE_TIME + timedelta(seconds=1),
                                       "monthly", "index", "inputs not available: x")
    assert record.spot is None and record.not_computed_reasons == ["inputs not available: x"]
    with pytest.raises(ValueError):
        build_not_computed_record("BTC", "hourly", BASE_TIME, BASE_TIME, "monthly", "index", "x")
