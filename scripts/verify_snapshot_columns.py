"""Spot-check snapshot columns for a random instrument across several expirations."""

import random
from coding.core.logging.logging_setup import init_logging
init_logging(level="WARNING")

from coding.service.deribit.deribit_api_service import DeribitApiService
from coding.service.snapshot.snapshot_service import SnapshotService

EXPECTED_FIELDS = [
    "instrument_name", "underlying_price", "mark_price", "bid_price", "ask_price",
    "mid_price", "open_interest", "volume", "volume_usd", "last", "interest_rate",
    "creation_timestamp", "estimated_delivery_price",
]

with DeribitApiService() as api:
    service = SnapshotService(api)

    result = api.get_expirations(currency="BTC")
    all_expirations = sorted(result.get("btc", {}).get("option", []))

    # Sample near, mid, and far expirations
    sample_expiries = [all_expirations[0], all_expirations[len(all_expirations)//2], all_expirations[-1]]
    print(f"Spot-checking expirations: {sample_expiries}")
    print()

    for expiry in sample_expiries:
        data = service.get_filtered_instruments(
            currency="BTC",
            expirations=[expiry],
            min_volume=0,
            fetch_greeks=False
        )
        if not data:
            print(f"{expiry}: no data")
            continue

        instrument = random.choice(data)
        name = instrument.get("instrument_name", "?")
        underlying = instrument.get("underlying_price", 0)

        print(f"--- {expiry} | {name} ---")

        # Check all expected fields
        missing = []
        for field in EXPECTED_FIELDS:
            val = instrument.get(field)
            if val is None:
                missing.append(field)
            else:
                print(f"  {field}: {val}")

        if missing:
            print(f"  MISSING fields: {missing}")

        # Sanity checks
        mark = instrument.get("mark_price", 0) or 0
        mark_usd = mark * underlying
        bid = instrument.get("bid_price") or 0
        ask = instrument.get("ask_price") or 0

        print(f"  --> mark_price in USD: ${mark_usd:,.2f}")
        if bid and ask:
            spread_ok = bid <= ask
            print(f"  --> bid <= ask: {spread_ok} (bid={bid}, ask={ask})")
        if underlying > 0:
            delivery = instrument.get("estimated_delivery_price") or 0
            delivery_diff_pct = abs(delivery - underlying) / underlying * 100 if delivery else None
            if delivery_diff_pct is not None:
                reasonable = delivery_diff_pct < 10
                print(f"  --> estimated_delivery_price vs underlying: {delivery_diff_pct:.2f}% diff, reasonable={reasonable}")
        print()
