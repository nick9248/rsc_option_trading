"""
build_level_record / classify_gamma_regime: regime boundaries and the
'not computed' reasons (missing IV, missing open interest, no GEX at all).
"""

from datetime import timedelta

import pytest

from coding.core.analytics.menthorq_monitor.level_record import (
    build_level_record,
    build_not_computed_record,
    classify_gamma_regime,
)
from coding.core.analytics.menthorq_replication_calculator import replicate_menthorq
from tests.fakes.fake_menthorq_monitor import BASE_TIME, FakeInputLoader


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


def test_not_computed_record_and_mode_validation():
    record = build_not_computed_record("BTC", "daily_close", BASE_TIME, BASE_TIME + timedelta(seconds=1),
                                       "monthly", "index", "inputs not available: x")
    assert record.spot is None and record.not_computed_reasons == ["inputs not available: x"]
    with pytest.raises(ValueError):
        build_not_computed_record("BTC", "hourly", BASE_TIME, BASE_TIME, "monthly", "index", "x")
