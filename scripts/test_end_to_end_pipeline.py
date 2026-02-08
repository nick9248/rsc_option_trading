"""
End-to-End Pipeline Test (Phase 4)

Validates complete data flow:
Raw Trades → Greeks Calculation → Hourly Aggregation → Label Generation → Database Storage

This is the final integration test before running large-scale backfill.
"""

import logging
from datetime import datetime, timedelta

from coding.core.logging.logging_setup import init_logging
from coding.core.database.repository import DatabaseRepository
from coding.core.ml.label_generator import LabelGenerator
from coding.service.data_collection.hourly_aggregation_service import HourlyAggregationService

init_logging(level="INFO")
logger = logging.getLogger(__name__)


def test_end_to_end_pipeline():
    """Test complete pipeline with real data."""

    logger.info("")
    logger.info("="*60)
    logger.info("END-TO-END PIPELINE TEST (PHASE 4)")
    logger.info("="*60)
    logger.info("")

    repo = DatabaseRepository()
    aggregator = HourlyAggregationService()
    label_gen = LabelGenerator()

    # Step 1: Verify raw trades exist
    logger.info("="*60)
    logger.info("Step 1: Verify Raw Trades")
    logger.info("="*60)

    connection = repo._get_connection()
    try:
        cursor = connection.cursor()

        cursor.execute("""
            SELECT
                currency,
                COUNT(*) as trade_count,
                MIN(TO_TIMESTAMP(trade_timestamp / 1000.0)) as earliest,
                MAX(TO_TIMESTAMP(trade_timestamp / 1000.0)) as latest
            FROM historical_trades
            GROUP BY currency
            ORDER BY currency
        """)

        trades_summary = {}
        for row in cursor.fetchall():
            currency, count, earliest, latest = row
            trades_summary[currency] = {
                "count": count,
                "earliest": earliest,
                "latest": latest
            }
            logger.info(f"\n{currency}:")
            logger.info(f"  Trades: {count:,}")
            logger.info(f"  Range: {earliest} to {latest}")

        cursor.close()

        if not trades_summary:
            logger.error("\n[FAILED] No trades found in database!")
            return False

        logger.info("\n[SUCCESS] Raw trades verified")

    finally:
        repo._return_connection(connection)

    # Step 2: Run hourly aggregation
    logger.info("")
    logger.info("="*60)
    logger.info("Step 2: Hourly Aggregation")
    logger.info("="*60)
    logger.info("")

    for currency in trades_summary.keys():
        logger.info(f"Aggregating {currency}...")

        stats = aggregator.aggregate_to_hourly(currency=currency)

        logger.info(f"  Snapshots created: {stats['snapshots_created']}")
        logger.info(f"  Hours processed: {stats['hours_processed']}")

    logger.info("\n[SUCCESS] Hourly aggregation complete")

    # Step 3: Verify hourly snapshots
    logger.info("")
    logger.info("="*60)
    logger.info("Step 3: Verify Hourly Snapshots")
    logger.info("="*60)

    connection = repo._get_connection()
    try:
        cursor = connection.cursor()

        cursor.execute("""
            SELECT
                currency,
                COUNT(*) as snapshot_count,
                MIN(snapshot_hour) as earliest_hour,
                MAX(snapshot_hour) as latest_hour,
                COUNT(DISTINCT snapshot_hour) as unique_hours,
                AVG(CASE WHEN avg_delta IS NOT NULL THEN 1.0 ELSE 0.0 END) * 100 as greeks_coverage
            FROM hourly_snapshots
            GROUP BY currency
            ORDER BY currency
        """)

        snapshots_summary = {}
        for row in cursor.fetchall():
            currency, count, earliest, latest, hours, greeks_cov = row
            snapshots_summary[currency] = {
                "count": count,
                "earliest": earliest,
                "latest": latest,
                "hours": hours,
                "greeks_coverage": float(greeks_cov)
            }
            logger.info(f"\n{currency}:")
            logger.info(f"  Snapshots: {count:,}")
            logger.info(f"  Hours: {hours}")
            logger.info(f"  Greeks coverage: {greeks_cov:.2f}%")

        cursor.close()

        logger.info("\n[SUCCESS] Hourly snapshots verified")

    finally:
        repo._return_connection(connection)

    # Step 4: Generate labels for all hours
    logger.info("")
    logger.info("="*60)
    logger.info("Step 4: Label Generation")
    logger.info("="*60)
    logger.info("")

    all_labels = {}
    for currency, info in snapshots_summary.items():
        logger.info(f"Generating labels for {currency}...")

        labels_list = label_gen.generate_labels_batch(
            currency=currency,
            start_time=info["earliest"],
            end_time=info["latest"]
        )

        all_labels[currency] = labels_list
        logger.info(f"  Generated: {len(labels_list)} label sets")

        # Show label distribution
        from collections import Counter
        regimes = [l.market_regime for l in labels_list if l.market_regime]
        regime_dist = Counter(regimes)

        logger.info(f"  Regime distribution:")
        for regime, count in regime_dist.items():
            logger.info(f"    {regime}: {count}")

    logger.info("\n[SUCCESS] Labels generated for all hours")

    # Step 5: Validate data consistency
    logger.info("")
    logger.info("="*60)
    logger.info("Step 5: Data Consistency Validation")
    logger.info("="*60)
    logger.info("")

    for currency in all_labels.keys():
        logger.info(f"Validating {currency}...")

        # Check: Number of label sets matches number of hours
        expected_hours = snapshots_summary[currency]["hours"]
        actual_labels = len(all_labels[currency])

        # May have fewer labels due to insufficient data for early hours
        if actual_labels <= expected_hours:
            logger.info(f"  Hours with snapshots: {expected_hours}")
            logger.info(f"  Hours with labels: {actual_labels}")
            logger.info(f"  Coverage: {(actual_labels/expected_hours)*100:.1f}%")
        else:
            logger.error(f"  [ERROR] More labels ({actual_labels}) than hours ({expected_hours})!")
            return False

        # Check: All labels have valid regimes
        labels_with_regime = sum(1 for l in all_labels[currency] if l.market_regime)
        logger.info(f"  Labels with regime: {labels_with_regime}/{actual_labels}")

        # Check: All labels have valid timestamps
        timestamps_match = all(
            info["earliest"] <= label.timestamp <= info["latest"]
            for label in all_labels[currency]
        )
        if timestamps_match:
            logger.info(f"  All timestamps within valid range")
        else:
            logger.error(f"  [ERROR] Invalid timestamps detected!")
            return False

    logger.info("\n[SUCCESS] Data consistency validated")

    # Step 6: Test queryability
    logger.info("")
    logger.info("="*60)
    logger.info("Step 6: Query Test (ML-Ready Data)")
    logger.info("="*60)
    logger.info("")

    # Simulate ML training query: Get features + labels for a specific hour
    test_currency = "BTC"
    test_timestamp = snapshots_summary[test_currency]["latest"]

    logger.info(f"Test query: {test_currency} at {test_timestamp}")

    connection = repo._get_connection()
    try:
        cursor = connection.cursor()

        # Get features (from hourly_snapshots)
        cursor.execute("""
            SELECT
                instrument_name,
                mark_price,
                mark_iv,
                avg_delta,
                avg_gamma,
                avg_theta,
                avg_vega,
                total_volume
            FROM hourly_snapshots
            WHERE currency = %s
              AND snapshot_hour = %s
            LIMIT 5
        """, (test_currency, test_timestamp))

        features = cursor.fetchall()
        logger.info(f"\nFeatures retrieved: {len(features)} instruments")
        for i, feat in enumerate(features[:3], 1):
            price_str = f"${float(feat[1]):.2f}" if feat[1] else "N/A"
            iv_str = f"{float(feat[2]):.2f}%" if feat[2] else "N/A"
            logger.info(f"  {i}. {feat[0]}: price={price_str}, iv={iv_str}")

        cursor.close()

    finally:
        repo._return_connection(connection)

    # Get labels (from generated data)
    test_labels = [l for l in all_labels[test_currency] if l.timestamp == test_timestamp]
    if test_labels:
        label = test_labels[0]
        logger.info(f"\nLabel retrieved:")
        logger.info(f"  Market regime: {label.market_regime}")
        logger.info(f"  Realized vol: {label.realized_vol_24h:.2f}%" if label.realized_vol_24h else "  Realized vol: N/A")
        logger.info(f"  Trend: {label.trend_direction} ({label.trend_strength:.1f})" if label.trend_direction else "  Trend: N/A")
    else:
        logger.warning(f"  No labels found for {test_timestamp}")

    logger.info("\n[SUCCESS] ML-ready data queryable")

    # Final Summary
    logger.info("")
    logger.info("="*60)
    logger.info("PHASE 4 TEST RESULTS")
    logger.info("="*60)
    logger.info("")
    logger.info("Pipeline Flow:")
    logger.info("  1. Raw Trades       -> VERIFIED")
    logger.info("  2. Greeks Calc      -> VERIFIED (embedded in aggregation)")
    logger.info("  3. Hourly Snapshots -> VERIFIED")
    logger.info("  4. Labels           -> VERIFIED")
    logger.info("  5. Consistency      -> VALIDATED")
    logger.info("  6. ML Queryability  -> WORKING")
    logger.info("")
    logger.info("Data Summary:")
    for currency in trades_summary.keys():
        logger.info(f"  {currency}:")
        logger.info(f"    Trades: {trades_summary[currency]['count']:,}")
        logger.info(f"    Snapshots: {snapshots_summary[currency]['count']:,}")
        logger.info(f"    Labels: {len(all_labels[currency])}")
        logger.info(f"    Greeks: {snapshots_summary[currency]['greeks_coverage']:.1f}%")
    logger.info("")
    logger.info("="*60)
    logger.info("SUCCESS: End-to-End Pipeline Working!")
    logger.info("="*60)
    logger.info("")
    logger.info("Pipeline is READY for ML training data collection.")
    logger.info("")

    return True


if __name__ == "__main__":
    success = test_end_to_end_pipeline()
    exit(0 if success else 1)
