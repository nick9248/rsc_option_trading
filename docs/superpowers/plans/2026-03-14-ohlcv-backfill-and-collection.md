# OHLCV Backfill and Ongoing Collection Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Populate `ohlcv_history` with 2 years of historical daily candles via a one-time backfill script, and ensure the table stays current by collecting the latest daily candle in every `ProspectiveCollector` cycle.

**Architecture:** Add `save_ohlcv()` to `DatabaseRepository` (same ON CONFLICT DO NOTHING pattern as `save_funding_rate`/`save_dvol`). A standalone backfill script fetches historical daily candles from the Deribit API for BTC-PERPETUAL and ETH-PERPETUAL and inserts them. `ProspectiveCollector._collect_currency()` gets a new `_fetch_ohlcv()` step that fetches and saves the last 2 days of daily candles each cycle (idempotent).

**Tech Stack:** Python 3.13, psycopg2, Deribit API (`get_tradingview_chart_data` with `resolution=1D`), pytest

---

## Chunk 1: Repository layer and backfill script

### Task 1: Add `save_ohlcv` to `DatabaseRepository`

**Files:**
- Modify: `coding/core/database/repository.py` (after `save_dvol` ~line 1563)
- Test: `tests/unit/test_repository_ohlcv.py`

- [ ] **Step 1: Write the failing test**

`tests/unit/test_repository_ohlcv.py` already exists with 3 tests for `get_ohlcv_by_date_range`. **Append** (do not overwrite) the following at the bottom of that file:

```python
def test_save_ohlcv_inserts_row():
    """save_ohlcv executes INSERT with correct columns."""
    from unittest.mock import MagicMock, patch, call
    from datetime import datetime
    from coding.core.database.repository import DatabaseRepository

    repo = DatabaseRepository.__new__(DatabaseRepository)
    mock_cursor = MagicMock()
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cm.__exit__ = MagicMock(return_value=False)
    repo._db_cursor = MagicMock(return_value=mock_cm)

    ts = 1700000000000
    dt = datetime(2023, 11, 15)
    repo.save_ohlcv("BTC", "BTC-PERPETUAL", ts, dt, 37000.0, 38000.0, 36500.0, 37500.0, 1234.5)

    sql = mock_cursor.execute.call_args[0][0]
    assert "ohlcv_history" in sql
    assert "ON CONFLICT" in sql
    args = mock_cursor.execute.call_args[0][1]
    assert args == ("BTC", "BTC-PERPETUAL", ts, dt, 37000.0, 38000.0, 36500.0, 37500.0, 1234.5)


def test_save_ohlcv_conflict_do_nothing():
    """save_ohlcv uses DO NOTHING on conflict (idempotent)."""
    from unittest.mock import MagicMock
    from datetime import datetime
    from coding.core.database.repository import DatabaseRepository

    repo = DatabaseRepository.__new__(DatabaseRepository)
    mock_cursor = MagicMock()
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_cursor)
    mock_cm.__exit__ = MagicMock(return_value=False)
    repo._db_cursor = MagicMock(return_value=mock_cm)

    dt = datetime(2023, 11, 15)
    repo.save_ohlcv("BTC", "BTC-PERPETUAL", 1700000000000, dt, 37000.0, 38000.0, 36500.0, 37500.0, 1234.5)

    sql = mock_cursor.execute.call_args[0][0]
    assert "DO NOTHING" in sql
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_repository_ohlcv.py::test_save_ohlcv_inserts_row tests/unit/test_repository_ohlcv.py::test_save_ohlcv_conflict_do_nothing -v
```

Expected: FAIL — `AttributeError: 'DatabaseRepository' object has no attribute 'save_ohlcv'`

- [ ] **Step 3: Implement `save_ohlcv` in repository**

In `coding/core/database/repository.py`, after the `save_dvol` method (~line 1563), add:

```python
def save_ohlcv(
    self,
    currency: str,
    instrument_name: str,
    timestamp: int,
    date,
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: float
) -> None:
    """
    Save one OHLCV daily candle to ohlcv_history.

    Args:
        currency: Currency symbol (e.g., "BTC", "ETH").
        instrument_name: Perpetual instrument (e.g., "BTC-PERPETUAL").
        timestamp: Unix timestamp in milliseconds.
        date: Datetime object for this candle.
        open_price: Opening price.
        high: High price.
        low: Low price.
        close: Closing price.
        volume: Trading volume.
    """
    with self._db_cursor() as cursor:
        cursor.execute("""
            INSERT INTO ohlcv_history (
                currency, instrument_name, timestamp, date,
                open, high, low, close, volume
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (instrument_name, timestamp) DO NOTHING
        """, (currency, instrument_name, timestamp, date,
              open_price, high, low, close, volume))
        logger.debug(f"Saved OHLCV candle for {instrument_name} at {date}: close={close:.2f}")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/test_repository_ohlcv.py -v
```

Expected: all 5 tests PASS (3 existing `get_ohlcv_by_date_range` tests + 2 new `save_ohlcv` tests)

- [ ] **Step 5: Commit**

```bash
git add coding/core/database/repository.py tests/unit/test_repository_ohlcv.py
git commit -m "feat: add save_ohlcv to DatabaseRepository"
```

---

### Task 2: Create OHLCV backfill script

**Files:**
- Create: `scripts/backfill_ohlcv.py`

- [ ] **Step 1: Create the backfill script**

Create `scripts/backfill_ohlcv.py`:

```python
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
```

- [ ] **Step 2: Run the backfill script**

```bash
python -m scripts.backfill_ohlcv
```

Expected output (approximately):
```
OHLCV BACKFILL STARTING
  Currencies: ['BTC', 'ETH']
  Years back: 2
BTC-PERPETUAL: ~730 candles saved
ETH-PERPETUAL: ~730 candles saved
BACKFILL COMPLETE: ~1460 total candles inserted
```

- [ ] **Step 3: Verify data in database**

```bash
python -c "
import psycopg2
conn = psycopg2.connect(host='localhost', port=5433, database='option_trading', user='postgres', password='DB_PASSWORD_REDACTED')
cur = conn.cursor()
cur.execute('''
    SELECT currency, COUNT(*), MIN(date), MAX(date)
    FROM ohlcv_history GROUP BY currency ORDER BY currency
''')
for r in cur.fetchall():
    print(f'{r[0]}: {r[1]} candles | {r[2].date()} to {r[3].date()}')
cur.close(); conn.close()
"
```

Expected: BTC and ETH each with ~730 rows spanning 2 years.

- [ ] **Step 4: Commit**

```bash
git add scripts/backfill_ohlcv.py
git commit -m "feat: add OHLCV backfill script and run historical data load"
```

---

## Chunk 2: Ongoing collection in ProspectiveCollector

### Task 3: Add `_fetch_ohlcv` to `ProspectiveCollector`

**Files:**
- Modify: `coding/service/data_collection/prospective_collector.py`
- Test: `tests/unit/test_prospective_collector_ohlcv.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_prospective_collector_ohlcv.py`:

```python
"""Tests for OHLCV collection in ProspectiveCollector."""
from unittest.mock import MagicMock, patch
from datetime import datetime

import pytest


def _make_collector():
    """Build a ProspectiveCollector with mocked dependencies."""
    from coding.service.data_collection.prospective_collector import ProspectiveCollector
    collector = ProspectiveCollector.__new__(ProspectiveCollector)
    collector.api = MagicMock()
    collector.repo = MagicMock()
    collector.aggregation_service = MagicMock()
    return collector


def test_fetch_ohlcv_saves_candles():
    """_fetch_ohlcv calls save_ohlcv for each candle returned by API."""
    collector = _make_collector()

    collector.api.get_tradingview_chart_data.return_value = {
        "ticks": [1700000000000, 1700086400000],
        "open":  [37000.0, 37500.0],
        "high":  [38000.0, 38200.0],
        "low":   [36500.0, 37100.0],
        "close": [37500.0, 37900.0],
        "volume": [100.0, 120.0],
        "status": "ok"
    }

    collector._fetch_ohlcv("BTC")

    assert collector.repo.save_ohlcv.call_count == 2
    first_call_args = collector.repo.save_ohlcv.call_args_list[0][1]
    assert first_call_args["currency"] == "BTC"
    assert first_call_args["instrument_name"] == "BTC-PERPETUAL"
    assert first_call_args["close"] == 37500.0


def test_fetch_ohlcv_empty_response_does_not_crash():
    """_fetch_ohlcv handles empty API response gracefully."""
    collector = _make_collector()
    collector.api.get_tradingview_chart_data.return_value = {}

    collector._fetch_ohlcv("ETH")  # should not raise

    collector.repo.save_ohlcv.assert_not_called()


def test_fetch_ohlcv_none_response_does_not_crash():
    """_fetch_ohlcv handles None API response gracefully."""
    collector = _make_collector()
    collector.api.get_tradingview_chart_data.return_value = None

    collector._fetch_ohlcv("BTC")  # should not raise

    collector.repo.save_ohlcv.assert_not_called()


def test_collect_currency_calls_fetch_ohlcv():
    """_collect_currency calls _fetch_ohlcv as part of collection."""
    collector = _make_collector()
    collector._fetch_trades = MagicMock(return_value={"count": 5})
    collector._fetch_book_summary = MagicMock(return_value={"count": 10, "instruments": []})
    collector._run_onchain_analysis = MagicMock()
    collector._fetch_dvol = MagicMock()
    collector._fetch_funding_rate = MagicMock()
    collector._fetch_ohlcv = MagicMock()

    collector._collect_currency("BTC", datetime(2026, 3, 14))

    collector._fetch_ohlcv.assert_called_once_with("BTC")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/test_prospective_collector_ohlcv.py -v
```

Expected: FAIL — `AttributeError: '_fetch_ohlcv' not defined`

- [ ] **Step 3: Add `_fetch_ohlcv` method to `ProspectiveCollector`**

In `coding/service/data_collection/prospective_collector.py`, after the `_fetch_funding_rate` method, add:

Note: `time` and `datetime` are already imported at the top of `prospective_collector.py`. Do NOT add duplicate imports inside the method. Add `from datetime import timezone` at the top-level imports of the file if not already present, then add this method:

```python
def _fetch_ohlcv(self, currency: str) -> None:
    """
    Fetch and save the last 2 days of daily OHLCV candles.

    Runs on every 30-min cycle. ON CONFLICT DO NOTHING in save_ohlcv
    makes this idempotent — duplicate candles are silently skipped.

    Args:
        currency: Currency symbol (e.g., "BTC", "ETH").
    """
    instrument = f"{currency}-PERPETUAL"
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (2 * 24 * 60 * 60 * 1000)  # 2 days back

    result = self.api.get_tradingview_chart_data(
        instrument_name=instrument,
        resolution="1D",
        start_timestamp=start_ms,
        end_timestamp=now_ms
    )

    if not result or "ticks" not in result:
        logger.warning(f"No OHLCV data returned for {instrument}")
        return

    ticks = result["ticks"]
    opens = result.get("open", [])
    highs = result.get("high", [])
    lows = result.get("low", [])
    closes = result.get("close", [])
    volumes = result.get("volume", [])

    saved = 0
    for i, ts_ms in enumerate(ticks):
        try:
            dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).replace(tzinfo=None)
            self.repo.save_ohlcv(
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
            saved += 1
        except Exception as e:
            logger.warning(f"Failed to save OHLCV candle for {instrument} at {ts_ms}: {e}")

    logger.info(f"OHLCV: {saved}/{len(ticks)} candles saved for {instrument}")
```

- [ ] **Step 4: Hook `_fetch_ohlcv` into `_collect_currency`**

In `_collect_currency`, after step 5 (`_fetch_funding_rate`), add step 6:

```python
        # 6. Fetch and store latest OHLCV daily candle
        logger.info(f"  Fetching {currency} OHLCV daily candle...")
        try:
            self._fetch_ohlcv(currency)
        except Exception as e:
            logger.error(f"    Error fetching OHLCV: {e}")
```

- [ ] **Step 5: Run all tests**

```bash
pytest tests/unit/test_prospective_collector_ohlcv.py tests/unit/test_repository_ohlcv.py -v
```

Expected: all PASS

- [ ] **Step 6: Run full test suite to check for regressions**

```bash
pytest tests/unit/ -v
```

Expected: all existing tests still PASS

- [ ] **Step 7: Commit**

```bash
git add coding/service/data_collection/prospective_collector.py tests/unit/test_prospective_collector_ohlcv.py
git commit -m "feat: collect daily OHLCV candles in ProspectiveCollector every 30 min"
```

---

## Chunk 3: Final verification

### Task 4: End-to-end verification

- [ ] **Step 1: Verify ohlcv_history is populated**

```bash
python -c "
import psycopg2
conn = psycopg2.connect(host='localhost', port=5433, database='option_trading', user='postgres', password='DB_PASSWORD_REDACTED')
cur = conn.cursor()
cur.execute('''
    SELECT currency, COUNT(*), MIN(date)::date, MAX(date)::date
    FROM ohlcv_history GROUP BY currency ORDER BY currency
''')
for r in cur.fetchall():
    print(f'{r[0]}: {r[1]} rows | {r[2]} to {r[3]}')
cur.close(); conn.close()
"
```

Expected: BTC ~730 rows, ETH ~730 rows, spanning ~2 years.

- [ ] **Step 2: Verify ML feature engineering can load OHLCV data**

```bash
python -c "
import psycopg2
from datetime import datetime, timedelta
conn = psycopg2.connect(host='localhost', port=5433, database='option_trading', user='postgres', password='DB_PASSWORD_REDACTED')
cur = conn.cursor()
# Simulate what feature_engineering does: get last 30 days
end = datetime.utcnow()
start = end - timedelta(days=30)
cur.execute('''
    SELECT date, close FROM ohlcv_history
    WHERE instrument_name = 'BTC-PERPETUAL' AND date BETWEEN %s AND %s
    ORDER BY date ASC
''', (start, end))
rows = cur.fetchall()
print(f'BTC last 30d candles available for feature engineering: {len(rows)}')
closes = [r[1] for r in rows]
if len(closes) >= 2:
    ret_1d = float(closes[-1] - closes[-2]) / float(closes[-2])
    print(f'1d return: {ret_1d:.4f}')
cur.close(); conn.close()
"
```

Expected: 30 rows, valid 1d return value printed.

- [ ] **Step 3: Final commit (if any loose files)**

```bash
git status
# Add only tracked project files — never use git add -A (risk of committing .env)
git add docs/superpowers/plans/2026-03-14-ohlcv-backfill-and-collection.md
git commit -m "chore: ohlcv collection complete - backfill + ongoing collection"
```
