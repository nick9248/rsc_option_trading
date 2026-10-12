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


class TestLiveTablesAreSynced:
    """Five live tables were missing from the sync list (found 2026-10-11)."""

    # Never changed after insertion. futures_basis and position_snapshots put several rows
    # on one hour: the tie-safe batches cover that.
    APPEND_ONLY = (
        "futures_basis", "account_snapshots", "position_snapshots", "user_trades", "settlements",
    )
    MUTABLE = {
        # Rows are updated on the VPS after insertion (alerts, settlement results).
        "forward_test_predictions": ["currency", "snapshot_hour"],
        "straddle_scan_history": ["currency", "expiration", "scan_time"],
        "defined_risk_scan_history": ["currency", "expiration", "structure_type", "scan_time"],
        # Rewritten in place on the VPS (ON CONFLICT ... DO UPDATE) when recomputed.
        "daily_oi_snapshots": ["snapshot_date", "snapshot_hour_utc", "currency", "expiration", "strike", "option_type"],
        "volatility_skew_history": ["snapshot_hour", "currency", "expiration"],
        # Final candle closes, corrected in place for the last 48 h every cycle.
        "dvol_history": ["asset", "timestamp"],
        # An order changes while it is open.
        "orders": ["order_id"],
    }

    def test_append_only_tables_are_present_with_a_do_nothing_conflict_target(self):
        for name in self.APPEND_ONLY:
            entry = _find(name)
            assert entry is not None, f"{name} missing from SYNC_TABLES"
            assert entry["conflict_target"].endswith("DO NOTHING"), entry
            assert "upsert_key" not in entry

    def test_mutable_tables_are_present_with_their_unique_key(self):
        for name, key in self.MUTABLE.items():
            entry = _find(name)
            assert entry is not None, f"{name} missing from SYNC_TABLES"
            assert entry["upsert_key"] == key
            assert entry["conflict_target"] is None  # unused for upsert tables

    def test_dead_tables_are_not_synced(self):
        dropped = {
            "calculated_greeks", "data_quality_checks", "collection_logs",
            "collection_completeness", "hourly_trade_volume", "latest_hourly_snapshots",
            "volatility_index_history",  # merged into dvol_history
            "max_pain", "open_interest", "volume",  # onchain_analysis_snapshots has them hourly
            "buy_sell_flow_metrics",  # computed on demand from historical_trades
        }
        assert dropped.isdisjoint({entry["name"] for entry in SYNC_TABLES})

    def test_table_names_are_unique(self):
        names = [entry["name"] for entry in SYNC_TABLES]
        assert len(names) == len(set(names))


class TestLookbackWindows:
    """Tables whose rows change only shortly after insertion re-read just a recent window.
    Tables whose rows change long after insertion (resolved scans) must be re-read in full."""

    def test_recent_window_tables(self):
        assert _find("dvol_history")["lookback_hours"] == 72  # daemon repairs the last 48 h
        assert _find("daily_oi_snapshots")["lookback_hours"] == 6
        assert _find("volatility_skew_history")["lookback_hours"] == 6

    def test_tables_resolved_long_after_insertion_are_re_read_in_full(self):
        for name in ("forward_test_predictions", "straddle_scan_history", "defined_risk_scan_history"):
            assert "lookback_hours" not in _find(name), name

    def test_lookback_is_only_used_with_an_upsert_key_and_a_timestamp_watermark(self):
        for entry in SYNC_TABLES:
            if "lookback_hours" in entry:
                assert entry.get("upsert_key"), entry["name"]
                assert entry["watermark_type"] == "timestamp", entry["name"]
