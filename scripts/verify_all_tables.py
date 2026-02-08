"""
Check ALL database tables for recent data.
No assumptions - just facts.
"""

import logging
from datetime import datetime
from coding.core.database.repository import DatabaseRepository
from coding.core.logging.logging_setup import init_logging

init_logging(level="INFO")
logger = logging.getLogger(__name__)


def check_all_tables():
    """Check every table that might have recent data."""

    repo = DatabaseRepository()
    conn = repo._get_connection()

    try:
        cursor = conn.cursor()

        # Get list of all tables
        cursor.execute("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            ORDER BY table_name
        """)

        all_tables = [row[0] for row in cursor.fetchall()]

        print("=" * 80)
        print("CHECKING ALL TABLES FOR RECENT DATA")
        print("=" * 80)
        print(f"Current time: {datetime.now()}\n")

        for table_name in all_tables:
            print(f"\n{table_name}:")
            print("-" * 80)

            try:
                # Try to find timestamp columns
                cursor.execute(f"""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = '{table_name}'
                    AND (column_name LIKE '%timestamp%'
                         OR column_name LIKE '%captured_at%'
                         OR column_name LIKE '%created_at%'
                         OR column_name = 'generated_at'
                         OR column_name = 'trade_timestamp')
                """)

                time_columns = [row[0] for row in cursor.fetchall()]

                if time_columns:
                    time_col = time_columns[0]  # Use first timestamp column

                    # Get latest timestamp and count
                    cursor.execute(f"""
                        SELECT
                            MAX({time_col}) as latest,
                            COUNT(*) as total_rows
                        FROM {table_name}
                    """)

                    result = cursor.fetchone()
                    latest_time, total_rows = result

                    if latest_time:
                        # Convert timestamp to datetime if needed
                        if isinstance(latest_time, int):
                            # Unix timestamp in milliseconds
                            latest_time = datetime.fromtimestamp(latest_time / 1000)

                        hours_ago = (datetime.now() - latest_time).total_seconds() / 3600

                        print(f"  Latest: {latest_time}")
                        print(f"  Hours ago: {hours_ago:.2f}")
                        print(f"  Total rows: {total_rows}")

                        if hours_ago < 1:
                            print(f"  Status: RECENT DATA (< 1 hour)")
                        elif hours_ago < 24:
                            print(f"  Status: Data from today")
                        else:
                            print(f"  Status: Old data ({hours_ago/24:.1f} days ago)")
                    else:
                        print(f"  Status: EMPTY TABLE")
                else:
                    # No timestamp column - just get count
                    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                    count = cursor.fetchone()[0]
                    print(f"  Total rows: {count}")
                    print(f"  No timestamp column found")

            except Exception as e:
                print(f"  Error checking table: {e}")

        print("\n" + "=" * 80)
        print("SUMMARY - TABLES WITH RECENT DATA (< 2 hours):")
        print("=" * 80)

        for table_name in all_tables:
            try:
                cursor.execute(f"""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = '{table_name}'
                    AND (column_name LIKE '%timestamp%'
                         OR column_name LIKE '%captured_at%'
                         OR column_name LIKE '%created_at%'
                         OR column_name = 'generated_at'
                         OR column_name = 'trade_timestamp')
                """)

                time_columns = [row[0] for row in cursor.fetchall()]

                if time_columns:
                    time_col = time_columns[0]
                    cursor.execute(f"SELECT MAX({time_col}) FROM {table_name}")
                    latest = cursor.fetchone()[0]

                    if latest:
                        if isinstance(latest, int):
                            latest = datetime.fromtimestamp(latest / 1000)

                        hours_ago = (datetime.now() - latest).total_seconds() / 3600

                        if hours_ago < 2:
                            print(f"  {table_name}: {latest} ({hours_ago*60:.0f} min ago)")
            except:
                pass

        print("=" * 80)

    finally:
        cursor.close()
        repo._return_connection(conn)


if __name__ == "__main__":
    check_all_tables()
