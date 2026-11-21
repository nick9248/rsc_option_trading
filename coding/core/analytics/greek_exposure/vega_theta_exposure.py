"""
Vega and theta exposure of the open interest of one expiry.

Vega is the change in value for one volatility point, theta the change in value per day, both in USD per contract (Deribit
convention, a contract is one coin). The exposure is the sum over the options of open interest x greek.

This is a convention, not a measured position: open interest has a long and a short side for every contract. "Holder side" assumes
the holders are long every option (vega positive, theta negative); "assumed dealer" is the opposite sign, the same unverified
convention as the dealer gamma (dealers short what the holders hold).
"""

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional


@dataclass(frozen=True)
class VegaThetaExposure:
    """Vega and theta exposure of one expiry (holder side; the assumed dealer is the negative)."""

    holder_vega_usd_per_vol_point: float
    holder_theta_usd_per_day: float
    instruments_used: int
    instruments_missing: int
    """Instruments with open interest but no vega or theta (no mark IV, expired): left out, counted here, never counted as 0."""

    @property
    def dealer_vega_usd_per_vol_point(self) -> float:
        """Assumed dealer vega: the negative of the holder side."""
        return -self.holder_vega_usd_per_vol_point

    @property
    def dealer_theta_usd_per_day(self) -> float:
        """Assumed dealer theta: the negative of the holder side."""
        return -self.holder_theta_usd_per_day


def calculate_vega_theta_exposure(instruments: Iterable[Dict[str, Any]]) -> Optional[VegaThetaExposure]:
    """
    Sum open interest x vega and open interest x theta over the instruments of one expiry.

    Args:
        instruments: Greeks-enriched instrument dicts with "open_interest", "vega" and "theta".

    Returns:
        The exposure, or None when no instrument with open interest has both greeks (nothing to sum is not an exposure of 0).
    """
    vega_total = theta_total = 0.0
    used = missing = 0
    for instrument in instruments:
        open_interest = instrument.get("open_interest") or 0
        if open_interest <= 0:
            continue
        vega, theta = instrument.get("vega"), instrument.get("theta")
        if vega is None or theta is None:
            missing += 1
            continue
        vega_total += open_interest * vega
        theta_total += open_interest * theta
        used += 1
    if used == 0:
        return None
    return VegaThetaExposure(vega_total, theta_total, used, missing)
