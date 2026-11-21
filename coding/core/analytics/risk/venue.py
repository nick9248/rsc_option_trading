"""
The trading venue the positions are placed on: its minimum order size, size step and fees (pure).

Prices come from the Deribit chain the collector stores (a fair proxy: at matching strikes Derive's spreads were 0.86 to 0.92 times Deribit's on
2026-11-21), but the positions are traded on Derive, whose fees and minimum order size differ. Source: Derive docs
(docs.derive.xyz/integrators/trading/trading-fees) and ``public/get_all_instruments``, read 2026-11-21.

Fees modelled: the taker schedule of an orderbook order, one order per leg, no tier discount, no RFQ discount (an RFQ would waive the fee on the
cheaper leg of a spread and charge the base fee once: costs here are an upper bound). A maker order has no base fee and a lower rate; it is not
modelled because a resting order may not be filled. The delivery fee is not confirmed for Derive: the Deribit rate is used in the expected result.
"""

import math
from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class FeeSchedule:
    """The fee of one order of one option: a base fee plus a rate of the notional, at most a fraction of the premium."""

    base_fee_per_order_usd: float
    rate_of_notional: float
    cap_fraction_of_premium: float

    def fee_per_coin_usd(self, index_price: float, option_price_usd: float, quantity: float) -> float:
        """The fee of one order of ``quantity`` coins, divided by the quantity (so per coin, like the other numbers of a candidate)."""
        if quantity <= 0:
            raise ValueError("the quantity must be positive")
        return self.base_fee_per_order_usd / quantity + min(self.rate_of_notional * index_price, self.cap_fraction_of_premium * option_price_usd)


@dataclass(frozen=True)
class Venue:
    """Where positions are placed."""

    name: str
    minimum_amount: float
    """Smallest order, in coins; a position below it cannot be placed."""
    amount_step: float
    taker_fees: FeeSchedule

    def floor_to_step(self, quantity: float) -> float:
        """The largest allowed size not above ``quantity`` (never rounded up)."""
        return round(math.floor(quantity / self.amount_step + 1e-9) * self.amount_step, 10)


DERIVE_TAKER_FEES = FeeSchedule(base_fee_per_order_usd=0.5, rate_of_notional=0.0003, cap_fraction_of_premium=0.125)

DERIVE_VENUES: Mapping[str, Venue] = {
    "BTC": Venue("Derive BTC options", minimum_amount=0.01, amount_step=0.00001, taker_fees=DERIVE_TAKER_FEES),
    "ETH": Venue("Derive ETH options", minimum_amount=0.1, amount_step=0.01, taker_fees=DERIVE_TAKER_FEES),
}
