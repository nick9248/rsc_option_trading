"""
Backfill Technical Indicators

Computes RSI, MACD, ADX, SMA, EMA, ATR for all dates in ohlcv_history
and saves them to technical_indicators table.

No API calls needed — uses existing ohlcv_history data (748 days).
Safe to re-run: uses ON CONFLICT DO UPDATE.
"""

import logging
import math
from datetime import datetime

import pandas as pd

from coding.core.analytics.technical_indicator_calculator import TechnicalIndicatorCalculator
from coding.core.database.repository import DatabaseRepository
from coding.core.logging.logging_setup import init_logging

init_logging(level="INFO")
logger = logging.getLogger(__name__)


def _to_float(val):
    """Convert value to float, returning None for NaN/None."""
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def backfill_currency(currency: str, repo: DatabaseRepository, calculator: TechnicalIndicatorCalculator) -> int:
    """
    Backfill technical indicators for one currency from ohlcv_history.

    Returns number of rows saved.
    """
    logger.info(f"Loading ohlcv_history for {currency}...")

    conn = repo._get_connection()
    try:
        df = pd.read_sql_query(
            "SELECT timestamp, open, high, low, close, volume FROM ohlcv_history WHERE currency = %s ORDER BY timestamp ASC",
            conn,
            params=(currency,)
        )
    finally:
        repo._return_connection(conn)

    if df.empty:
        logger.error(f"No OHLCV data for {currency}")
        return 0

    logger.info(f"  {len(df)} candles from {df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]}")

    # Format expected by TechnicalIndicatorCalculator: [[timestamp_ms, open, high, low, close, volume], ...]
    ohlcv_list = df[["timestamp", "open", "high", "low", "close", "volume"]].values.tolist()

    logger.info(f"  Calculating indicators...")
    indicators_df = calculator.calculate_all_indicators(ohlcv_list)

    if indicators_df.empty:
        logger.error(f"Indicator calculation failed for {currency}")
        return 0

    saved = 0
    for _, row in indicators_df.iterrows():
        date = datetime.utcfromtimestamp(row["timestamp"] / 1000)
        indicators = {
            "sma_50":         _to_float(row.get("sma_50")),
            "sma_200":        _to_float(row.get("sma_200")),
            "ema_50":         _to_float(row.get("ema_50")),
            "ema_200":        _to_float(row.get("ema_200")),
            "adx":            _to_float(row.get("adx")),
            "plus_di":        _to_float(row.get("plus_di")),
            "minus_di":       _to_float(row.get("minus_di")),
            "atr":            _to_float(row.get("atr")),
            "atr_percentile": _to_float(row.get("atr_percentile")),
            "rsi":            _to_float(row.get("rsi")),
            "macd":           _to_float(row.get("macd")),
            "macd_signal":    _to_float(row.get("macd_signal")),
            "macd_histogram": _to_float(row.get("macd_histogram")),
        }
        repo.save_technical_indicators(currency=currency, date=date, indicators=indicators)
        saved += 1

    logger.info(f"  Saved {saved} rows for {currency}")
    return saved


if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info("Backfilling Technical Indicators from ohlcv_history")
    logger.info("=" * 60)

    repo = DatabaseRepository()
    calculator = TechnicalIndicatorCalculator()
    total = 0

    for currency in ["BTC", "ETH"]:
        logger.info(f"\n[{currency}]")
        total += backfill_currency(currency, repo, calculator)

    logger.info(f"\n{'='*60}")
    logger.info(f"Done: {total} total rows saved")
    logger.info("=" * 60)
