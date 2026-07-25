"""
One-shot fixture recorder for the on-chain analysis golden-master test
(Task A2 / refactor_design_spec.md section T1 / 7.1).

Replays the exact API + DB call sequence that
``OnChainAnalysisService.fetch_and_analyze`` issues, against LIVE Deribit and
the local read-only database, and writes every response as gzipped JSON under
``tests/fixtures/onchain/{CURRENCY}_{YYYYMMDD_HHMMSS}/``. The characterization
test (``tests/characterization/test_onchain_golden_master.py``) replays this
fixture through ``FakeDeribitApiService`` / ``FakeDatabaseRepository`` so the
suite runs fully offline and deterministically.

READ-ONLY: only issues GET-style Deribit endpoints and repository read
methods (``get_trades_for_flow_analysis``, ``get_previous_oi_snapshot``,
``get_atm_iv_history``, ``get_onchain_snapshot_history``). Never writes to
the database.

Determinism anchor: a single ``recorded_at_epoch`` is captured once at the
very start and used for every relative time window this script computes
(24h flow lookback, 35d/180d/365d chart windows) instead of calling
``time.time()``/``datetime.now()`` again per phase. ``tests/conftest.py``'s
``frozen_clock`` fixture freezes the production code's own ``time.time()``/
``datetime.now()`` calls to this exact same epoch at test time, so the
windows the pipeline recomputes during replay are byte-identical to the ones
recorded here — required for the fakes' exact-kwargs dict lookup to hit.

Usage:
    python -m scripts.record_onchain_fixture --currency BTC
"""

import argparse
import logging
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from coding.core.analytics.on_chain_analyzer import OnChainAnalyzer
from coding.core.database.repository import DatabaseRepository
from coding.core.logging.logging_setup import init_logging
from coding.service.deribit.deribit_api_service import DeribitApiService
from tests.fakes.fixture_io import dump_json_gz

init_logging(level="INFO")
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent
FIXTURES_ROOT = PROJECT_ROOT / "tests" / "fixtures" / "onchain"

DAY_MS = 24 * 60 * 60 * 1000


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, text=True
        ).strip()
    except Exception:
        return "unknown"


def record(currency: str = "BTC") -> Path:
    """
    Record a full on-chain fixture for ``currency`` from live API + DB.

    Args:
        currency: Currency symbol (BTC, ETH).

    Returns:
        Path to the fixture directory written.
    """
    other_currency = "ETH" if currency == "BTC" else "BTC"
    api = DeribitApiService()
    repo = DatabaseRepository()

    frozen_epoch = time.time()
    frozen_now = datetime.fromtimestamp(frozen_epoch)
    logger.info(f"Recording {currency} fixture anchored at epoch={frozen_epoch} ({frozen_now})")

    out_dir = FIXTURES_ROOT / f"{currency}_{frozen_now.strftime('%Y%m%d_%H%M%S')}"

    # 1. Book summary
    logger.info("Fetching book summary...")
    book_summary = api.get_book_summary(currency=currency, kind="option")
    dump_json_gz(
        out_dir / "book_summary.json.gz",
        [{"kwargs": {"currency": currency, "kind": "option"}, "response": book_summary}],
    )

    analyzer = OnChainAnalyzer(book_summary, currency)
    analyzer.parse_instruments()
    expirations = analyzer.get_expirations()
    spot_price = analyzer.underlying_price
    logger.info(f"Spot price: {spot_price}, expirations ({len(expirations)}): {expirations}")

    # 2. Tickers: every option instrument
    tickers: Dict[str, Any] = {}
    total_instruments = sum(len(v) for v in analyzer.parsed_data.values())
    fetched = 0
    for expiration, instruments in analyzer.parsed_data.items():
        for item in instruments:
            name = item["instrument_name"]
            try:
                tickers[name] = api.get_ticker(name)
            except Exception as e:
                logger.warning(f"Skipping ticker for {name}: {e}")
            fetched += 1
            if fetched % 100 == 0:
                logger.info(f"  Fetched {fetched}/{total_instruments} option tickers")

    # 3. Futures instruments + their tickers + perpetual ticker
    logger.info("Fetching futures instruments...")
    futures_instruments = api.get_instruments(currency=currency, kind="future", expired=False)
    dump_json_gz(
        out_dir / "instruments_future.json.gz",
        [
            {
                "kwargs": {"currency": currency, "kind": "future", "expired": False},
                "response": futures_instruments,
            }
        ],
    )
    for fut in futures_instruments:
        name = fut.get("instrument_name", "")
        if not name:
            continue
        try:
            tickers[name] = api.get_ticker(name)
        except Exception as e:
            logger.warning(f"Skipping future ticker for {name}: {e}")

    perp_name = f"{currency}-PERPETUAL"
    if perp_name not in tickers:
        tickers[perp_name] = api.get_ticker(perp_name)

    dump_json_gz(out_dir / "tickers.json.gz", tickers)
    logger.info(f"Recorded {len(tickers)} tickers total")

    # 4. Last trades (option, count=1000) — used for VWAP IV + block trades
    logger.info("Fetching last trades...")
    last_trades = api.get_last_trades_by_currency(currency=currency, kind="option", count=1000)
    dump_json_gz(
        out_dir / "last_trades.json.gz",
        [
            {
                "kwargs": {"currency": currency, "kind": "option", "count": 1000},
                "response": last_trades,
            }
        ],
    )

    # 5. Tradingview: own currency 180d (RV/VRP/cone, reused for correlation's
    #    own_prices via [-35:] slicing), other currency 35d (correlation only).
    end_ts_180 = int(frozen_epoch * 1000)
    start_ts_180 = end_ts_180 - 180 * DAY_MS
    logger.info("Fetching own-currency tradingview chart (180d)...")
    tv_own = api.get_tradingview_chart_data(
        instrument_name=f"{currency}-PERPETUAL",
        resolution="1D",
        start_timestamp=start_ts_180,
        end_timestamp=end_ts_180,
    )
    dump_json_gz(
        out_dir / f"tradingview_{currency}.json.gz",
        [
            {
                "kwargs": {
                    "instrument_name": f"{currency}-PERPETUAL",
                    "resolution": "1D",
                    "start_timestamp": start_ts_180,
                    "end_timestamp": end_ts_180,
                },
                "response": tv_own,
            }
        ],
    )

    end_ts_35 = int(frozen_epoch * 1000)
    start_ts_35 = end_ts_35 - 35 * DAY_MS
    logger.info("Fetching other-currency tradingview chart (35d)...")
    tv_other = api.get_tradingview_chart_data(
        instrument_name=f"{other_currency}-PERPETUAL",
        resolution="1D",
        start_timestamp=start_ts_35,
        end_timestamp=end_ts_35,
    )
    dump_json_gz(
        out_dir / f"tradingview_{other_currency}.json.gz",
        [
            {
                "kwargs": {
                    "instrument_name": f"{other_currency}-PERPETUAL",
                    "resolution": "1D",
                    "start_timestamp": start_ts_35,
                    "end_timestamp": end_ts_35,
                },
                "response": tv_other,
            }
        ],
    )

    # 6. Funding chart (own currency perpetual, "1m")
    logger.info("Fetching funding chart data...")
    funding_chart = api.get_funding_chart_data(instrument_name=perp_name, length="1m")
    dump_json_gz(
        out_dir / "funding_chart.json.gz",
        [{"kwargs": {"instrument_name": perp_name, "length": "1m"}, "response": funding_chart}],
    )

    # 7. Volatility index (DVOL): own currency 365d (market metrics) + 35d
    #    (correlation); other currency 35d (correlation).
    end_ts_365 = int(frozen_epoch * 1000)
    start_ts_365 = end_ts_365 - 365 * DAY_MS
    logger.info("Fetching own-currency DVOL (365d)...")
    dvol_own_365 = api.get_volatility_index_data(
        currency=currency, resolution=86400, start_timestamp=start_ts_365, end_timestamp=end_ts_365
    )
    logger.info("Fetching own-currency DVOL (35d)...")
    dvol_own_35 = api.get_volatility_index_data(
        currency=currency, resolution=86400, start_timestamp=start_ts_35, end_timestamp=end_ts_35
    )
    dump_json_gz(
        out_dir / f"volatility_index_{currency}.json.gz",
        [
            {
                "kwargs": {
                    "currency": currency,
                    "resolution": 86400,
                    "start_timestamp": start_ts_365,
                    "end_timestamp": end_ts_365,
                },
                "response": dvol_own_365,
            },
            {
                "kwargs": {
                    "currency": currency,
                    "resolution": 86400,
                    "start_timestamp": start_ts_35,
                    "end_timestamp": end_ts_35,
                },
                "response": dvol_own_35,
            },
        ],
    )

    logger.info("Fetching other-currency DVOL (35d)...")
    dvol_other_35 = api.get_volatility_index_data(
        currency=other_currency,
        resolution=86400,
        start_timestamp=start_ts_35,
        end_timestamp=end_ts_35,
    )
    dump_json_gz(
        out_dir / f"volatility_index_{other_currency}.json.gz",
        [
            {
                "kwargs": {
                    "currency": other_currency,
                    "resolution": 86400,
                    "start_timestamp": start_ts_35,
                    "end_timestamp": end_ts_35,
                },
                "response": dvol_other_35,
            }
        ],
    )

    # 8. DB reads, per expiration (read-only)
    flow_end_ts = int(frozen_epoch * 1000)
    flow_start_ts = flow_end_ts - 24 * 60 * 60 * 1000

    for expiration in expirations:
        logger.info(f"Fetching DB fixtures for {expiration}...")
        instruments = analyzer.parsed_data[expiration]

        trades = repo.get_trades_for_flow_analysis(
            currency=currency,
            expiration=expiration,
            start_ts=flow_start_ts,
            end_ts=flow_end_ts,
            trade_filter="all",
        )
        dump_json_gz(out_dir / "db" / f"trades_for_flow_{expiration}.json.gz", trades)

        prev_oi = repo.get_previous_oi_snapshot(currency=currency, expiration=expiration)
        dump_json_gz(
            out_dir / "db" / f"previous_oi_{expiration}.json.gz",
            [[strike, opt_type, oi] for (strike, opt_type), oi in prev_oi.items()],
        )

        atm_strike = min(instruments, key=lambda i: abs(i["strike"] - spot_price))["strike"]
        iv_history = repo.get_atm_iv_history(
            currency=currency, expiration=expiration, strike=atm_strike, option_type="C", limit=90
        )
        dump_json_gz(
            out_dir / "db" / f"atm_iv_history_{expiration}.json.gz",
            {"strike": atm_strike, "history": iv_history},
        )

        history = repo.get_onchain_snapshot_history(currency, expiration, limit=2)
        dump_json_gz(out_dir / "db" / f"onchain_snapshot_history_{expiration}.json.gz", history)

    # 9. Meta
    meta = {
        "currency": currency,
        "other_currency": other_currency,
        "recorded_at_epoch": frozen_epoch,
        "recorded_at_iso": frozen_now.isoformat(),
        "git_sha": _git_sha(),
        "expirations": expirations,
        "spot_price": spot_price,
        "instrument_count": total_instruments,
    }
    dump_json_gz(out_dir / "meta.json.gz", meta)

    logger.info(f"Fixture recorded at {out_dir}")
    return out_dir


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Record on-chain fixture from live API + DB")
    parser.add_argument("--currency", default="BTC")
    args = parser.parse_args()
    record(args.currency)
