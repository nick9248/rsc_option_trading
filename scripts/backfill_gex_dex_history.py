"""
GEX/DEX History Backfill Script

Recomputes total_net_gex, total_net_dex, call_resistance_strike, put_support_strike,
and hvl_level for historical rows in onchain_analysis_snapshots where these were
never populated (0/NULL) due to a collection bug: greeks were not attached before
feeding GexDexCalculator (fixed 2026-07-13, see
ProspectiveCollector._enrich_with_greeks / _run_onchain_analysis).

Data source investigation (see repository.py:1943 get_hourly_snapshots_for_hour,
already used by VolatilityReconstructionService for the same historical-reconstruction
problem):

- The raw `snapshots` table (per-instrument book_summary captures) has NO mark_iv and
  NO greeks columns at all, and has real collection gaps (irregular captured_at,
  not hourly-bucketed) — it cannot drive Black-Scholes reconstruction on its own.
- `hourly_snapshots` is the correct historical per-(hour, instrument) source: it is
  built by HourlyAggregationService from historical_trades, already carries
  avg_delta/avg_gamma/avg_theta/avg_vega computed via BlackScholesCalculator (from
  each hour's average trade IV) plus open_interest enriched from `snapshots`.
  repo.get_hourly_snapshots_for_hour() already returns instrument dicts in the exact
  shape GexDexCalculator expects (strike, option_type, delta, gamma, open_interest).

Known limitation (see TASKS.md / this script's verification report): hourly_snapshots
only contains instruments that had at least one trade in that hour. Strikes that carry
open interest but did not trade that hour are absent, so the reconstructed GEX/DEX is a
traded-strike-coverage approximation of the live full-order-book calculation used going
forward. This is a genuine historical data-availability constraint, not a shortcut in
this script: the raw per-strike IV needed to Black-Scholes every listed (traded or not)
strike was never persisted before HourlyAggregationService started running.

For the (usually small) case where a hourly_snapshots row exists with mark_iv but no
avg_delta/avg_gamma (aggregation-time edge case), this script applies its own
Black-Scholes fallback pass — same call pattern as
ProspectiveCollector._enrich_with_greeks, replicated here (not imported, since that
method is an instance method private to a live-collector instance) with the historical
snapshot_hour as the time-to-expiry reference instead of datetime.now().

Only rows where total_net_gex = 0 OR total_net_gex IS NULL are ever touched (both in
the initial SELECT and defensively again in the UPDATE's WHERE clause), so correctly
populated post-fix rows are never overwritten. Commits are batched per snapshot_hour.

Usage:
    python -m scripts.backfill_gex_dex_history --dry-run --currency BTC --start 2026-06-01 --end 2026-06-02
    python -m scripts.backfill_gex_dex_history --currency BTC --start 2026-06-01 --end 2026-06-02
    python -m scripts.backfill_gex_dex_history --currency both
    python -m scripts.backfill_gex_dex_history --currency both --limit 500 --dry-run
"""
import argparse
import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from coding.core.logging.logging_setup import init_logging
from coding.core.database.repository import DatabaseRepository
from coding.core.analytics.gex_dex_calculator import GexDexCalculator
from coding.core.analytics.black_scholes_calculator import BlackScholesCalculator

init_logging(level="INFO")
logger = logging.getLogger(__name__)

UPDATE_SQL = """
    UPDATE onchain_analysis_snapshots
    SET total_net_gex = %s,
        total_net_dex = %s,
        call_resistance_strike = %s,
        put_support_strike = %s,
        hvl_level = %s
    WHERE snapshot_hour = %s
      AND currency = %s
      AND expiration = %s
      AND (total_net_gex = 0 OR total_net_gex IS NULL)
"""

SELECT_ZERO_ROWS_SQL_BASE = """
    SELECT snapshot_hour, expiration, underlying_price, total_net_gex
    FROM onchain_analysis_snapshots
    WHERE currency = %s
      AND (total_net_gex = 0 OR total_net_gex IS NULL)
"""

COUNT_ZERO_ROWS_SQL_BASE = """
    SELECT COUNT(*)
    FROM onchain_analysis_snapshots
    WHERE currency = %s
      AND (total_net_gex = 0 OR total_net_gex IS NULL)
"""


def _apply_bs_fallback(
    instruments: List[Dict[str, Any]],
    underlying_price: float,
    reference_time: datetime,
    bs_calculator: BlackScholesCalculator,
) -> List[Dict[str, Any]]:
    """
    Fill missing delta/gamma via Black-Scholes when hourly_snapshots left them NULL
    but mark_iv is present (aggregation-time edge case — see module docstring).

    Mirrors ProspectiveCollector._enrich_with_greeks's BS-fallback branch. Not
    imported directly: that method is a private instance method on a live collector
    with no reason to exist here, but the call pattern into BlackScholesCalculator
    is identical. The only difference is `reference_time`: the historical
    snapshot_hour is used for time-to-expiry instead of datetime.now(), since this
    is reconstructing the past, not analyzing the present.

    Instruments that already have both delta and gamma are passed through
    unchanged. Instruments with no usable mark_iv/strike/name are normalized to
    delta=0, gamma=0 (matches GexDexCalculator's own `item.get("gamma") or 0`
    defaulting, made explicit here for clarity and testability).

    Args:
        instruments: Instrument dicts as returned by
            DatabaseRepository.get_hourly_snapshots_for_hour (strike, option_type,
            mark_iv, delta, gamma, open_interest, instrument_name, ...).
        underlying_price: Spot price to use for the BS calculation.
        reference_time: Historical hour to compute time-to-expiry against.
        bs_calculator: Shared BlackScholesCalculator instance (reused across calls).

    Returns:
        New list of instrument dicts with delta/gamma filled in where possible.
    """
    enriched = []

    for inst in instruments:
        delta = inst.get("delta")
        gamma = inst.get("gamma")

        if (not delta or not gamma) and underlying_price > 0:
            mark_iv = inst.get("mark_iv")
            strike = inst.get("strike")
            name = inst.get("instrument_name", "")

            if mark_iv and strike and name:
                parsed = bs_calculator.parse_instrument_name(name)
                if parsed:
                    tte = bs_calculator.calculate_time_to_expiry(
                        reference_time, parsed["expiry_time"]
                    )
                    if tte > 0:
                        calc = bs_calculator.calculate_greeks(
                            spot_price=underlying_price,
                            strike_price=float(strike),
                            time_to_expiry=tte,
                            implied_volatility=float(mark_iv) / 100.0,
                            option_type=parsed["option_type"],
                        )
                        delta = delta or calc["delta"]
                        gamma = gamma or calc["gamma"]

        enriched.append({
            **inst,
            "delta": delta or 0,
            "gamma": gamma or 0,
        })

    return enriched


def _extract_update_values(
    gex_dex_data: Dict[str, Any]
) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float], Optional[float]]:
    """
    Flatten GexDexCalculator.calculate() output into the five scalar columns
    onchain_analysis_snapshots stores. Mirrors the exact flattening convention
    already used in DatabaseRepository.save_onchain_snapshot (repository.py:1713-1716):
    call_resistance/put_support are {"strike", "net_gex"} dicts (or None) — only the
    strike scalar is persisted.

    Args:
        gex_dex_data: Return value of GexDexCalculator.calculate().

    Returns:
        Tuple of (total_net_gex, total_net_dex, call_resistance_strike,
        put_support_strike, hvl_level).
    """
    key_levels = gex_dex_data.get("key_levels", {}) or {}
    call_resistance = key_levels.get("call_resistance") or {}
    put_support = key_levels.get("put_support") or {}

    return (
        gex_dex_data.get("total_net_gex"),
        gex_dex_data.get("total_net_dex"),
        call_resistance.get("strike"),
        put_support.get("strike"),
        key_levels.get("hvl"),
    )


def _fetch_rows_needing_backfill(
    repo: DatabaseRepository,
    currency: str,
    start: Optional[datetime],
    end: Optional[datetime],
) -> List[Tuple[datetime, str, Optional[float], Optional[float]]]:
    """
    Fetch (snapshot_hour, expiration, underlying_price, total_net_gex) for every
    row that still needs backfilling, ordered by snapshot_hour then expiration so
    the caller can group by hour for batched commits.
    """
    query = SELECT_ZERO_ROWS_SQL_BASE
    params: List[Any] = [currency]

    if start is not None:
        query += " AND snapshot_hour >= %s"
        params.append(start)
    if end is not None:
        query += " AND snapshot_hour <= %s"
        params.append(end)

    query += " ORDER BY snapshot_hour, expiration"

    with repo._db_cursor() as cursor:
        cursor.execute(query, params)
        return cursor.fetchall()


def _count_remaining_zero_rows(
    repo: DatabaseRepository,
    currency: str,
    start: Optional[datetime],
    end: Optional[datetime],
) -> int:
    """Count rows still zero/NULL after a run, for the final report."""
    query = COUNT_ZERO_ROWS_SQL_BASE
    params: List[Any] = [currency]

    if start is not None:
        query += " AND snapshot_hour >= %s"
        params.append(start)
    if end is not None:
        query += " AND snapshot_hour <= %s"
        params.append(end)

    with repo._db_cursor() as cursor:
        cursor.execute(query, params)
        return cursor.fetchone()[0]


def backfill_currency(
    repo: DatabaseRepository,
    currency: str,
    start: Optional[datetime],
    end: Optional[datetime],
    dry_run: bool,
    limit: Optional[int],
    progress_every: int = 100,
) -> Dict[str, Any]:
    """
    Backfill GEX/DEX for one currency over a date range.

    Groups (hour, expiration) pairs by snapshot_hour and commits once per hour
    (one DB transaction per hour, via repo._db_cursor()'s automatic commit/rollback).

    Returns:
        Stats dict: hours_processed, updated, skipped_no_hourly_data,
        skipped_no_price, samples (list of before/after dicts, capped at 5),
        skip_examples (list of (hour, expiration, reason), capped at 20).
    """
    bs_calculator = BlackScholesCalculator()

    rows = _fetch_rows_needing_backfill(repo, currency, start, end)
    if limit is not None:
        rows = rows[:limit]

    stats: Dict[str, Any] = {
        "currency": currency,
        "pairs_found": len(rows),
        "hours_processed": 0,
        "updated": 0,
        "skipped_no_hourly_data": 0,
        "skipped_no_price": 0,
        "samples": [],
        "skip_examples": [],
    }

    if not rows:
        logger.info(f"[{currency}] No rows need backfilling in the given range.")
        return stats

    by_hour: Dict[datetime, List[Tuple[str, Optional[float], Optional[float]]]] = {}
    for snapshot_hour, expiration, underlying_price, old_gex in rows:
        by_hour.setdefault(snapshot_hour, []).append((expiration, underlying_price, old_gex))

    logger.info(
        f"[{currency}] {len(rows)} (hour, expiration) pairs across {len(by_hour)} hours "
        f"need backfilling{' (dry-run)' if dry_run else ''}."
    )

    for snapshot_hour in sorted(by_hour.keys()):
        exp_rows = by_hour[snapshot_hour]

        def _process_hour(cursor=None):
            for expiration, underlying_price, old_gex in exp_rows:
                instruments = repo.get_hourly_snapshots_for_hour(currency, snapshot_hour, expiration)

                if not instruments:
                    stats["skipped_no_hourly_data"] += 1
                    if len(stats["skip_examples"]) < 20:
                        stats["skip_examples"].append(
                            (snapshot_hour, expiration, "no hourly_snapshots rows for this (hour, expiration)")
                        )
                    continue

                if not underlying_price or float(underlying_price) <= 0:
                    stats["skipped_no_price"] += 1
                    if len(stats["skip_examples"]) < 20:
                        stats["skip_examples"].append(
                            (snapshot_hour, expiration, "underlying_price missing/zero on the existing row")
                        )
                    continue

                spot_price = float(underlying_price)
                enriched = _apply_bs_fallback(instruments, spot_price, snapshot_hour, bs_calculator)

                gex_calc = GexDexCalculator(instruments=enriched, spot_price=spot_price, currency=currency)
                result = gex_calc.calculate()
                values = _extract_update_values(result)

                if len(stats["samples"]) < 5:
                    stats["samples"].append({
                        "snapshot_hour": snapshot_hour,
                        "expiration": expiration,
                        "instrument_count": len(instruments),
                        "before_total_net_gex": old_gex,
                        "after_total_net_gex": values[0],
                        "after_total_net_dex": values[1],
                        "after_call_resistance_strike": values[2],
                        "after_put_support_strike": values[3],
                        "after_hvl_level": values[4],
                    })

                if dry_run:
                    stats["updated"] += 1
                else:
                    cursor.execute(UPDATE_SQL, (*values, snapshot_hour, currency, expiration))
                    stats["updated"] += cursor.rowcount

        if dry_run:
            _process_hour()
        else:
            with repo._db_cursor() as cursor:
                _process_hour(cursor)

        stats["hours_processed"] += 1
        if stats["hours_processed"] % progress_every == 0:
            logger.info(
                f"[{currency}] Progress: {stats['hours_processed']}/{len(by_hour)} hours, "
                f"{stats['updated']} rows updated, "
                f"{stats['skipped_no_hourly_data']} skipped (no data), "
                f"{stats['skipped_no_price']} skipped (no price)"
            )

    return stats


def _parse_date_arg(value: Optional[str], end_of_day: bool = False) -> Optional[datetime]:
    if value is None:
        return None
    dt = datetime.strptime(value, "%Y-%m-%d")
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59)
    return dt


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill total_net_gex/total_net_dex/call_resistance_strike/"
            "put_support_strike/hvl_level in onchain_analysis_snapshots for "
            "historical rows left at 0/NULL by the pre-2026-07-13 greeks-attachment bug."
        )
    )
    parser.add_argument(
        "--currency", choices=["BTC", "ETH", "both"], default="both",
        help="Currency to backfill (default: both).",
    )
    parser.add_argument(
        "--start", type=str, default=None,
        help="Start date YYYY-MM-DD, inclusive (default: earliest zero-GEX row for the currency).",
    )
    parser.add_argument(
        "--end", type=str, default=None,
        help="End date YYYY-MM-DD, inclusive (default: latest zero-GEX row for the currency).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Compute GEX/DEX and log samples but do not write to the database.",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Max number of (hour, expiration) pairs to process (for smoke-testing).",
    )
    parser.add_argument(
        "--progress-every", type=int, default=100,
        help="Log progress every N snapshot_hours processed (default: 100).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    currencies = ["BTC", "ETH"] if args.currency == "both" else [args.currency]
    start = _parse_date_arg(args.start, end_of_day=False)
    end = _parse_date_arg(args.end, end_of_day=True)

    logger.info("=" * 70)
    logger.info("GEX/DEX HISTORY BACKFILL STARTING")
    logger.info(f"  Currencies: {currencies}")
    logger.info(f"  Range: {start or '(earliest zero-GEX row)'} -> {end or '(latest zero-GEX row)'}")
    logger.info(f"  Dry run: {args.dry_run}")
    if args.limit is not None:
        logger.info(f"  Limit: {args.limit} pairs")
    logger.info("=" * 70)

    repo = DatabaseRepository()
    start_time = time.time()

    grand_totals = {
        "pairs_found": 0, "hours_processed": 0, "updated": 0,
        "skipped_no_hourly_data": 0, "skipped_no_price": 0,
    }
    all_samples: Dict[str, List[Dict[str, Any]]] = {}
    all_skip_examples: Dict[str, List[Tuple]] = {}
    remaining_zero: Dict[str, int] = {}

    for currency in currencies:
        logger.info(f"--- {currency} ---")
        result = backfill_currency(
            repo=repo,
            currency=currency,
            start=start,
            end=end,
            dry_run=args.dry_run,
            limit=args.limit,
            progress_every=args.progress_every,
        )
        for key in grand_totals:
            grand_totals[key] += result[key]
        all_samples[currency] = result["samples"]
        all_skip_examples[currency] = result["skip_examples"]

        if not args.dry_run:
            remaining_zero[currency] = _count_remaining_zero_rows(repo, currency, start, end)

    elapsed = time.time() - start_time

    logger.info("=" * 70)
    logger.info("BACKFILL COMPLETE")
    logger.info(f"  (hour, expiration) pairs found: {grand_totals['pairs_found']}")
    logger.info(f"  Hours processed:                {grand_totals['hours_processed']}")
    logger.info(f"  Rows updated{'  (dry-run, not written)' if args.dry_run else ''}: {grand_totals['updated']}")
    logger.info(f"  Skipped (no hourly_snapshots data): {grand_totals['skipped_no_hourly_data']}")
    logger.info(f"  Skipped (no underlying_price):      {grand_totals['skipped_no_price']}")
    logger.info(f"  Runtime: {elapsed:.1f}s")

    for currency in currencies:
        logger.info(f"  --- {currency} samples (before -> after) ---")
        for sample in all_samples[currency]:
            logger.info(
                f"    {sample['snapshot_hour']} {sample['expiration']} "
                f"[{sample['instrument_count']} instruments]: "
                f"total_net_gex {sample['before_total_net_gex']} -> {sample['after_total_net_gex']:.2f}, "
                f"total_net_dex -> {sample['after_total_net_dex']:.4f}, "
                f"call_resistance -> {sample['after_call_resistance_strike']}, "
                f"put_support -> {sample['after_put_support_strike']}, "
                f"hvl -> {sample['after_hvl_level']}"
            )
        if all_skip_examples[currency]:
            logger.info(f"  --- {currency} unreconstructable examples (first {len(all_skip_examples[currency])}) ---")
            for snapshot_hour, expiration, reason in all_skip_examples[currency]:
                logger.info(f"    {snapshot_hour} {expiration}: {reason}")
        if currency in remaining_zero:
            logger.info(f"  {currency} rows still zero/NULL after this run: {remaining_zero[currency]}")

    logger.info("=" * 70)


if __name__ == "__main__":
    main()
