"""
OHLCV History Backfill Script

Fetches 2 years of daily candles from Deribit for BTC-PERPETUAL and
ETH-PERPETUAL and inserts them into ohlcv_history.

Safe to re-run — uses ON CONFLICT DO NOTHING.

Usage:
    python -m scripts.backfill_ohlcv
    python -m scripts.backfill_ohlcv --years 3
    python -m scripts.backfill_ohlcv --currency BTC
"""
import argparse
import logging
import time
from datetime import datetime, timezone

from coding.core.logging.logging_setup import init_logging
from coding.core.database.repository import DatabaseRepository
from coding.service.deribit.deribit_api_service import DeribitApiService

init_logging(level="INFO")
logger = logging.getLogger(__name__)


def backfill_currency(api: DeribitApiService, repo: DatabaseRepository, currency: str, years: int) -> int:
    """
    Fetch and save daily OHLCV candles for one currency.

    Args:
        api: Deribit API service.
        repo: Database repository.
        currency: e.g. "BTC" or "ETH".
        years: How many years back to fetch.

    Returns:
        Number of candles inserted.
    """
    instrument = f"{currency}-PERPETUAL"
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (years * 365 * 24 * 60 * 60 * 1000)

    logger.info(f"Fetching {instrument} daily candles ({years} years back)...")

    result = api.get_tradingview_chart_data(
        instrument_name=instrument,
        resolution="1D",
        start_timestamp=start_ms,
        end_timestamp=now_ms
    )

    if not result or "ticks" not in result:
        logger.error(f"No data returned for {instrument}")
        return 0

    ticks = result["ticks"]
    opens = result.get("open", [])
    highs = result.get("high", [])
    lows = result.get("low", [])
    closes = result.get("close", [])
    volumes = result.get("volume", [])

    if not ticks:
        logger.warning(f"Empty ticks for {instrument}")
        return 0

    inserted = 0
    for i, ts_ms in enumerate(ticks):
        try:
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).replace(tzinfo=None)
            repo.save_ohlcv(
                currency=currency,
                instrument_name=instrument,
                timestamp=ts_ms,
                date=dt,
                open_price=float(opens[i]) if i < len(opens) else 0.0,
                high=float(highs[i]) if i < len(highs) else 0.0,
                low=float(lows[i]) if i < len(lows) else 0.0,
                close=float(closes[i]) if i < len(closes) else 0.0,
                volume=float(volumes[i]) if i < len(volumes) else 0.0
            )
            inserted += 1
        except Exception as e:
            logger.warning(f"Failed to save candle at {ts_ms}: {e}")

    logger.info(f"{instrument}: {inserted} candles saved (out of {len(ticks)} fetched)")
    return inserted


def main():
    parser = argparse.ArgumentParser(description="Backfill OHLCV history from Deribit")
    parser.add_argument("--years", type=int, default=2, help="Years of history to fetch (default: 2)")
    parser.add_argument("--currency", type=str, default=None, help="Single currency (BTC or ETH). Default: both.")
    args = parser.parse_args()

    currencies = [args.currency.upper()] if args.currency else ["BTC", "ETH"]

    logger.info("=" * 60)
    logger.info("OHLCV BACKFILL STARTING")
    logger.info(f"  Currencies: {currencies}")
    logger.info(f"  Years back: {args.years}")
    logger.info("=" * 60)

    api = DeribitApiService()
    repo = DatabaseRepository()

    total = 0
    for currency in currencies:
        count = backfill_currency(api, repo, currency, args.years)
        total += count

    logger.info("=" * 60)
    logger.info(f"BACKFILL COMPLETE: {total} total candles inserted")
    logger.info("=" * 60)

    api.close()


if __name__ == "__main__":
    main()
