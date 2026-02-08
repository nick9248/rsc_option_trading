"""
Feature engineering pipeline for ML regime detection.

Extracts 100+ features from multiple data sources:
- Technical indicators (25)
- On-chain metrics (30)
- Sentiment signals (10)
- Market structure (20)
- Derived features (15)
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class FeatureVector:
    """Container for feature vector with metadata."""

    def __init__(self, features: Dict[str, float], timestamp: datetime, currency: str):
        """
        Initialize feature vector.

        Args:
            features: Dictionary of feature name -> value
            timestamp: Timestamp for this feature vector
            currency: Currency (BTC or ETH)
        """
        self.features = features
        self.timestamp = timestamp
        self.currency = currency

    def to_array(self) -> np.ndarray:
        """Convert to numpy array (ordered by feature names)."""
        feature_names = sorted(self.features.keys())
        return np.array([self.features[name] for name in feature_names])

    def to_dict(self) -> Dict:
        """Convert to dictionary with metadata."""
        return {
            "features": self.features,
            "timestamp": self.timestamp.isoformat(),
            "currency": self.currency
        }

    @staticmethod
    def concat(vectors: List['FeatureVector']) -> 'FeatureVector':
        """
        Concatenate multiple feature vectors.

        Args:
            vectors: List of FeatureVector objects

        Returns:
            Combined FeatureVector
        """
        if not vectors:
            raise ValueError("Cannot concatenate empty list")

        combined_features = {}
        for vector in vectors:
            combined_features.update(vector.features)

        # Use timestamp and currency from first vector
        return FeatureVector(
            features=combined_features,
            timestamp=vectors[0].timestamp,
            currency=vectors[0].currency
        )


class FeatureEngineer:
    """
    Extract and engineer features for ML regime detection.

    Transforms raw market data into ML-ready feature vectors.
    """

    def __init__(self):
        """Initialize feature engineer."""
        self.feature_names = None  # Will be set after first extraction

    def extract_features(
        self,
        currency: str,
        timestamp: datetime,
        market_data: Dict
    ) -> FeatureVector:
        """
        Extract 100+ features from multiple data sources.

        Args:
            currency: Currency (BTC or ETH)
            timestamp: Current timestamp
            market_data: Dictionary with keys:
                - "ohlcv": OHLC + volume data
                - "technical": Technical indicators
                - "onchain": On-chain metrics
                - "sentiment": Sentiment signals
                - "options": Options market data

        Returns:
            FeatureVector with all extracted features
        """
        try:
            # Extract features from each domain
            technical = self._extract_technical(market_data.get("technical", {}))
            onchain = self._extract_onchain(market_data.get("onchain", {}))
            sentiment = self._extract_sentiment(market_data.get("sentiment", {}))
            market = self._extract_market_structure(market_data.get("ohlcv", {}), market_data.get("options", {}))
            derived = self._create_derived_features(technical, onchain, sentiment, market)

            # Combine all features
            all_features = {}
            all_features.update(technical.features)
            all_features.update(onchain.features)
            all_features.update(sentiment.features)
            all_features.update(market.features)
            all_features.update(derived.features)

            return FeatureVector(
                features=all_features,
                timestamp=timestamp,
                currency=currency
            )

        except Exception as e:
            logger.error(f"Error extracting features: {e}")
            raise

    def _extract_technical(self, technical_data: Dict) -> FeatureVector:
        """
        Extract technical indicator features (25 features).

        Features:
        - Moving averages (SMA, EMA)
        - Oscillators (RSI, MACD, Stochastic)
        - Trend indicators (ADX, Aroon)
        - Volatility (ATR, Bollinger Bands)
        """
        features = {}

        # Moving averages
        features["sma_50"] = technical_data.get("sma_50", 0.0)
        features["sma_200"] = technical_data.get("sma_200", 0.0)
        features["ema_50"] = technical_data.get("ema_50", 0.0)
        features["ema_200"] = technical_data.get("ema_200", 0.0)

        # Price relative to MAs
        current_price = technical_data.get("close", 0.0)
        if current_price > 0:
            features["price_vs_sma50"] = (current_price - features["sma_50"]) / current_price
            features["price_vs_sma200"] = (current_price - features["sma_200"]) / current_price
        else:
            features["price_vs_sma50"] = 0.0
            features["price_vs_sma200"] = 0.0

        # Golden/Death cross indicator
        if features["sma_50"] > 0 and features["sma_200"] > 0:
            features["ma_cross_signal"] = (features["sma_50"] - features["sma_200"]) / features["sma_200"]
        else:
            features["ma_cross_signal"] = 0.0

        # Oscillators
        features["rsi"] = technical_data.get("rsi", 50.0)  # RSI (0-100)
        features["rsi_normalized"] = (features["rsi"] - 50.0) / 50.0  # Centered at 0

        features["macd"] = technical_data.get("macd", 0.0)
        features["macd_signal"] = technical_data.get("macd_signal", 0.0)
        features["macd_histogram"] = technical_data.get("macd_histogram", 0.0)

        features["stochastic_k"] = technical_data.get("stochastic_k", 50.0)
        features["stochastic_d"] = technical_data.get("stochastic_d", 50.0)

        # Trend strength
        features["adx"] = technical_data.get("adx", 0.0)
        features["adx_di_plus"] = technical_data.get("adx_di_plus", 0.0)
        features["adx_di_minus"] = technical_data.get("adx_di_minus", 0.0)

        # Volatility
        features["atr"] = technical_data.get("atr", 0.0)
        features["atr_percentile"] = technical_data.get("atr_percentile", 50.0)

        features["bb_width"] = technical_data.get("bb_width", 0.0)  # Bollinger Band width
        features["bb_position"] = technical_data.get("bb_position", 0.5)  # Price position in BB (0-1)

        # Volume
        features["volume_sma"] = technical_data.get("volume_sma", 0.0)
        features["volume_ratio"] = technical_data.get("volume_ratio", 1.0)  # Current vs SMA

        # Momentum
        features["roc"] = technical_data.get("roc", 0.0)  # Rate of change
        features["momentum"] = technical_data.get("momentum", 0.0)

        return FeatureVector(
            features=features,
            timestamp=datetime.now(),
            currency=""
        )

    def _extract_onchain(self, onchain_data: Dict) -> FeatureVector:
        """
        Extract on-chain metrics features (30 features).

        Features:
        - GEX/DEX (Gamma/Delta exposure)
        - Max pain distance
        - Put/Call ratio
        - Open interest distribution
        - Volume profile
        - Support/resistance levels
        """
        features = {}

        # GEX/DEX metrics
        features["total_gex"] = onchain_data.get("total_gex", 0.0)
        features["call_gex"] = onchain_data.get("call_gex", 0.0)
        features["put_gex"] = onchain_data.get("put_gex", 0.0)
        features["net_gex"] = onchain_data.get("net_gex", 0.0)

        features["total_dex"] = onchain_data.get("total_dex", 0.0)
        features["call_dex"] = onchain_data.get("call_dex", 0.0)
        features["put_dex"] = onchain_data.get("put_dex", 0.0)
        features["net_dex"] = onchain_data.get("net_dex", 0.0)

        # Normalize by underlying price
        underlying_price = onchain_data.get("underlying_price", 1.0)
        if underlying_price > 0:
            features["gex_per_price"] = features["total_gex"] / underlying_price
            features["dex_per_price"] = features["total_dex"] / underlying_price
        else:
            features["gex_per_price"] = 0.0
            features["dex_per_price"] = 0.0

        # Max pain
        features["max_pain"] = onchain_data.get("max_pain", 0.0)
        features["max_pain_distance"] = onchain_data.get("max_pain_distance", 0.0)
        features["max_pain_distance_pct"] = onchain_data.get("max_pain_distance_pct", 0.0)

        # Put/Call ratios
        features["put_call_ratio_oi"] = onchain_data.get("put_call_ratio_oi", 1.0)
        features["put_call_ratio_volume"] = onchain_data.get("put_call_ratio_volume", 1.0)

        # Open interest
        features["total_oi"] = onchain_data.get("total_oi", 0.0)
        features["call_oi"] = onchain_data.get("call_oi", 0.0)
        features["put_oi"] = onchain_data.get("put_oi", 0.0)

        # OI distribution (by moneyness)
        features["otm_call_oi_pct"] = onchain_data.get("otm_call_oi_pct", 0.0)
        features["itm_call_oi_pct"] = onchain_data.get("itm_call_oi_pct", 0.0)
        features["otm_put_oi_pct"] = onchain_data.get("otm_put_oi_pct", 0.0)
        features["itm_put_oi_pct"] = onchain_data.get("itm_put_oi_pct", 0.0)

        # Gamma/Delta walls (strike levels with high exposure)
        features["call_wall_strike"] = onchain_data.get("call_wall_strike", 0.0)
        features["put_wall_strike"] = onchain_data.get("put_wall_strike", 0.0)
        features["call_wall_distance_pct"] = onchain_data.get("call_wall_distance_pct", 0.0)
        features["put_wall_distance_pct"] = onchain_data.get("put_wall_distance_pct", 0.0)

        # Support/resistance
        features["nearest_support"] = onchain_data.get("nearest_support", 0.0)
        features["nearest_resistance"] = onchain_data.get("nearest_resistance", 0.0)
        features["support_strength"] = onchain_data.get("support_strength", 0.0)
        features["resistance_strength"] = onchain_data.get("resistance_strength", 0.0)

        return FeatureVector(
            features=features,
            timestamp=datetime.now(),
            currency=""
        )

    def _extract_sentiment(self, sentiment_data: Dict) -> FeatureVector:
        """
        Extract sentiment signal features (10 features).

        Features:
        - Fear & Greed Index
        - Funding rate
        - BTC/ETH dominance
        - Social sentiment
        """
        features = {}

        # Fear & Greed Index (0-100)
        features["fear_greed_index"] = sentiment_data.get("fear_greed_index", 50.0)
        features["fear_greed_normalized"] = (features["fear_greed_index"] - 50.0) / 50.0  # -1 to 1

        # Funding rate (perpetual swaps)
        features["funding_rate"] = sentiment_data.get("funding_rate", 0.0)
        features["funding_rate_8h_annualized"] = features["funding_rate"] * 365 * 3  # 8h periods

        # Market dominance
        features["btc_dominance"] = sentiment_data.get("btc_dominance", 0.0)
        features["eth_dominance"] = sentiment_data.get("eth_dominance", 0.0)
        features["altcoin_dominance"] = 100.0 - features["btc_dominance"] - features["eth_dominance"]

        # Social sentiment (if available)
        features["social_sentiment"] = sentiment_data.get("social_sentiment", 0.0)
        features["social_volume"] = sentiment_data.get("social_volume", 0.0)

        # Trend in sentiment
        features["fear_greed_change_24h"] = sentiment_data.get("fear_greed_change_24h", 0.0)

        return FeatureVector(
            features=features,
            timestamp=datetime.now(),
            currency=""
        )

    def _extract_market_structure(self, ohlcv_data: Dict, options_data: Dict) -> FeatureVector:
        """
        Extract market structure features (20 features).

        Features:
        - Volatility regime
        - Trend strength
        - Price momentum
        - Volume profile
        - IV surface metrics
        """
        features = {}

        # Volatility metrics
        features["realized_vol_24h"] = ohlcv_data.get("realized_vol_24h", 0.0)
        features["realized_vol_7d"] = ohlcv_data.get("realized_vol_7d", 0.0)
        features["realized_vol_30d"] = ohlcv_data.get("realized_vol_30d", 0.0)

        # Implied volatility
        features["atm_iv"] = options_data.get("atm_iv", 0.0)
        features["iv_percentile_30d"] = options_data.get("iv_percentile_30d", 50.0)

        # IV vs RV (volatility risk premium)
        features["iv_rv_spread"] = features["atm_iv"] - features["realized_vol_30d"]

        # IV term structure
        features["iv_term_structure_slope"] = options_data.get("iv_term_structure_slope", 0.0)
        features["iv_term_structure_state"] = options_data.get("iv_term_structure_state", 0.0)  # -1=backwardation, 0=flat, 1=contango

        # IV skew
        features["iv_skew_25delta"] = options_data.get("iv_skew_25delta", 0.0)
        features["iv_skew_state"] = options_data.get("iv_skew_state", 0.0)  # Positive=puts more expensive

        # Price momentum
        features["return_1d"] = ohlcv_data.get("return_1d", 0.0)
        features["return_7d"] = ohlcv_data.get("return_7d", 0.0)
        features["return_30d"] = ohlcv_data.get("return_30d", 0.0)

        # Trend consistency
        features["trend_consistency"] = ohlcv_data.get("trend_consistency", 0.0)  # % of periods in same direction

        # Drawdown
        features["drawdown_from_ath"] = ohlcv_data.get("drawdown_from_ath", 0.0)
        features["drawdown_7d"] = ohlcv_data.get("drawdown_7d", 0.0)

        # Volume metrics
        features["volume_24h"] = ohlcv_data.get("volume_24h", 0.0)
        features["volume_change_pct"] = ohlcv_data.get("volume_change_pct", 0.0)

        # Market cap / liquidity
        features["market_cap"] = ohlcv_data.get("market_cap", 0.0)
        features["liquidity_score"] = ohlcv_data.get("liquidity_score", 0.0)

        return FeatureVector(
            features=features,
            timestamp=datetime.now(),
            currency=""
        )

    def _create_derived_features(
        self,
        technical: FeatureVector,
        onchain: FeatureVector,
        sentiment: FeatureVector,
        market: FeatureVector
    ) -> FeatureVector:
        """
        Create derived features from cross-product interactions (15 features).

        These capture non-linear relationships between different feature domains.
        """
        features = {}

        # GEX × RSI (gamma exposure in overbought/oversold territory)
        features["gex_times_rsi"] = onchain.features.get("net_gex", 0.0) * technical.features.get("rsi_normalized", 0.0)

        # P/C Ratio × Momentum (positioning vs price action)
        features["pc_ratio_times_momentum"] = (
            onchain.features.get("put_call_ratio_oi", 1.0) * market.features.get("return_7d", 0.0)
        )

        # Fear & Greed × Volatility (sentiment vs realized vol)
        features["fear_greed_times_vol"] = (
            sentiment.features.get("fear_greed_normalized", 0.0) * market.features.get("realized_vol_7d", 0.0)
        )

        # Funding Rate × Trend (carry cost in trending markets)
        features["funding_times_trend"] = (
            sentiment.features.get("funding_rate", 0.0) * technical.features.get("adx", 0.0)
        )

        # IV-RV Spread × OI (VRP with market positioning)
        features["iv_rv_spread_times_oi"] = (
            market.features.get("iv_rv_spread", 0.0) * onchain.features.get("total_oi", 0.0)
        )

        # Max Pain Distance × Volume (dealer positioning pressure)
        features["maxpain_dist_times_volume"] = (
            onchain.features.get("max_pain_distance_pct", 0.0) * market.features.get("volume_change_pct", 0.0)
        )

        # RSI × ATR (momentum in volatile conditions)
        features["rsi_times_atr"] = (
            technical.features.get("rsi_normalized", 0.0) * technical.features.get("atr_percentile", 0.0)
        )

        # GEX × Volatility (gamma exposure amplifies moves)
        features["gex_times_realized_vol"] = (
            onchain.features.get("net_gex", 0.0) * market.features.get("realized_vol_7d", 0.0)
        )

        # Term structure × Skew (IV surface shape)
        features["term_structure_times_skew"] = (
            market.features.get("iv_term_structure_slope", 0.0) * market.features.get("iv_skew_25delta", 0.0)
        )

        # Trend strength × Volume (confirmed trends)
        features["adx_times_volume_ratio"] = (
            technical.features.get("adx", 0.0) * technical.features.get("volume_ratio", 1.0)
        )

        # Drawdown × Fear (stress indicators)
        features["drawdown_times_fear"] = (
            market.features.get("drawdown_7d", 0.0) * (50.0 - sentiment.features.get("fear_greed_index", 50.0))
        )

        # BTC Dominance × ETH specific (for ETH models)
        features["btc_dominance_normalized"] = sentiment.features.get("btc_dominance", 50.0) - 50.0

        # OI imbalance (calls vs puts)
        call_oi = onchain.features.get("call_oi", 0.0)
        put_oi = onchain.features.get("put_oi", 0.0)
        total_oi = call_oi + put_oi
        if total_oi > 0:
            features["oi_imbalance"] = (call_oi - put_oi) / total_oi
        else:
            features["oi_imbalance"] = 0.0

        # Gamma wall proximity (distance to nearest gamma barrier)
        features["nearest_gamma_wall_distance"] = min(
            abs(onchain.features.get("call_wall_distance_pct", 100.0)),
            abs(onchain.features.get("put_wall_distance_pct", 100.0))
        )

        # Momentum consistency (trend × return alignment)
        adx = technical.features.get("adx", 0.0)
        return_7d = market.features.get("return_7d", 0.0)
        features["momentum_consistency"] = adx * abs(return_7d)

        return FeatureVector(
            features=features,
            timestamp=datetime.now(),
            currency=""
        )

    def get_feature_names(self) -> List[str]:
        """Get list of all feature names (sorted)."""
        if self.feature_names is None:
            # Extract once to determine all feature names
            dummy_data = {
                "technical": {},
                "onchain": {},
                "sentiment": {},
                "ohlcv": {},
                "options": {}
            }
            dummy_vector = self.extract_features("BTC", datetime.now(), dummy_data)
            self.feature_names = sorted(dummy_vector.features.keys())

        return self.feature_names

    def get_feature_count(self) -> int:
        """Get total number of features."""
        return len(self.get_feature_names())
