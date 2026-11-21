"""
How much may be risked and what size that allows (pure).

The hard limit is the maximum drawdown of the account. A single trade may lose at most the drawdown limit divided by the number of full
losses in a row that the limit should survive (four by default). A defined-risk structure is sized so that its maximum loss equals that
budget; an exchange minimum order size can make that impossible on a small account, which is reported, never rounded up.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RiskBudget:
    """The account and its limits."""

    capital_usd: float
    max_drawdown_fraction: float = 0.20
    losses_in_a_row: int = 4
    """The number of full losses in a row the drawdown limit must survive."""

    def __post_init__(self) -> None:
        if self.capital_usd <= 0 or not 0 < self.max_drawdown_fraction < 1 or self.losses_in_a_row < 1:
            raise ValueError("capital must be positive, the drawdown fraction between 0 and 1, the losses in a row at least 1")

    @property
    def risk_per_trade_usd(self) -> float:
        """The most one trade may lose."""
        return self.capital_usd * self.max_drawdown_fraction / self.losses_in_a_row


@dataclass(frozen=True)
class SizedPosition:
    """The size of a trade under a budget."""

    quantity: float
    """In coins; 0 when the trade does not fit."""
    max_loss_usd: float
    feasible: bool
    reason: str


def size_position(max_loss_per_coin_usd: float, budget: RiskBudget, minimum_quantity: Optional[float] = None) -> SizedPosition:
    """
    The quantity whose maximum loss equals the risk per trade.

    Args:
        max_loss_per_coin_usd: Maximum loss of one coin of the structure (positive).
        budget: The account and its limits.
        minimum_quantity: The exchange's minimum order size in coins (0.1 for BTC options, 1 for ETH options on Deribit); None for
            research on a notional account where any fraction of a coin is allowed.

    Raises:
        ValueError: for a maximum loss that is not positive.
    """
    if max_loss_per_coin_usd <= 0:
        raise ValueError("a defined-risk structure has a positive maximum loss")
    quantity = budget.risk_per_trade_usd / max_loss_per_coin_usd
    if minimum_quantity is not None and quantity < minimum_quantity:
        loss_at_minimum = minimum_quantity * max_loss_per_coin_usd
        return SizedPosition(0.0, loss_at_minimum, False,
                             f"the minimum order of {minimum_quantity:g} coins risks {loss_at_minimum:,.2f} USD, more than the "
                             f"{budget.risk_per_trade_usd:,.2f} USD allowed per trade")
    return SizedPosition(quantity, quantity * max_loss_per_coin_usd, True, "")
