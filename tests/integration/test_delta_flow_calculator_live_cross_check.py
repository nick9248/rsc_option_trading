"""
Live cross-check of DeltaFlowCalculator's recomputed delta against
Deribit's own ticker.greeks.delta (institutional_metrics_spec.md section 6
/ task C7, spec acceptance test T6.2).

Review fix (Minor #2): the original unit test suite only compared
DeltaFlowCalculator's BlackScholesCalculator against a second, separately-
instantiated BlackScholesCalculator -- circular, and proves nothing about
accuracy against the real market. T6.2 explicitly requires comparing
against Deribit's OWN greeks (an independent, exchange-computed value),
which requires a real API call -- hence an integration test, following the
precedent already established by tests/integration/test_deribit_api_
service.py (real calls to Deribit's public endpoints, run as part of the
normal `pytest` invocation per this project's CLAUDE.md).

Selects a near-ATM, several-days-out call dynamically (not a hardcoded
instrument name, which would expire and stop existing) so this test stays
valid indefinitely rather than needing a periodic manual bump.
"""

from datetime import datetime, timezone

import pytest

from coding.core.analytics.black_scholes_calculator import BlackScholesCalculator
from coding.core.analytics.delta_flow_calculator import DeltaFlowCalculator
from coding.service.deribit.deribit_api_service import DeribitApiService

_MIN_DAYS_TO_EXPIRY = 3.0  # avoid near-expiry degenerate tau


def _pick_near_atm_instrument(api: DeribitApiService, currency: str = "BTC"):
    """Return (instrument_name, strike) for the active call closest to spot
    with at least _MIN_DAYS_TO_EXPIRY remaining."""
    index_price = api.get_index_price(currency=currency)
    instruments = api.get_instruments(currency=currency, kind="option")
    bs = BlackScholesCalculator()
    now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)

    candidates = []
    for inst in instruments:
        if not inst.get("is_active"):
            continue
        name = inst["instrument_name"]
        parts = name.split("-")
        if len(parts) != 4 or parts[3] != "C":
            continue
        parsed = bs.parse_instrument_name(name)
        if parsed is None:
            continue
        days_to_expiry = (parsed["expiry_time"] - now_utc_naive).total_seconds() / 86400.0
        if days_to_expiry < _MIN_DAYS_TO_EXPIRY:
            continue
        candidates.append((abs(parsed["strike"] - index_price), name, parsed["strike"]))

    assert candidates, "No active BTC call with >= 3 DTE found -- cannot run live cross-check"
    candidates.sort(key=lambda c: c[0])
    _, name, strike = candidates[0]
    return name, strike, index_price


class TestDeltaFlowCalculatorLiveCrossCheckT62:
    def test_recomputed_delta_within_0_01_of_deribit_ticker_delta(self):
        api = DeribitApiService()
        try:
            instrument_name, strike, _index_price_hint = _pick_near_atm_instrument(api)
            ticker = api.get_ticker(instrument_name)
        finally:
            api.close()

        deribit_delta = ticker["greeks"]["delta"]
        mark_iv = ticker["mark_iv"]
        index_price = ticker["index_price"]
        trade_timestamp_ms = ticker["timestamp"]

        assert mark_iv and mark_iv > 0, f"{instrument_name} has no usable mark_iv -- pick a different instrument"

        trade = {
            "trade_id": "live-cross-check",
            "trade_timestamp": trade_timestamp_ms,
            "instrument_name": instrument_name,
            "expiration": instrument_name.split("-")[1],
            "strike": strike,
            "option_type": "C",
            "direction": "buy",
            "amount": 1.0,
            "price": 0.0,
            "index_price": index_price,
            "iv": mark_iv,
        }

        enriched = DeltaFlowCalculator().enrich_trade(trade)

        assert enriched is not None, f"enrich_trade skipped a live, currently-quoted instrument: {instrument_name}"
        assert abs(enriched.delta - deribit_delta) <= 0.01, (
            f"{instrument_name}: recomputed delta {enriched.delta:.5f} vs. "
            f"Deribit's {deribit_delta:.5f} (diff {abs(enriched.delta - deribit_delta):.5f} > 0.01)"
        )
