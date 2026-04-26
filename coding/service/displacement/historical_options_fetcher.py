# coding/service/displacement/historical_options_fetcher.py
"""
Fetches historical Deribit options data for the backtest engine.

One-time local run only — not deployed to VPS.
Results are cached to disk to avoid re-fetching across runs.
"""
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from coding.service.deribit.deribit_api_service import DeribitApiService

logger = logging.getLogger(__name__)

CACHE_DIR = Path("backtest_cache/options")


class HistoricalOptionsFetcher:
    """
    Fetches historical options chain data from Deribit for specific event timestamps.

    For each displacement event date, identifies which OTM call instruments
    existed at that time, fetches their mark prices at entry and at 30/60/90/180
    day checkpoints, and caches results to disk.
    """

    def __init__(self, api_service: DeribitApiService):
        self._api = api_service
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def fetch_options_at_event(
        self,
        asset: str,
        event_ts_ms: int,
        checkpoint_days: list[int] = None,
        min_dte: int = 90,
        max_dte: int = 270,
    ) -> list[dict]:
        """
        Fetch OTM call mark prices at event time and at each checkpoint.

        Args:
            asset: "BTC" or "ETH"
            event_ts_ms: Unix timestamp in milliseconds of the event
            checkpoint_days: List of days after event to fetch exit prices (default [30,60,90,180])
            min_dte: Minimum DTE at event time
            max_dte: Maximum DTE at event time

        Returns:
            List of dicts with keys: instrument_name, dte_at_event, entry_mark_price,
            exit_prices (dict of {days: price or None}), asset, event_ts_ms
        """
        if checkpoint_days is None:
            checkpoint_days = [30, 60, 90, 180]

        cache_key = f"{asset}_{event_ts_ms}_{'_'.join(map(str, checkpoint_days))}"
        cache_file = CACHE_DIR / f"{cache_key}.json"

        if cache_file.exists():
            logger.debug("Cache hit: %s", cache_key)
            return json.loads(cache_file.read_text())

        results = self._fetch_uncached(
            asset, event_ts_ms, checkpoint_days, min_dte, max_dte
        )
        cache_file.write_text(json.dumps(results))
        logger.info(
            "Fetched %d option candidates for %s at %s",
            len(results),
            asset,
            datetime.fromtimestamp(event_ts_ms / 1000, tz=timezone.utc).date(),
        )
        return results

    def _fetch_uncached(
        self,
        asset: str,
        event_ts_ms: int,
        checkpoint_days: list[int],
        min_dte: int,
        max_dte: int,
    ) -> list[dict]:
        try:
            instruments_response = self._api.connection.fetch(
                "public/get_instruments",
                parameters={"currency": asset, "kind": "option", "expired": False},
            )
            instruments = instruments_response.get("result", [])
        except Exception as e:
            logger.error("Failed to fetch instruments for %s: %s", asset, e)
            return []

        candidates = []
        for inst in instruments:
            name = inst.get("instrument_name", "")
            if not name.endswith("-C"):
                continue

            expiry_ts = inst.get("expiration_timestamp", 0)
            if expiry_ts == 0:
                continue

            dte_at_event = (expiry_ts - event_ts_ms) / (1000 * 86400)
            if not (min_dte <= dte_at_event <= max_dte):
                continue

            entry_price = self._fetch_mark_price_at(name, event_ts_ms)
            if entry_price is None:
                continue

            exit_prices: dict[int, Optional[float]] = {}
            for days in checkpoint_days:
                target_ts = event_ts_ms + days * 24 * 3600 * 1000
                exit_prices[days] = self._fetch_mark_price_at(name, target_ts)
                time.sleep(0.05)  # ~20 req/s rate limit

            candidates.append({
                "instrument_name": name,
                "dte_at_event": round(dte_at_event),
                "entry_mark_price": entry_price,
                "exit_prices": exit_prices,
                "asset": asset,
                "event_ts_ms": event_ts_ms,
            })
            time.sleep(0.05)

        return candidates

    def _fetch_mark_price_at(self, instrument_name: str, ts_ms: int) -> Optional[float]:
        """Fetch the closest available mark price (close) to a given timestamp."""
        window_ms = 3600 * 1000  # 1-hour window
        try:
            response = self._api.connection.fetch(
                "public/get_tradingview_chart_data",
                parameters={
                    "instrument_name": instrument_name,
                    "start_timestamp": ts_ms - window_ms,
                    "end_timestamp": ts_ms + window_ms,
                    "resolution": "60",
                },
            )
            data = response.get("result", {})
            closes = data.get("close", [])
            return float(closes[-1]) if closes else None
        except Exception as e:
            logger.debug("Could not fetch mark price for %s at %d: %s", instrument_name, ts_ms, e)
            return None
