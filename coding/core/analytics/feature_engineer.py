"""
Feature engineering for ML-based options trading decisions.

Converts raw options data into ML-ready features based on research findings:
- Flow-based GEX/DEX (from trade direction)
- VRP (IV - RV)
- Rate of change features (ΔGEX, ΔDEX, ΔIV, ΔVRP)
- Volume and OI metrics
- Price action features (returns, volatility)
"""

import logging
import numpy as np
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


@dataclass
class OptionsFeatureSet:
    """
    Complete feature set for options ML model.

    Features are grouped by category for interpretability.
    """
    # Timestamp
    timestamp: datetime
    currency: str
    expiration: str

    # Flow-based GEX/DEX features
    total_net_gex: float
    total_net_dex: float
    call_resistance_strike: Optional[float]
    put_support_strike: Optional[float]
    hvl_strike: Optional[float]
    gex_skew: float  # (call GEX - put GEX) / total GEX

    # VRP features
    implied_volatility: float
    realized_volatility: float
    vrp_absolute: float  # IV - RV
    vrp_percentage: float  # (IV - RV) / RV * 100
    iv_percentile: float  # IV rank over lookback period

    # Rate of change features (delta from previous snapshot)
    delta_gex: float
    delta_dex: float
    delta_iv: float
    delta_vrp: float
    delta_oi: float
    delta_volume: float

    # Volume and OI features
    total_call_oi: float
    total_put_oi: float
    put_call_ratio_oi: float
    total_call_volume: float
    total_put_volume: float
    put_call_ratio_volume: float
    oi_concentration: float  # Herfindahl index of OI by strike

    # Price action features
    underlying_price: float
    price_return_1d: float  # 1-day return
    price_return_7d: float  # 7-day return
    price_volatility_7d: float  # 7-day realized vol
    price_momentum: float  # 7-day momentum (price / SMA)

    # Distance to key levels
    distance_to_max_pain_pct: float  # (price - max_pain) / price * 100
    distance_to_call_resistance_pct: float
    distance_to_put_support_pct: float
    distance_to_hvl_pct: float

    # Time to expiration
    days_to_expiration: float
    hours_to_expiration: float

    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization."""
        return asdict(self)

    def to_array(self, exclude_metadata: bool = True) -> np.ndarray:
        """
        Convert to numpy array for ML models.

        Args:
            exclude_metadata: Exclude timestamp, currency, expiration (default: True).

        Returns:
            1D numpy array of feature values.
        """
        data = self.to_dict()

        if exclude_metadata:
            # Remove non-numeric metadata fields
            data.pop("timestamp", None)
            data.pop("currency", None)
            data.pop("expiration", None)

        # Convert None values to NaN
        values = [float(v) if v is not None else np.nan for v in data.values()]

        return np.array(values)

    @staticmethod
    def feature_names(exclude_metadata: bool = True) -> List[str]:
        """
        Get feature names in order matching to_array().

        Args:
            exclude_metadata: Exclude timestamp, currency, expiration.

        Returns:
            List of feature name strings.
        """
        dummy = OptionsFeatureSet(
            timestamp=datetime.now(),
            currency="BTC",
            expiration="01JAN26",
            total_net_gex=0.0,
            total_net_dex=0.0,
            call_resistance_strike=None,
            put_support_strike=None,
            hvl_strike=None,
            gex_skew=0.0,
            implied_volatility=0.0,
            realized_volatility=0.0,
            vrp_absolute=0.0,
            vrp_percentage=0.0,
            iv_percentile=0.0,
            delta_gex=0.0,
            delta_dex=0.0,
            delta_iv=0.0,
            delta_vrp=0.0,
            delta_oi=0.0,
            delta_volume=0.0,
            total_call_oi=0.0,
            total_put_oi=0.0,
            put_call_ratio_oi=0.0,
            total_call_volume=0.0,
            total_put_volume=0.0,
            put_call_ratio_volume=0.0,
            oi_concentration=0.0,
            underlying_price=0.0,
            price_return_1d=0.0,
            price_return_7d=0.0,
            price_volatility_7d=0.0,
            price_momentum=0.0,
            distance_to_max_pain_pct=0.0,
            distance_to_call_resistance_pct=0.0,
            distance_to_put_support_pct=0.0,
            distance_to_hvl_pct=0.0,
            days_to_expiration=0.0,
            hours_to_expiration=0.0,
        )

        names = list(dummy.to_dict().keys())

        if exclude_metadata:
            names = [n for n in names if n not in ["timestamp", "currency", "expiration"]]

        return names


class FeatureEngineer:
    """
    Engineer ML-ready features from options data.

    Combines multiple data sources (GEX/DEX, VRP, OI, volume, price) into
    a unified feature set for machine learning models.
    """

    def __init__(self):
        """Initialize feature engineer."""
        self.previous_snapshot: Optional[OptionsFeatureSet] = None

    def compute_features(
        self,
        currency: str,
        expiration: str,
        gex_dex_result: Dict,
        vrp_result: Dict,
        oi_data: Dict,
        volume_data: Dict,
        price_history: List[Dict],
        max_pain_strike: Optional[float] = None,
        expiration_datetime: Optional[datetime] = None
    ) -> OptionsFeatureSet:
        """
        Compute complete feature set from all data sources.

        Args:
            currency: Currency symbol.
            expiration: Expiration date string.
            gex_dex_result: Flow-based GEX/DEX calculation result.
            vrp_result: VRP calculation result.
            oi_data: Open interest data dict.
            volume_data: Volume data dict.
            price_history: List of price dicts with timestamp and close.
            max_pain_strike: Optional max pain strike price.
            expiration_datetime: Optional expiration datetime for DTE calculation.

        Returns:
            Complete OptionsFeatureSet.
        """
        timestamp = datetime.now()

        # Extract GEX/DEX features
        total_net_gex = gex_dex_result.get("total_net_gex", 0.0)
        total_net_dex = gex_dex_result.get("total_net_dex", 0.0)

        key_levels = gex_dex_result.get("key_levels", {})
        call_resistance = key_levels.get("call_resistance")
        put_support = key_levels.get("put_support")
        hvl = key_levels.get("hvl")

        call_resistance_strike = call_resistance.get("strike") if call_resistance else None
        put_support_strike = put_support.get("strike") if put_support else None
        hvl_strike = hvl

        # Calculate GEX skew
        strike_data = gex_dex_result.get("strike_data", {})
        total_call_gex = sum(
            d.get("call_volume", 0) * d.get("net_gex", 0)
            for d in strike_data.values()
        )
        total_put_gex = sum(
            d.get("put_volume", 0) * d.get("net_gex", 0)
            for d in strike_data.values()
        )
        total_abs_gex = abs(total_call_gex) + abs(total_put_gex)
        gex_skew = (
            (total_call_gex - total_put_gex) / total_abs_gex
            if total_abs_gex > 0 else 0.0
        )

        # Extract VRP features
        implied_volatility = vrp_result.get("implied_volatility", 0.0)
        realized_volatility = vrp_result.get("realized_volatility", 0.0)
        vrp_absolute = vrp_result.get("vrp_absolute", 0.0)
        vrp_percentage = vrp_result.get("vrp_percentage", 0.0)
        iv_percentile = vrp_result.get("iv_percentile", 50.0)

        # Extract OI features
        total_call_oi = oi_data.get("total_call_oi", 0.0)
        total_put_oi = oi_data.get("total_put_oi", 0.0)
        put_call_ratio_oi = (
            total_put_oi / total_call_oi
            if total_call_oi > 0 else 0.0
        )

        # Calculate OI concentration (Herfindahl index)
        oi_by_strike = {}  # Would extract from strike_data
        total_oi = total_call_oi + total_put_oi
        if total_oi > 0:
            oi_shares = [oi / total_oi for oi in oi_by_strike.values()]
            oi_concentration = sum(s ** 2 for s in oi_shares)
        else:
            oi_concentration = 0.0

        # Extract volume features
        total_call_volume = volume_data.get("total_call_volume", 0.0)
        total_put_volume = volume_data.get("total_put_volume", 0.0)
        put_call_ratio_volume = (
            total_put_volume / total_call_volume
            if total_call_volume > 0 else 0.0
        )

        # Compute price action features
        underlying_price = price_history[-1]["close"] if price_history else 0.0

        price_return_1d = self._compute_return(price_history, days=1)
        price_return_7d = self._compute_return(price_history, days=7)
        price_volatility_7d = self._compute_volatility(price_history, days=7)
        price_momentum = self._compute_momentum(price_history, days=7)

        # Distance to key levels
        distance_to_max_pain_pct = (
            (underlying_price - max_pain_strike) / underlying_price * 100
            if max_pain_strike and underlying_price > 0 else 0.0
        )
        distance_to_call_resistance_pct = (
            (underlying_price - call_resistance_strike) / underlying_price * 100
            if call_resistance_strike and underlying_price > 0 else 0.0
        )
        distance_to_put_support_pct = (
            (underlying_price - put_support_strike) / underlying_price * 100
            if put_support_strike and underlying_price > 0 else 0.0
        )
        distance_to_hvl_pct = (
            (underlying_price - hvl_strike) / underlying_price * 100
            if hvl_strike and underlying_price > 0 else 0.0
        )

        # Time to expiration
        if expiration_datetime:
            time_to_exp = expiration_datetime - timestamp
            days_to_expiration = time_to_exp.total_seconds() / (24 * 3600)
            hours_to_expiration = time_to_exp.total_seconds() / 3600
        else:
            days_to_expiration = 0.0
            hours_to_expiration = 0.0

        # Compute delta features (rate of change)
        if self.previous_snapshot:
            delta_gex = total_net_gex - self.previous_snapshot.total_net_gex
            delta_dex = total_net_dex - self.previous_snapshot.total_net_dex
            delta_iv = implied_volatility - self.previous_snapshot.implied_volatility
            delta_vrp = vrp_absolute - self.previous_snapshot.vrp_absolute
            delta_oi = (total_call_oi + total_put_oi) - (
                self.previous_snapshot.total_call_oi + self.previous_snapshot.total_put_oi
            )
            delta_volume = (total_call_volume + total_put_volume) - (
                self.previous_snapshot.total_call_volume + self.previous_snapshot.total_put_volume
            )
        else:
            # First snapshot, no delta available
            delta_gex = 0.0
            delta_dex = 0.0
            delta_iv = 0.0
            delta_vrp = 0.0
            delta_oi = 0.0
            delta_volume = 0.0

        # Create feature set
        features = OptionsFeatureSet(
            timestamp=timestamp,
            currency=currency,
            expiration=expiration,
            total_net_gex=total_net_gex,
            total_net_dex=total_net_dex,
            call_resistance_strike=call_resistance_strike,
            put_support_strike=put_support_strike,
            hvl_strike=hvl_strike,
            gex_skew=gex_skew,
            implied_volatility=implied_volatility,
            realized_volatility=realized_volatility,
            vrp_absolute=vrp_absolute,
            vrp_percentage=vrp_percentage,
            iv_percentile=iv_percentile,
            delta_gex=delta_gex,
            delta_dex=delta_dex,
            delta_iv=delta_iv,
            delta_vrp=delta_vrp,
            delta_oi=delta_oi,
            delta_volume=delta_volume,
            total_call_oi=total_call_oi,
            total_put_oi=total_put_oi,
            put_call_ratio_oi=put_call_ratio_oi,
            total_call_volume=total_call_volume,
            total_put_volume=total_put_volume,
            put_call_ratio_volume=put_call_ratio_volume,
            oi_concentration=oi_concentration,
            underlying_price=underlying_price,
            price_return_1d=price_return_1d,
            price_return_7d=price_return_7d,
            price_volatility_7d=price_volatility_7d,
            price_momentum=price_momentum,
            distance_to_max_pain_pct=distance_to_max_pain_pct,
            distance_to_call_resistance_pct=distance_to_call_resistance_pct,
            distance_to_put_support_pct=distance_to_put_support_pct,
            distance_to_hvl_pct=distance_to_hvl_pct,
            days_to_expiration=days_to_expiration,
            hours_to_expiration=hours_to_expiration,
        )

        # Store for next delta calculation
        self.previous_snapshot = features

        logger.info(
            f"Computed features for {currency} {expiration}: "
            f"GEX={total_net_gex:.2f}, VRP={vrp_percentage:.1f}%, "
            f"IV_rank={iv_percentile:.0f}%"
        )

        return features

    def _compute_return(self, price_history: List[Dict], days: int) -> float:
        """Compute N-day return."""
        if len(price_history) < days + 1:
            return 0.0

        current = price_history[-1]["close"]
        past = price_history[-(days + 1)]["close"]

        return (current - past) / past if past > 0 else 0.0

    def _compute_volatility(self, price_history: List[Dict], days: int) -> float:
        """Compute N-day realized volatility."""
        if len(price_history) < days + 1:
            return 0.0

        prices = [p["close"] for p in price_history[-(days + 1):]]
        returns = [
            np.log(prices[i] / prices[i - 1])
            for i in range(1, len(prices))
        ]

        if not returns:
            return 0.0

        return np.std(returns) * np.sqrt(365)  # Annualized

    def _compute_momentum(self, price_history: List[Dict], days: int) -> float:
        """Compute N-day price momentum (price / SMA)."""
        if len(price_history) < days:
            return 1.0

        prices = [p["close"] for p in price_history[-days:]]
        sma = np.mean(prices)

        current = price_history[-1]["close"]

        return current / sma if sma > 0 else 1.0
