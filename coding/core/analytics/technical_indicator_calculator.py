"""
Technical indicator calculator for market regime detection.

Calculates technical indicators from OHLCV data:
- Moving averages (SMA, EMA)
- Trend strength (ADX)
- Volatility (ATR)
- Momentum (RSI, MACD)
"""

import logging
from typing import Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


class TechnicalIndicatorCalculator:
    """
    Calculate technical indicators from OHLCV data for market analysis.

    Uses pandas-ta library for indicator calculations.
    """

    def __init__(self):
        """Initialize the calculator."""
        logger.info("Initialized TechnicalIndicatorCalculator")

    def calculate_all_indicators(
        self,
        ohlcv_data: List[List],
        lookback_periods: Optional[Dict[str, int]] = None
    ) -> pd.DataFrame:
        """
        Calculate all technical indicators from OHLCV data.

        Args:
            ohlcv_data: List of [timestamp, open, high, low, close, volume] arrays.
            lookback_periods: Custom periods for indicators. If None, uses defaults.

        Returns:
            DataFrame with all indicators calculated.

        Example ohlcv_data format:
            [
                [1640000000000, 47000, 48000, 46500, 47500, 1000],
                [1640086400000, 47500, 48500, 47000, 48000, 1200],
                ...
            ]
        """
        if not ohlcv_data:
            logger.warning("No OHLCV data provided")
            return pd.DataFrame()

        # Convert to DataFrame
        df = pd.DataFrame(
            ohlcv_data,
            columns=["timestamp", "open", "high", "low", "close", "volume"]
        )

        # Convert timestamp to datetime
        df["date"] = pd.to_datetime(df["timestamp"], unit="ms")
        df.set_index("date", inplace=True)

        # Ensure numeric types
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # Set default periods
        periods = lookback_periods or {}
        sma_50_period = periods.get("sma_50", 50)
        sma_200_period = periods.get("sma_200", 200)
        ema_50_period = periods.get("ema_50", 50)
        ema_200_period = periods.get("ema_200", 200)
        adx_period = periods.get("adx", 14)
        atr_period = periods.get("atr", 14)
        rsi_period = periods.get("rsi", 14)
        macd_fast = periods.get("macd_fast", 12)
        macd_slow = periods.get("macd_slow", 26)
        macd_signal = periods.get("macd_signal", 9)

        logger.info(f"Calculating indicators for {len(df)} data points")

        # Calculate moving averages
        df["sma_50"] = df["close"].rolling(window=sma_50_period).mean()
        df["sma_200"] = df["close"].rolling(window=sma_200_period).mean()
        df["ema_50"] = df["close"].ewm(span=ema_50_period, adjust=False).mean()
        df["ema_200"] = df["close"].ewm(span=ema_200_period, adjust=False).mean()

        # Calculate ADX (Average Directional Index)
        try:
            import pandas_ta as ta
            adx_result = ta.adx(
                high=df["high"],
                low=df["low"],
                close=df["close"],
                length=adx_period
            )
            if adx_result is not None and not adx_result.empty:
                df["adx"] = adx_result[f"ADX_{adx_period}"]
                df["plus_di"] = adx_result[f"DMP_{adx_period}"]
                df["minus_di"] = adx_result[f"DMN_{adx_period}"]
            else:
                logger.warning("ADX calculation returned empty result")
                df["adx"] = None
                df["plus_di"] = None
                df["minus_di"] = None
        except Exception as e:
            logger.error(f"Failed to calculate ADX: {e}")
            df["adx"] = None
            df["plus_di"] = None
            df["minus_di"] = None

        # Calculate ATR (Average True Range)
        try:
            import pandas_ta as ta
            atr_result = ta.atr(
                high=df["high"],
                low=df["low"],
                close=df["close"],
                length=atr_period
            )
            if atr_result is not None and not atr_result.empty:
                df["atr"] = atr_result
            else:
                logger.warning("ATR calculation returned empty result")
                df["atr"] = None
        except Exception as e:
            logger.error(f"Failed to calculate ATR: {e}")
            df["atr"] = None

        # Calculate ATR percentile (rank over last 90 days)
        if "atr" in df.columns and df["atr"].notna().any():
            df["atr_percentile"] = df["atr"].rolling(window=90).apply(
                lambda x: (x.rank(pct=True).iloc[-1] * 100) if len(x) > 0 else None,
                raw=False
            )
        else:
            df["atr_percentile"] = None

        # Calculate RSI (Relative Strength Index)
        try:
            import pandas_ta as ta
            rsi_result = ta.rsi(close=df["close"], length=rsi_period)
            if rsi_result is not None and not rsi_result.empty:
                df["rsi"] = rsi_result
            else:
                logger.warning("RSI calculation returned empty result")
                df["rsi"] = None
        except Exception as e:
            logger.error(f"Failed to calculate RSI: {e}")
            df["rsi"] = None

        # Calculate MACD
        try:
            import pandas_ta as ta
            macd_result = ta.macd(
                close=df["close"],
                fast=macd_fast,
                slow=macd_slow,
                signal=macd_signal
            )
            if macd_result is not None and not macd_result.empty:
                df["macd"] = macd_result[f"MACD_{macd_fast}_{macd_slow}_{macd_signal}"]
                df["macd_signal"] = macd_result[f"MACDs_{macd_fast}_{macd_slow}_{macd_signal}"]
                df["macd_histogram"] = macd_result[f"MACDh_{macd_fast}_{macd_slow}_{macd_signal}"]
            else:
                logger.warning("MACD calculation returned empty result")
                df["macd"] = None
                df["macd_signal"] = None
                df["macd_histogram"] = None
        except Exception as e:
            logger.error(f"Failed to calculate MACD: {e}")
            df["macd"] = None
            df["macd_signal"] = None
            df["macd_histogram"] = None

        logger.info("Technical indicators calculated successfully")
        return df

    def get_latest_indicators(self, df: pd.DataFrame) -> Dict[str, Optional[float]]:
        """
        Extract the most recent indicator values from a DataFrame.

        Args:
            df: DataFrame with calculated indicators.

        Returns:
            Dictionary of latest indicator values.
        """
        if df.empty:
            return {}

        latest = df.iloc[-1]

        return {
            "timestamp": int(latest.name.timestamp() * 1000) if hasattr(latest.name, "timestamp") else None,
            "close": float(latest["close"]) if pd.notna(latest["close"]) else None,
            "sma_50": float(latest["sma_50"]) if pd.notna(latest["sma_50"]) else None,
            "sma_200": float(latest["sma_200"]) if pd.notna(latest["sma_200"]) else None,
            "ema_50": float(latest["ema_50"]) if pd.notna(latest["ema_50"]) else None,
            "ema_200": float(latest["ema_200"]) if pd.notna(latest["ema_200"]) else None,
            "adx": float(latest["adx"]) if pd.notna(latest["adx"]) else None,
            "plus_di": float(latest["plus_di"]) if pd.notna(latest["plus_di"]) else None,
            "minus_di": float(latest["minus_di"]) if pd.notna(latest["minus_di"]) else None,
            "atr": float(latest["atr"]) if pd.notna(latest["atr"]) else None,
            "atr_percentile": float(latest["atr_percentile"]) if pd.notna(latest["atr_percentile"]) else None,
            "rsi": float(latest["rsi"]) if pd.notna(latest["rsi"]) else None,
            "macd": float(latest["macd"]) if pd.notna(latest["macd"]) else None,
            "macd_signal": float(latest["macd_signal"]) if pd.notna(latest["macd_signal"]) else None,
            "macd_histogram": float(latest["macd_histogram"]) if pd.notna(latest["macd_histogram"]) else None,
        }

    def get_golden_death_cross(self, df: pd.DataFrame) -> Optional[str]:
        """
        Detect Golden Cross or Death Cross from moving averages.

        Args:
            df: DataFrame with SMA calculations.

        Returns:
            "golden" if 50 SMA > 200 SMA, "death" if 50 SMA < 200 SMA, None if no cross.
        """
        if df.empty or len(df) < 2:
            return None

        latest = df.iloc[-1]
        previous = df.iloc[-2]

        sma_50_latest = latest.get("sma_50")
        sma_200_latest = latest.get("sma_200")
        sma_50_prev = previous.get("sma_50")
        sma_200_prev = previous.get("sma_200")

        # Check if all values are valid
        if pd.isna(sma_50_latest) or pd.isna(sma_200_latest):
            return None
        if pd.isna(sma_50_prev) or pd.isna(sma_200_prev):
            return None

        # Golden Cross: 50 SMA crosses above 200 SMA
        if sma_50_prev <= sma_200_prev and sma_50_latest > sma_200_latest:
            logger.info("Golden Cross detected")
            return "golden"

        # Death Cross: 50 SMA crosses below 200 SMA
        if sma_50_prev >= sma_200_prev and sma_50_latest < sma_200_latest:
            logger.info("Death Cross detected")
            return "death"

        return None

    def classify_volatility_regime(self, atr_percentile: Optional[float]) -> str:
        """
        Classify volatility regime based on ATR percentile.

        Args:
            atr_percentile: ATR percentile (0-100).

        Returns:
            "LOW", "NORMAL", "HIGH", or "EXTREME".
        """
        if atr_percentile is None:
            return "UNKNOWN"

        if atr_percentile < 25:
            return "LOW"
        elif atr_percentile < 50:
            return "NORMAL"
        elif atr_percentile < 75:
            return "HIGH"
        else:
            return "EXTREME"

    def classify_trend_strength(self, adx: Optional[float]) -> str:
        """
        Classify trend strength based on ADX value.

        Args:
            adx: ADX value.

        Returns:
            "NO_TREND", "WEAK_TREND", "STRONG_TREND", or "VERY_STRONG_TREND".
        """
        if adx is None:
            return "UNKNOWN"

        if adx < 20:
            return "NO_TREND"
        elif adx < 25:
            return "WEAK_TREND"
        elif adx < 50:
            return "STRONG_TREND"
        else:
            return "VERY_STRONG_TREND"
