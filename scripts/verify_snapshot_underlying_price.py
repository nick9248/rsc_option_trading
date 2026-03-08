"""Verify that snapshot underlying_price is consistent across all expirations after fix."""

import random
from coding.core.logging.logging_setup import init_logging
init_logging(level="WARNING")

from coding.service.deribit.deribit_api_service import DeribitApiService
from coding.service.snapshot.snapshot_service import SnapshotService

with DeribitApiService() as api:
    service = SnapshotService(api)

    result = api.get_expirations(currency="BTC")
    expirations = sorted(result.get("btc", {}).get("option", []))

    print("Verifying underlying_price after fix for each expiry:")
    print()

    prices_seen = set()
    for expiry in expirations:
        data = service.get_filtered_instruments(
            currency="BTC",
            expirations=[expiry],
            min_volume=0,
            fetch_greeks=False
        )
        if data:
            prices = set(item.get("underlying_price") for item in data)
            consistent = len(prices) == 1
            price = list(prices)[0]
            prices_seen.add(price)

            sample = random.choice(data)
            print(
                f"  {expiry}: underlying_price=${price:,.2f} | "
                f"consistent={consistent} | "
                f"sample={sample['instrument_name']} "
                f"mark_price={sample.get('mark_price')} "
                f"OI={sample.get('open_interest')}"
            )
        else:
            print(f"  {expiry}: no data")

    print()
    print(f"Unique underlying prices across all expirations: {len(prices_seen)}")
    if len(prices_seen) == 1:
        print(f"  -> All expirations use same price: ${list(prices_seen)[0]:,.2f}  PASS")
    else:
        print(f"  -> Prices: {sorted(prices_seen)}  FAIL - still inconsistent")
