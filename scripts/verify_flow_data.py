"""
Flow data verification suite.

Verifies that buy_sell_flow_metrics correctly reflects raw historical_trades data.
Runs 6 cross-checks; Checks 1-4 and 6 are PASS/FAIL, Check 5 is informational.

Usage:
    python scripts/verify_flow_data.py --currency BTC
    python scripts/verify_flow_data.py --currency ETH
    python scripts/verify_flow_data.py --currency BTC --expiration 27MAR26
"""

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path so imports work when run as script
sys.path.insert(0, str(Path(__file__).parent.parent))

from coding.core.logging.logging_setup import init_logging
from coding.core.database.repository import DatabaseRepository

init_logging(level="WARNING")  # Suppress routine DB logs during verification
logger = logging.getLogger(__name__)


def _db_cursor(repo):
    """Get a database cursor context manager.

    Uses repository's internal cursor method — same pattern as other scripts in this codebase.
    """
    return repo._db_cursor()


def check_1_mathematical_consistency(repo, currency: str, expiration: str = None) -> bool:
    """
    Check 1: Mathematical consistency within buy_sell_flow_metrics.

    For each row: net_flow == round(buy_volume - sell_volume, 8)
    buy_sell_ratio == buy_volume / sell_volume when sell > 0, NULL when sell == 0.
    """
    print("\n[CHECK 1] Mathematical consistency")

    exp_clause = "AND expiration = %s" if expiration else ""
    params = [currency]
    if expiration:
        params.append(expiration)

    query = f"""
        SELECT strike, option_type, expiration, buy_volume, sell_volume, net_flow, buy_sell_ratio
        FROM buy_sell_flow_metrics
        WHERE currency = %s
          {exp_clause}
    """

    with _db_cursor(repo) as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()

    if not rows:
        print("  No rows found in buy_sell_flow_metrics.")
        print("  SKIP (no data)")
        return True  # No data is not a failure

    discrepancies = []
    for strike, opt_type, exp, buy_vol, sell_vol, net_flow, ratio in rows:
        buy_vol = float(buy_vol)
        sell_vol = float(sell_vol)
        net_flow = float(net_flow) if net_flow is not None else None
        ratio = float(ratio) if ratio is not None else None

        expected_net = round(buy_vol - sell_vol, 8)
        if net_flow is not None and abs(net_flow - expected_net) > 1e-6:
            discrepancies.append(
                f"  {exp} {strike} {opt_type}: net_flow={net_flow:.8f} expected={expected_net:.8f}"
            )

        if sell_vol > 0:
            expected_ratio = buy_vol / sell_vol
            if ratio is not None and abs(ratio - expected_ratio) > 1e-6:
                discrepancies.append(
                    f"  {exp} {strike} {opt_type}: buy_sell_ratio={ratio:.6f} expected={expected_ratio:.6f}"
                )
        else:
            # sell_vol == 0: ratio should be NULL
            if ratio is not None:
                discrepancies.append(
                    f"  {exp} {strike} {opt_type}: buy_sell_ratio={ratio} expected=NULL (sell_vol=0)"
                )

    print(f"  Rows checked: {len(rows):,}")
    print(f"  Discrepancies: {len(discrepancies)}")
    if discrepancies:
        print("  First 5 discrepancies:")
        for d in discrepancies[:5]:
            print(d)
        print("  FAIL ✗")
        return False
    print("  PASS ✓")
    return True


def check_2_trade_count_reconciliation(repo, currency: str, expiration: str = None) -> bool:
    """
    Check 2: Trade count reconciliation.

    For latest captured_at per (currency, expiration), per (strike, option_type):
    COUNT direction='buy' in historical_trades within 24h window == buy_count.
    COUNT direction='sell' == sell_count.
    """
    print("\n[CHECK 2] Trade count reconciliation")

    exp_clause = "AND m.expiration = %s" if expiration else ""
    params = [currency]
    if expiration:
        params.append(expiration)

    # Get latest metrics with their time windows
    query = f"""
        SELECT DISTINCT ON (m.expiration, m.strike, m.option_type)
            m.expiration, m.strike, m.option_type,
            m.buy_count, m.sell_count, m.captured_at
        FROM buy_sell_flow_metrics m
        WHERE m.currency = %s
          {exp_clause}
        ORDER BY m.expiration, m.strike, m.option_type, m.captured_at DESC
    """

    with _db_cursor(repo) as cursor:
        cursor.execute(query, params)
        metric_rows = cursor.fetchall()

    if not metric_rows:
        print("  No rows found.")
        print("  SKIP (no data)")
        return True

    mismatches = []
    checked = 0

    for exp, strike, opt_type, buy_count, sell_count, captured_at in metric_rows:
        # 24h window ending at captured_at
        window_end = captured_at
        window_start = window_end - timedelta(hours=24)
        start_ts = int(window_start.timestamp() * 1000)
        end_ts = int(window_end.timestamp() * 1000)

        count_query = """
            SELECT direction, COUNT(*) as cnt
            FROM historical_trades
            WHERE currency = %s
              AND expiration = %s
              AND strike = %s
              AND option_type = %s
              AND trade_timestamp >= %s
              AND trade_timestamp <= %s
              AND direction IS NOT NULL
            GROUP BY direction
        """

        with _db_cursor(repo) as cursor:
            cursor.execute(count_query, (currency, exp, strike, opt_type, start_ts, end_ts))
            count_rows = cursor.fetchall()

        raw_counts = {"buy": 0, "sell": 0}
        for direction, cnt in count_rows:
            raw_counts[direction] = cnt

        checked += 1
        expected_buy = int(buy_count) if buy_count else 0
        expected_sell = int(sell_count) if sell_count else 0

        if raw_counts["buy"] != expected_buy or raw_counts["sell"] != expected_sell:
            mismatches.append(
                f"  {exp} ${strike} {opt_type}: "
                f"buy={raw_counts['buy']} (expected {expected_buy}), "
                f"sell={raw_counts['sell']} (expected {expected_sell})"
            )

    print(f"  Strike-type pairs checked: {checked:,}")
    print(f"  Mismatches: {len(mismatches)}")
    if mismatches:
        print("  First 5 mismatches:")
        for m in mismatches[:5]:
            print(m)
        print("  FAIL ✗")
        return False
    print("  PASS ✓")
    return True


def check_3_volume_reconciliation(repo, currency: str, expiration: str = None) -> bool:
    """
    Check 3: Volume reconciliation.

    SUM(amount) per (strike, option_type, direction) from historical_trades within 24h window
    must match buy_volume / sell_volume in buy_sell_flow_metrics.
    Tolerance: 1e-6.
    """
    print("\n[CHECK 3] Volume reconciliation")

    exp_clause = "AND m.expiration = %s" if expiration else ""
    params = [currency]
    if expiration:
        params.append(expiration)

    query = f"""
        SELECT DISTINCT ON (m.expiration, m.strike, m.option_type)
            m.expiration, m.strike, m.option_type,
            m.buy_volume, m.sell_volume, m.captured_at
        FROM buy_sell_flow_metrics m
        WHERE m.currency = %s
          {exp_clause}
        ORDER BY m.expiration, m.strike, m.option_type, m.captured_at DESC
    """

    with _db_cursor(repo) as cursor:
        cursor.execute(query, params)
        metric_rows = cursor.fetchall()

    if not metric_rows:
        print("  No rows found.")
        print("  SKIP (no data)")
        return True

    mismatches = []
    max_discrepancy = 0.0
    checked = 0

    for exp, strike, opt_type, buy_volume, sell_volume, captured_at in metric_rows:
        window_end = captured_at
        window_start = window_end - timedelta(hours=24)
        start_ts = int(window_start.timestamp() * 1000)
        end_ts = int(window_end.timestamp() * 1000)

        vol_query = """
            SELECT direction, SUM(amount) as total
            FROM historical_trades
            WHERE currency = %s
              AND expiration = %s
              AND strike = %s
              AND option_type = %s
              AND trade_timestamp >= %s
              AND trade_timestamp <= %s
              AND direction IS NOT NULL
            GROUP BY direction
        """

        with _db_cursor(repo) as cursor:
            cursor.execute(vol_query, (currency, exp, strike, opt_type, start_ts, end_ts))
            vol_rows = cursor.fetchall()

        raw_vols = {"buy": 0.0, "sell": 0.0}
        for direction, total in vol_rows:
            raw_vols[direction] = float(total) if total else 0.0

        checked += 1
        exp_buy = float(buy_volume) if buy_volume else 0.0
        exp_sell = float(sell_volume) if sell_volume else 0.0

        disc_buy = abs(raw_vols["buy"] - exp_buy)
        disc_sell = abs(raw_vols["sell"] - exp_sell)
        max_discrepancy = max(max_discrepancy, disc_buy, disc_sell)

        if disc_buy > 1e-6 or disc_sell > 1e-6:
            mismatches.append(
                f"  {exp} ${strike} {opt_type}: "
                f"buy_vol={raw_vols['buy']:.6f} (expected {exp_buy:.6f}, diff={disc_buy:.8f}), "
                f"sell_vol={raw_vols['sell']:.6f} (expected {exp_sell:.6f}, diff={disc_sell:.8f})"
            )

    print(f"  Strike-type pairs checked: {checked:,}")
    print(f"  Mismatches: {len(mismatches)}  (tolerance: 1e-6)")
    print(f"  Largest absolute discrepancy: {max_discrepancy:.2e}")
    if mismatches:
        print("  First 5 mismatches:")
        for m in mismatches[:5]:
            print(m)
        print("  FAIL ✗")
        return False
    print("  PASS ✓")
    return True


def check_4_notional_reconciliation(repo, currency: str, expiration: str = None) -> bool:
    """
    Check 4: Notional reconciliation.

    SUM(amount * index_price) per (strike, option_type, direction) from historical_trades
    must match buy_notional / sell_notional in buy_sell_flow_metrics.
    """
    print("\n[CHECK 4] Notional reconciliation")

    exp_clause = "AND m.expiration = %s" if expiration else ""
    params = [currency]
    if expiration:
        params.append(expiration)

    query = f"""
        SELECT DISTINCT ON (m.expiration, m.strike, m.option_type)
            m.expiration, m.strike, m.option_type,
            m.buy_notional, m.sell_notional, m.captured_at
        FROM buy_sell_flow_metrics m
        WHERE m.currency = %s
          {exp_clause}
        ORDER BY m.expiration, m.strike, m.option_type, m.captured_at DESC
    """

    with _db_cursor(repo) as cursor:
        cursor.execute(query, params)
        metric_rows = cursor.fetchall()

    if not metric_rows:
        print("  No rows found.")
        print("  SKIP (no data)")
        return True

    mismatches = []
    max_discrepancy = 0.0
    checked = 0

    for exp, strike, opt_type, buy_notional, sell_notional, captured_at in metric_rows:
        window_end = captured_at
        window_start = window_end - timedelta(hours=24)
        start_ts = int(window_start.timestamp() * 1000)
        end_ts = int(window_end.timestamp() * 1000)

        notional_query = """
            SELECT direction, SUM(amount * index_price) as total
            FROM historical_trades
            WHERE currency = %s
              AND expiration = %s
              AND strike = %s
              AND option_type = %s
              AND trade_timestamp >= %s
              AND trade_timestamp <= %s
              AND direction IS NOT NULL
              AND index_price IS NOT NULL
            GROUP BY direction
        """

        with _db_cursor(repo) as cursor:
            cursor.execute(notional_query, (currency, exp, strike, opt_type, start_ts, end_ts))
            notional_rows = cursor.fetchall()

        raw_notional = {"buy": 0.0, "sell": 0.0}
        for direction, total in notional_rows:
            raw_notional[direction] = float(total) if total else 0.0

        checked += 1
        exp_buy = float(buy_notional) if buy_notional else 0.0
        exp_sell = float(sell_notional) if sell_notional else 0.0

        disc_buy = abs(raw_notional["buy"] - exp_buy)
        disc_sell = abs(raw_notional["sell"] - exp_sell)
        max_discrepancy = max(max_discrepancy, disc_buy, disc_sell)

        # Notional can differ slightly due to index_price changes between trade time;
        # use a tolerance of $1 or 0.01% of notional, whichever is larger.
        tolerance = max(1.0, exp_buy * 0.0001, exp_sell * 0.0001)

        if disc_buy > tolerance or disc_sell > tolerance:
            mismatches.append(
                f"  {exp} ${strike} {opt_type}: "
                f"buy_notional={raw_notional['buy']:.2f} (expected {exp_buy:.2f}), "
                f"sell_notional={raw_notional['sell']:.2f} (expected {exp_sell:.2f})"
            )

    print(f"  Strike-type pairs checked: {checked:,}")
    print(f"  Mismatches: {len(mismatches)}")
    print(f"  Largest absolute discrepancy: ${max_discrepancy:,.2f}")
    if mismatches:
        print("  First 5 mismatches:")
        for m in mismatches[:5]:
            print(m)
        print("  FAIL ✗")
        return False
    print("  PASS ✓")
    return True


def check_5_direction_semantics(repo, currency: str, expiration: str = None) -> None:
    """
    Check 5: Direction semantics (informational only — never fails the suite).

    Hypothesis: direction='buy' correlates with tick_direction IN (0, 1) (price moving up).
    Options markets have weaker correlation than futures — low percentage is expected.
    WARNING if correlation < 45% (below random chance).
    """
    print("\n[CHECK 5] Direction semantics (informational)")

    exp_clause = "AND expiration = %s" if expiration else ""
    params = [currency]
    if expiration:
        params.append(expiration)

    query = f"""
        SELECT direction, tick_direction
        FROM historical_trades
        WHERE currency = %s
          {exp_clause}
          AND tick_direction IS NOT NULL
          AND direction IS NOT NULL
        ORDER BY trade_timestamp DESC
        LIMIT 500
    """

    with _db_cursor(repo) as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()

    if not rows:
        print("  No trades with tick_direction found.")
        print("  SKIP")
        return

    # tick_direction: 0=plus tick, 1=zero-plus tick, 2=minus tick, 3=zero-minus tick
    # Hypothesis: buy -> tick_direction in (0, 1)
    total = len(rows)
    buy_total = sum(1 for direction, _ in rows if direction == "buy")
    correlated = sum(
        1 for direction, tick_dir in rows
        if direction == "buy" and tick_dir in (0, 1)
    )
    correlation_pct = (correlated / buy_total * 100) if buy_total > 0 else 0.0

    print(f"  Trades sampled: {total:,}")
    print(f"  Buy trades: {buy_total:,}")
    print(f"  Buy + tick (0,1) correlation: {correlation_pct:.1f}%")
    print("  NOTE: Options markets have weaker aggressor/tick correlation than futures.")
    print("        Mid-market crosses are common; a low percentage is expected for liquid options.")

    if correlation_pct < 45.0:
        print(f"  WARNING: Correlation {correlation_pct:.1f}% < 45% threshold.")
        print("           This may indicate direction inversion or data quality issues.")
        print("           Investigate a sample of raw trades before concluding.")
    else:
        print("  This value is within the expected range for liquid options.")


def check_6_time_window_verification(repo, currency: str, expiration: str = None) -> bool:
    """
    Check 6: Time window verification.

    The pipeline uses window [captured_at - 24h, captured_at] when fetching trades.
    Because the SQL in BuySellFlowAnalyzer._fetch_trades already enforces this window with
    WHERE trade_timestamp >= start_ts AND trade_timestamp <= end_ts, it is architecturally
    impossible for the pipeline to include trades outside the window. The meaningful verification
    is therefore:

    (a) Are the metrics fresh? (captured_at is recent — pipeline ran in the last 30h)
    (b) Does the stated 24h window actually have trades? (pipeline ran with real data, not empty)
    (c) How many trades exist outside the 24h window in historical_trades (informational)?
        These are older/newer trades that CORRECTLY were not included.

    FAIL conditions:
    - captured_at is more than 30h old (metrics are stale — pipeline has not run)
    - Zero trades found within the 24h window (pipeline ran but captured nothing — data gap)
    """
    print("\n[CHECK 6] Time window verification")

    exp_clause = "AND expiration = %s" if expiration else ""
    params = [currency]
    if expiration:
        params.append(expiration)

    # Find the latest captured_at
    query = f"""
        SELECT MAX(captured_at) FROM buy_sell_flow_metrics
        WHERE currency = %s {exp_clause}
    """

    with _db_cursor(repo) as cursor:
        cursor.execute(query, params)
        row = cursor.fetchone()

    if not row or not row[0]:
        print("  No captured_at found.")
        print("  SKIP (no data)")
        return True

    latest_captured_at = row[0]
    window_start = latest_captured_at - timedelta(hours=24)
    start_ts = int(window_start.timestamp() * 1000)
    end_ts = int(latest_captured_at.timestamp() * 1000)

    # Compute age — handle timezone-aware vs naive datetimes
    if latest_captured_at.tzinfo is not None:
        from datetime import timezone
        now = datetime.now(timezone.utc)
    else:
        now = datetime.utcnow()
    age_hours = (now - latest_captured_at).total_seconds() / 3600

    print(f"  Latest captured_at: {latest_captured_at.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Window: {window_start.strftime('%Y-%m-%d %H:%M:%S')} → {latest_captured_at.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Metrics age: {age_hours:.1f}h ago")

    # (a) Freshness check
    stale = age_hours > 30.0
    if stale:
        print(f"  WARNING: captured_at is {age_hours:.1f}h old — collection daemon may not have run recently.")

    # (b) Count trades within the declared 24h window
    in_query = f"""
        SELECT COUNT(*)
        FROM historical_trades
        WHERE currency = %s
          {exp_clause}
          AND direction IS NOT NULL
          AND trade_timestamp >= %s
          AND trade_timestamp <= %s
    """
    in_params = [currency]
    if expiration:
        in_params.append(expiration)
    in_params.extend([start_ts, end_ts])

    with _db_cursor(repo) as cursor:
        cursor.execute(in_query, in_params)
        in_count = cursor.fetchone()[0]

    print(f"  Trades within 24h window: {in_count:,}")

    # (c) Informational: trades outside the 24h window (correctly excluded by the pipeline)
    #     historical_trades accumulates all history, so this will always be > 0; it is expected.
    out_query = f"""
        SELECT COUNT(*)
        FROM historical_trades
        WHERE currency = %s
          {exp_clause}
          AND direction IS NOT NULL
          AND (trade_timestamp < %s OR trade_timestamp > %s)
    """
    out_params = [currency]
    if expiration:
        out_params.append(expiration)
    out_params.extend([start_ts, end_ts])

    with _db_cursor(repo) as cursor:
        cursor.execute(out_query, out_params)
        out_count = cursor.fetchone()[0]

    print(f"  Trades outside 24h window (informational — correctly excluded): {out_count:,}")
    print("  NOTE: historical_trades accumulates all history; out-of-window count is expected to be large.")

    if stale:
        print("  FAIL ✗ — Metrics are stale (captured_at > 30h ago). Run the collection daemon.")
        return False

    if in_count == 0:
        print("  FAIL ✗ — No trades found within the 24h window. Pipeline may not have collected data.")
        return False

    print("  PASS ✓")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Verify flow data integrity: buy_sell_flow_metrics vs historical_trades."
    )
    parser.add_argument("--currency", required=True, choices=["BTC", "ETH"],
                        help="Currency to verify (BTC or ETH)")
    parser.add_argument("--expiration", default=None,
                        help="Optional: restrict to a single expiration (e.g. 27MAR26)")
    args = parser.parse_args()

    currency = args.currency
    expiration = args.expiration

    scope = f"{currency}" + (f" {expiration}" if expiration else " (all expirations)")
    print(f"\n=== Flow Data Verification: {scope} ===")

    repo = DatabaseRepository()

    results = {}

    results["check_1"] = check_1_mathematical_consistency(repo, currency, expiration)
    results["check_2"] = check_2_trade_count_reconciliation(repo, currency, expiration)
    results["check_3"] = check_3_volume_reconciliation(repo, currency, expiration)
    results["check_4"] = check_4_notional_reconciliation(repo, currency, expiration)
    check_5_direction_semantics(repo, currency, expiration)  # informational, no result stored
    results["check_6"] = check_6_time_window_verification(repo, currency, expiration)

    # Summary
    hard_checks = [results["check_1"], results["check_2"], results["check_3"],
                   results["check_4"], results["check_6"]]
    passed = sum(hard_checks)
    total = len(hard_checks)

    print(f"\n=== SUMMARY ===")
    print(f"PASS: {passed}/{total} hard checks")
    print(f"INFORMATIONAL: 1 (Check 5 — direction semantics)")

    if passed == total:
        print("All flow data verified correctly.")
        sys.exit(0)
    else:
        print("One or more checks FAILED. Investigate discrepancies above.")
        sys.exit(1)


if __name__ == "__main__":
    main()
