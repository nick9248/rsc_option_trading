"""
Compare the venue constants of the pipeline (minimum order, step, fees) with what Derive's public API says now.

Run it from time to time and before trusting live sizes; it only reads public data (no key, no order). Exit code 1 when a value differs.
"""

import json
import logging
import sys
import urllib.request
from typing import Callable, Dict, List, Mapping, Optional

from coding.core.analytics.risk.venue import DERIVE_VENUES, Venue
from coding.core.logging.logging_setup import init_logging

logger = logging.getLogger(__name__)

INSTRUMENTS_URL = "https://api.derive.xyz/v3/public/get_all_instruments"


def fetch_option_instrument(currency: str) -> Dict[str, str]:
    """One live option instrument of the currency as Derive describes it (all options of a currency share these settings)."""
    body = json.dumps({"currency": currency, "instrument_type": "option", "expired": False, "page": 1, "page_size": 1}).encode()
    request = urllib.request.Request(INSTRUMENTS_URL, data=body, headers={"Content-Type": "application/json", "User-Agent": "research-client"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)["result"]["instruments"][0]


def differences(venues: Mapping[str, Venue], fetch: Callable[[str], Dict[str, str]]) -> List[str]:
    """What differs between the constants and the exchange, one plain sentence each (empty when everything agrees)."""
    found: List[str] = []
    for currency, venue in venues.items():
        instrument = fetch(currency)
        fees = venue.taker_fees
        checks = (("minimum order", venue.minimum_amount, instrument["minimum_amount"]), ("amount step", venue.amount_step, instrument["amount_step"]),
                  ("base fee", fees.base_fee_per_order_usd, instrument["base_fee"]), ("taker fee rate", fees.rate_of_notional, instrument["taker_fee_rate"]),
                  ("fee cap", fees.cap_fraction_of_premium, instrument["mark_price_fee_rate_cap"]))
        for name, ours, theirs in checks:
            if abs(float(theirs) - ours) > 1e-12:
                found.append(f"{currency} {name}: the code has {ours:g}, Derive says {float(theirs):g}")
    return found


def main(argv: Optional[List[str]] = None) -> int:
    """Check the constants against the API; returns the exit code."""
    init_logging(task_name="check_derive_venue", log_to_file=False)
    problems = differences(DERIVE_VENUES, fetch_option_instrument)
    for problem in problems:
        logger.error(problem)
    if not problems:
        logger.info("The venue constants agree with Derive's API.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
