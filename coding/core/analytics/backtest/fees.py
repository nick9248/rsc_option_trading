"""
Deribit option fees, in USD per contract of one coin.

Trading fee: 0.03% of the index price per contract, at most 12.5% of the option's price. Delivery (settlement) fee: 0.015% of the
settlement price, at most 12.5% of the option's value, charged on options that settle in the money. These are the published standard
rates as found in third-party summaries; Deribit's own fee page was not fully readable, and a waiver of settlement fees exists for some
accounts. The rates are constants here so that a result can be re-run with the real schedule of the account.
"""

TRADING_FEE_RATE = 0.0003
DELIVERY_FEE_RATE = 0.00015
FEE_CAP_FRACTION_OF_OPTION_VALUE = 0.125


def trading_fee_usd(index_price: float, option_price_usd: float) -> float:
    """Fee of one fill of one contract: the rate times the index price, at most 12.5% of the option price paid or received."""
    return min(TRADING_FEE_RATE * index_price, FEE_CAP_FRACTION_OF_OPTION_VALUE * option_price_usd)


def delivery_fee_usd(settlement_price: float, intrinsic_value_usd: float) -> float:
    """Fee of one contract that settles with the given intrinsic value; 0 for an option that expires worthless."""
    if intrinsic_value_usd <= 0:
        return 0.0
    return min(DELIVERY_FEE_RATE * settlement_price, FEE_CAP_FRACTION_OF_OPTION_VALUE * intrinsic_value_usd)
