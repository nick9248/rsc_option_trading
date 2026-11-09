"""
The positional row mapping of ``DatabaseRepository._menthorq_level_record_from_row``
matches ``_MENTHORQ_LEVEL_COLUMNS`` (length and order), so a column added or
moved on one side only fails here. No database.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from coding.core.database.repository import DatabaseRepository

# Column name -> MenthorqLevelRecord attribute where the two differ.
RECORD_ATTRIBUTE_OF_COLUMN = {"id": "record_id", "as_of_utc": "as_of_utc", "computed_at": "computed_at_utc"}
SESSION_TIME_ZONE = timezone(timedelta(hours=2))   # timestamptz comes back in the session zone


def _columns():
    return [name.strip() for name in DatabaseRepository._MENTHORQ_LEVEL_COLUMNS.split(",")]


def _distinct_value(index: int, column: str):
    """A value unique to this column, of the type the database driver returns."""
    if column == "id":
        return 7
    if column.endswith("_utc") or column == "computed_at":
        return datetime(2026, 11, 6, 9, index, tzinfo=SESSION_TIME_ZONE)
    if column in ("spot", "call_resistance", "put_support", "hvl", "total_net_gex"):
        return Decimal(1000 + index)
    if column in ("expirations", "not_computed_reasons"):
        return [f"{column}-{index}"]
    if column in ("panel_levels", "iv_source_counts"):
        return {column: index}
    return f"{column}-{index}"


def test_every_column_lands_on_its_own_record_field():
    columns = _columns()
    assert len(columns) == len(set(columns)) == 20
    row = tuple(_distinct_value(index, column) for index, column in enumerate(columns))
    record = DatabaseRepository._menthorq_level_record_from_row(row)
    for index, column in enumerate(columns):
        value = getattr(record, RECORD_ATTRIBUTE_OF_COLUMN.get(column, column))
        expected = row[index]
        if isinstance(expected, datetime):
            assert value == expected and value.tzinfo == timezone.utc, column
        elif isinstance(expected, Decimal):
            assert value == float(expected) and isinstance(value, float), column
        else:
            assert value == expected, column


def test_null_optional_columns_stay_none():
    columns = _columns()
    nullable = {"spot", "spot_time_utc", "snapshot_captured_at_utc", "open_interest_captured_at_utc",
                "call_resistance", "put_support", "hvl", "total_net_gex", "gamma_regime"}
    row = tuple(None if column in nullable else _distinct_value(index, column)
                for index, column in enumerate(columns))
    record = DatabaseRepository._menthorq_level_record_from_row(row)
    assert all(getattr(record, column) is None for column in nullable)
