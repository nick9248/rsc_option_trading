"""
Regression test for scripts/sync_from_vps.py's SYNC_TABLES configuration
(institutional_metrics_spec.md section 6 / infra_spec.md section 2 --
task C7 review fix, Important #3).

The daemon writes flow_delta_hourly on the VPS only; the GUI/report read
from the local DB. Without an entry in SYNC_TABLES, local queries return
empty indefinitely (never populated by any path) -- this test locks in
that the table is actually configured to sync, with the correct watermark
column and conflict target matching the table's own unique constraint
(migration 021 / dbf6803: UNIQUE (snapshot_hour, currency, expiration)).

Import-only test (no DB connection, no SSH tunnel) -- sync_from_vps.py has
no import-time side effects beyond logging.basicConfig and load_dotenv.
"""

from scripts.sync_from_vps import SYNC_TABLES


def _find(name):
    return next((t for t in SYNC_TABLES if t["name"] == name), None)


class TestFlowDeltaHourlySyncEntry:
    def test_flow_delta_hourly_is_present(self):
        assert _find("flow_delta_hourly") is not None, (
            "flow_delta_hourly missing from SYNC_TABLES -- local queries "
            "against this table will return empty indefinitely"
        )

    def test_watermark_column_is_snapshot_hour(self):
        entry = _find("flow_delta_hourly")
        assert entry["watermark_col"] == "snapshot_hour"
        assert entry["watermark_type"] == "timestamp"

    def test_conflict_target_matches_the_table_s_unique_constraint(self):
        """Migration 021 (dbf6803): UNIQUE (snapshot_hour, currency, expiration)."""
        entry = _find("flow_delta_hourly")
        assert entry["conflict_target"] == "(snapshot_hour, currency, expiration) DO NOTHING"

    def test_every_sync_table_entry_has_required_keys(self):
        """Sanity check the whole list still has the expected shape after
        this edit (catches an accidental malformed entry)."""
        required_keys = {"name", "watermark_col", "watermark_type", "conflict_target"}
        for entry in SYNC_TABLES:
            assert required_keys.issubset(entry.keys()), entry
