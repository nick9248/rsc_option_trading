"""
Black-Scholes Greeks calculator.

Calculates option Greeks (Delta, Gamma, Theta, Vega, Rho) from implied volatility.
Used for historical data where exchange Greeks are not available.
"""

import logging
import math
from typing import Any, Dict, Optional
from datetime import datetime

from coding.core.analytics.market_wide_calculator import DERIBIT_SETTLEMENT_HOUR_UTC

logger = logging.getLogger(__name__)

# Task Wave-J-A Fix 1/2 (moved here from on_chain_analysis_service.py's
# private _MAX_SANE_MARK_IV_PCT, Task G2-A re-review): a single sane upper
# ceiling on mark_iv, shared by every caller of calculate_greeks_safe below
# so the daemon path (ProspectiveCollector._enrich_with_greeks) and the
# report/GUI path (OnChainAnalysisService._compute_bs_gamma) can never
# silently drift apart on what counts as "absurd" IV. 1000% annualized IV
# is already far beyond anything Deribit's real book has shown even during
# extreme crypto vol spikes (typical peak is low hundreds of percent) -- a
# documented, generous ceiling, not a tuned statistical bound.
MAX_SANE_MARK_IV_PCT = 1000.0


class BlackScholesCalculator:
    """
    Calculate option Greeks using Black-Scholes model.

    This is used for historical data backfill where we have IV but not Greeks.
    For live trading, prefer exchange-provided Greeks (mark_greeks).
    """

    def __init__(self, risk_free_rate: float = 0.0):
        """
        Initialize Black-Scholes calculator.

        Args:
            risk_free_rate: Risk-free interest rate (default: 0.0 for crypto)
        """
        self.risk_free_rate = risk_free_rate

    def calculate_greeks(
        self,
        spot_price: float,
        strike_price: float,
        time_to_expiry: float,
        implied_volatility: float,
        option_type: str
    ) -> Dict[str, float]:
        """
        Calculate all Greeks for an option.

        Args:
            spot_price: Current underlying price
            strike_price: Option strike price
            time_to_expiry: Time to expiration in years
            implied_volatility: Implied volatility (as decimal, e.g., 0.80 for 80%)
            option_type: "call" or "put"

        Returns:
            Dictionary with delta, gamma, theta, vega, rho
        """
        if time_to_expiry <= 0:
            # Expired option
            return self._expired_option_greeks(spot_price, strike_price, option_type)

        if implied_volatility <= 0:
            logger.warning(f"Invalid IV: {implied_volatility}, using 0.01")
            implied_volatility = 0.01

        try:
            # Calculate d1 and d2
            d1 = self._calculate_d1(
                spot_price, strike_price, time_to_expiry,
                implied_volatility, self.risk_free_rate
            )
            d2 = d1 - implied_volatility * math.sqrt(time_to_expiry)

            # Calculate Greeks
            delta = self._calculate_delta(d1, d2, option_type)
            gamma = self._calculate_gamma(spot_price, d1, implied_volatility, time_to_expiry)
            theta = self._calculate_theta(
                spot_price, strike_price, d1, d2,
                implied_volatility, time_to_expiry, option_type
            )
            vega = self._calculate_vega(spot_price, d1, time_to_expiry)
            rho = self._calculate_rho(strike_price, d2, time_to_expiry, option_type)

            return {
                "delta": delta,
                "gamma": gamma,
                "theta": theta,
                "vega": vega,
                "rho": rho
            }

        except Exception as e:
            logger.error(f"Error calculating Greeks: {e}")
            return {
                "delta": 0.0,
                "gamma": 0.0,
                "theta": 0.0,
                "vega": 0.0,
                "rho": 0.0
            }

    def calculate_greeks_safe(
        self,
        item: Dict[str, Any],
        mark_iv: Optional[float],
        spot_price: Optional[float],
        now_utc: datetime,
        max_sane_mark_iv_pct: float = MAX_SANE_MARK_IV_PCT,
    ) -> Optional[Dict[str, float]]:
        """
        Task Wave-J-A (Fix 1 / Fix 2): the single, shared, rigorously-
        guarded entry point for computing Black-Scholes Greeks from
        ``mark_iv`` -- extracted from ``OnChainAnalysisService.
        _compute_bs_gamma`` (Task G2-A / Wave G re-review) so that method
        and ``ProspectiveCollector._enrich_with_greeks`` (the daemon path)
        share exactly one implementation of this guard logic instead of
        two that can drift apart.

        Every guard below is an explicit numeric range check (``is None or
        <= 0``), never a bare truthiness check (``not x``) -- ``not
        -50000`` is ``False`` in Python, so a truthiness guard would
        silently let a negative strike/mark_iv straight through to
        ``calculate_greeks``, whose blanket ``except Exception`` then
        returns an all-zero-greeks dict that would look like "computed
        successfully" to any caller that doesn't check for it.

        Returns:
            The full Greeks dict from ``calculate_greeks``, or ``None``
            (never a fabricated 0.0 -- a real "could not compute" case,
            not "zero exposure") if ``mark_iv``/``strike``/``spot_price``/
            the instrument name are missing, non-positive, unparseable,
            or outside a sane range (``max_sane_mark_iv_pct``), or the
            option has already passed its 08:00 UTC settlement
            (``time_to_expiry <= 0``). A ``None`` return is exactly the
            "missing gamma"/"missing delta" case
            ``GexDexCalculator._aggregate_by_strike``'s completeness
            tracking (Task G2-A, bug 2) is built to detect.
        """
        if spot_price is None or spot_price <= 0:
            return None

        if mark_iv is None or mark_iv <= 0 or mark_iv > max_sane_mark_iv_pct:
            return None

        strike = item.get("strike")
        name = item.get("instrument_name", "")
        if strike is None or strike <= 0 or not name:
            return None

        parsed = self.parse_instrument_name(name)
        if parsed is None:
            return None

        now_utc_naive = now_utc.replace(tzinfo=None) if now_utc.tzinfo is not None else now_utc
        time_to_expiry = self.calculate_time_to_expiry(now_utc_naive, parsed["expiry_time"])
        if time_to_expiry <= 0:
            return None

        return self.calculate_greeks(
            spot_price=float(spot_price),
            strike_price=float(strike),
            time_to_expiry=time_to_expiry,
            implied_volatility=float(mark_iv) / 100.0,
            option_type=parsed["option_type"],
        )

    def _calculate_d1(
        self,
        spot: float,
        strike: float,
        time: float,
        vol: float,
        rate: float
    ) -> float:
        """Calculate d1 parameter for Black-Scholes."""
        numerator = math.log(spot / strike) + (rate + 0.5 * vol ** 2) * time
        denominator = vol * math.sqrt(time)
        return numerator / denominator

    def _calculate_delta(self, d1: float, d2: float, option_type: str) -> float:
        """
        Calculate Delta (rate of change of option price with respect to spot).

        Delta ranges from 0 to 1 for calls, -1 to 0 for puts.
        """
        if option_type.lower() == "call":
            return self._norm_cdf(d1)
        else:  # put
            return self._norm_cdf(d1) - 1.0

    def _calculate_gamma(
        self,
        spot: float,
        d1: float,
        vol: float,
        time: float
    ) -> float:
        """
        Calculate Gamma (rate of change of Delta with respect to spot).

        Gamma is same for calls and puts (always positive).
        """
        numerator = self._norm_pdf(d1)
        denominator = spot * vol * math.sqrt(time)
        return numerator / denominator

    def _calculate_theta(
        self,
        spot: float,
        strike: float,
        d1: float,
        d2: float,
        vol: float,
        time: float,
        option_type: str
    ) -> float:
        """
        Calculate Theta (rate of change of option price with respect to time).

        Theta is typically negative (time decay). Expressed per day.
        """
        sqrt_time = math.sqrt(time)
        discount = math.exp(-self.risk_free_rate * time)

        # First term (common for both)
        first_term = -(spot * self._norm_pdf(d1) * vol) / (2 * sqrt_time)

        if option_type.lower() == "call":
            second_term = self.risk_free_rate * strike * discount * self._norm_cdf(d2)
            theta = first_term - second_term
        else:  # put
            second_term = self.risk_free_rate * strike * discount * self._norm_cdf(-d2)
            theta = first_term + second_term

        # Convert from per year to per day
        return theta / 365.0

    def _calculate_vega(self, spot: float, d1: float, time: float) -> float:
        """
        Calculate Vega (rate of change of option price with respect to volatility).

        Vega is same for calls and puts (always positive).
        Expressed per 1% change in IV.
        """
        vega = spot * self._norm_pdf(d1) * math.sqrt(time)
        # Convert from per 1.0 change to per 0.01 change (1%)
        return vega / 100.0

    def _calculate_rho(
        self,
        strike: float,
        d2: float,
        time: float,
        option_type: str
    ) -> float:
        """
        Calculate Rho (rate of change of option price with respect to interest rate).

        Less important for crypto (rates near zero).
        Expressed per 1% change in rate.
        """
        discount = math.exp(-self.risk_free_rate * time)

        if option_type.lower() == "call":
            rho = strike * time * discount * self._norm_cdf(d2)
        else:  # put
            rho = -strike * time * discount * self._norm_cdf(-d2)

        # Convert from per 1.0 change to per 0.01 change (1%)
        return rho / 100.0

    def _norm_cdf(self, x: float) -> float:
        """
        Cumulative distribution function for standard normal distribution.

        Uses approximation for computational efficiency.
        """
        return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0

    def _norm_pdf(self, x: float) -> float:
        """
        Probability density function for standard normal distribution.
        """
        return math.exp(-0.5 * x ** 2) / math.sqrt(2.0 * math.pi)

    @staticmethod
    def _erfinv(x: float) -> float:
        """Approximate inverse error function (Winitzki 2003). Accuracy < 5e-4."""
        x = max(-1.0 + 1e-9, min(1.0 - 1e-9, x))
        a = 0.147
        ln_term = math.log(1.0 - x * x)
        b = 2.0 / (math.pi * a) + ln_term / 2.0
        return math.copysign(math.sqrt(math.sqrt(b * b - ln_term / a) - b), x)

    def d1_from_delta(self, delta: float, option_type: str) -> float:
        """
        Recover d1 from delta using inverse normal CDF.

        Call: Δ = N(d1) → d1 = √2 · erfinv(2Δ − 1)
        Put:  Δ = N(d1) − 1 → d1 = √2 · erfinv(2(Δ+1) − 1)
        """
        if option_type.upper() in ("C", "CALL"):
            p = max(1e-9, min(1.0 - 1e-9, float(delta)))
        else:
            p = max(1e-9, min(1.0 - 1e-9, float(delta) + 1.0))
        return math.sqrt(2.0) * self._erfinv(2.0 * p - 1.0)

    def calculate_vanna(self, d1: float, d2: float, implied_volatility: float) -> float:
        """
        Vanna = ∂²V/∂S∂σ = ∂Δ/∂σ

        Closed-form BS (r=q=0): −φ(d1) × d2 / σ
        Same for calls and puts.
        """
        if implied_volatility <= 0:
            return 0.0
        return -self._norm_pdf(d1) * d2 / implied_volatility

    def calculate_charm(self, d1: float, d2: float, time_to_expiry: float) -> float:
        """
        bugfix_spec.md Item 12: the sign LABEL below was wrong; the
        returned VALUE was always correct (verified by finite-difference
        against real delta decay) -- documentation-only fix.

        Charm = ∂Δ/∂t -- the rate of change of delta with respect to
        ELAPSING calendar time (equivalently −∂Δ/∂τ, where τ is time
        REMAINING -- NOT ∂Δ/∂τ itself, which has the opposite sign).

        Closed-form BS (r=q=0): +φ(d1) × d2 / (2τ)
        Derivation: ∂d1/∂τ = −d2/(2τ)  ⇒  ∂Δ/∂τ = −φ(d1)·d2/(2τ)  ⇒
        ∂Δ/∂t = +φ(d1)·d2/(2τ). Same for calls and puts.

        Units: per YEAR. Divide by 365 for the per-day delta drift.
        """
        if time_to_expiry <= 0:
            return 0.0
        return self._norm_pdf(d1) * d2 / (2.0 * time_to_expiry)

    def _expired_option_greeks(
        self,
        spot: float,
        strike: float,
        option_type: str
    ) -> Dict[str, float]:
        """
        Greeks for expired option (all zero except intrinsic delta).
        """
        if option_type.lower() == "call":
            delta = 1.0 if spot > strike else 0.0
        else:
            delta = -1.0 if spot < strike else 0.0

        return {
            "delta": delta,
            "gamma": 0.0,
            "theta": 0.0,
            "vega": 0.0,
            "rho": 0.0
        }

    def calculate_time_to_expiry(
        self,
        current_time: datetime,
        expiry_time: datetime
    ) -> float:
        """
        Calculate time to expiry in years.

        Args:
            current_time: Current datetime
            expiry_time: Expiration datetime

        Returns:
            Time to expiry in years (fraction)
        """
        delta = expiry_time - current_time
        hours = delta.total_seconds() / 3600
        years = hours / (24 * 365)
        return max(0.0, years)

    def parse_instrument_name(self, instrument_name: str) -> Optional[Dict[str, any]]:
        """
        Parse Deribit instrument name to extract components.

        Format: BTC-27MAR26-100000-C (Currency-Expiry-Strike-Type)

        Args:
            instrument_name: Deribit instrument name

        Returns:
            Dictionary with currency, expiry, strike, option_type
            Returns None if parsing fails
        """
        try:
            parts = instrument_name.split("-")
            if len(parts) != 4:
                return None

            currency = parts[0]
            expiry_str = parts[1]
            strike = float(parts[2])
            option_type = "call" if parts[3] == "C" else "put"

            # Parse expiry date (e.g., "27MAR26" or "7FEB26" -> "2026-03-27" or "2026-02-07")
            # Format: [D]DMONTHYY where D is 1-2 digits, MONTH is 3 letters, YY is 2 digits
            year = int("20" + expiry_str[-2:])  # Last 2 chars are year
            month_str = expiry_str[-5:-2]  # 3 chars before year are month
            day = int(expiry_str[:-5])  # Everything before month is day

            month_map = {
                "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4,
                "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8,
                "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12
            }
            month = month_map.get(month_str)

            if not month:
                return None

            # Deribit options expire at 08:00 UTC
            expiry_time = datetime(year, month, day, DERIBIT_SETTLEMENT_HOUR_UTC, 0, 0)

            return {
                "currency": currency,
                "expiry_time": expiry_time,
                "strike": strike,
                "option_type": option_type
            }

        except Exception as e:
            logger.warning(f"Failed to parse instrument {instrument_name}: {e}")
            return None
