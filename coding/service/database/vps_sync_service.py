"""
VPS -> local database sync, as a Service-layer class.

Wave G task G2-F fix 3: coding/gui/tabs/database_tab.py's SyncWorker (a
QThread) used to `import psycopg2` directly inside the GUI file, open two
raw connections (VPS + local) itself, and drive the table-sync loop --
business logic and direct DB access in the GUI layer, which this repo's
CLAUDE.md Code Quality Checklist explicitly forbids ("GUI/CLI never
contains business logic or direct API calls; services orchestrate using
base methods"). It also had a real resource leak: the `except Exception`
handler had no `finally`, so a mid-loop failure (e.g. one table's
sync_table raising, or the SSH tunnel dying) left the SSH tunnel and both
DB connections open forever.

This module does not reinvent VPS-sync logic a third time -- it reuses
scripts/sync_from_vps.py's SYNC_TABLES / sync_table / open_ssh_tunnel /
_pull_health_json directly (that script is the one place the actual sync
mechanics -- watermark columns, conflict targets, per-table column
introspection -- are defined; duplicating it here would be a second source
of truth to drift). What this class adds is: (1) a Service-layer home for
the orchestration so the GUI can call one method instead of managing
connections itself, (2) a `progress_callback` hook so callers (GUI or CLI)
get per-step updates without this class depending on Qt, and (3) a
try/finally around tunnel + connection cleanup -- mirroring the pattern in
coding.core.database.repository.DatabaseRepository._db_cursor and
scripts/sync_from_vps.py's own run() function -- so nothing leaks on
failure. Exceptions from sync_table itself are already caught inside
sync_table (returns (-1, "ERROR: ...") rather than raising); only cleanup
lives in this class's try/finally, so a raise from open_ssh_tunnel,
psycopg2.connect, or _pull_health_json still propagates to the caller
after cleanup runs -- callers (e.g. SyncWorker) decide how to report that,
this class does not swallow it.
"""
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Sequence

import psycopg2

from scripts.sync_from_vps import (
    LOCAL_CONN,
    SYNC_TABLES,
    VPS_TUNNEL_CONN,
    _pull_health_json,
    get_columns,
    get_local_watermark,
    open_ssh_tunnel,
    sync_table,
)

logger = logging.getLogger(__name__)

# The tables the market report reads.
REPORT_SYNC_TABLES = ("historical_trades", "snapshots", "taker_flow_hourly", "liquidation_heatmap_snapshots")


@dataclass
class VpsSyncResult:
    """Outcome of one VpsSyncService.sync() run."""

    success: bool
    total_rows: int
    errors: List[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def summary_message(self) -> str:
        """Human-readable one-line summary, same wording the old GUI-embedded logic used."""
        summary = f"Synced {self.total_rows:,} rows in {self.duration_seconds:.1f}s"
        if self.errors:
            summary += f" — {len(self.errors)} error(s): {', '.join(self.errors)}"
        return summary


@dataclass
class WatermarkComparison:
    """Newest watermark value of one table on the VPS versus locally."""

    table: str
    column: str                      # the table's watermark column from SYNC_TABLES
    vps_newest: Optional[Any]        # MAX(column) on the VPS, None when the table is empty there
    local_newest: Optional[Any]      # MAX(column) locally, None when empty
    error: Optional[str] = None      # why the comparison could not be made (the table counts as "unknown")
    missing_on_vps: bool = False     # optional table that does not exist on the VPS

    @property
    def behind(self) -> bool:
        """True when the VPS holds newer rows than the local database."""
        if self.error is not None or self.missing_on_vps or self.vps_newest is None:
            return False
        return self.local_newest is None or self.vps_newest > self.local_newest

    @property
    def unknown(self) -> bool:
        """True when the comparison could not be made."""
        return self.error is not None


@dataclass
class SyncCheckResult:
    """Outcome of VpsSyncService.sync_if_behind(): the comparisons and the sync, if one ran."""

    comparisons: List[WatermarkComparison]
    sync_ran: bool
    sync_result: Optional[VpsSyncResult] = None

    @property
    def tables_behind(self) -> List[str]:
        """Names of tables where the VPS is ahead."""
        return [comparison.table for comparison in self.comparisons if comparison.behind]

    @property
    def tables_unknown(self) -> List[str]:
        """Names of tables that could not be compared."""
        return [comparison.table for comparison in self.comparisons if comparison.unknown]

    @property
    def summary_message(self) -> str:
        """One plain-language line describing the check and the sync."""
        parts = [f"Checked {len(self.comparisons)} tables against the VPS: "]
        notes: List[str] = []
        if self.tables_behind:
            notes.append(f"{', '.join(self.tables_behind)} behind")
        unknown = [
            f"{comparison.table} ({comparison.error})"
            for comparison in self.comparisons
            if comparison.unknown
        ]
        if unknown:
            notes.append(f"could not compare {', '.join(unknown)}")
        missing = [comparison.table for comparison in self.comparisons if comparison.missing_on_vps]
        if missing:
            notes.append(f"not on the VPS: {', '.join(missing)}")
        if not self.tables_behind and not unknown:
            notes.insert(0, "all up to date, no sync needed")
        message = parts[0] + "; ".join(notes)
        if self.sync_result is not None:
            message += f"; {self.sync_result.summary_message[0].lower()}{self.sync_result.summary_message[1:]}"
        return message


def compare_watermarks(vps_cursor, local_cursor, table_names: Sequence[str]) -> List[WatermarkComparison]:
    """
    Read MAX(watermark column) of each table on both sides and compare.

    Raises ValueError for a name that is not in SYNC_TABLES (programming error).
    Any other failure is recorded in that table's ``error`` so the rest still run.
    """
    entries = {entry["name"]: entry for entry in SYNC_TABLES}
    comparisons: List[WatermarkComparison] = []
    for name in table_names:
        if name not in entries:
            raise ValueError(f"{name} is not in SYNC_TABLES")
        entry = entries[name]
        column = entry["watermark_col"]
        vps_newest = None
        local_newest = None
        try:
            if not get_columns(vps_cursor, name):
                if entry.get("optional"):
                    comparisons.append(WatermarkComparison(name, column, None, None, missing_on_vps=True))
                else:
                    comparisons.append(
                        WatermarkComparison(name, column, None, None, error="table not found on the VPS")
                    )
                continue
            vps_newest = get_local_watermark(vps_cursor, entry)
            local_newest = get_local_watermark(local_cursor, entry)
            error = None
            if vps_newest is not None and local_newest is not None:
                try:
                    vps_newest > local_newest
                except TypeError:
                    error = f"cannot compare {vps_newest!r} with {local_newest!r}"
            comparisons.append(WatermarkComparison(name, column, vps_newest, local_newest, error=error))
        except Exception as error:  # one table's failure must not stop the others
            logger.warning("Watermark comparison failed for %s: %s", name, error)
            try:
                local_cursor.connection.rollback()
            except Exception as rollback_error:
                logger.warning("Local rollback failed (ignored): %s", rollback_error)
            comparisons.append(
                WatermarkComparison(
                    name, column, vps_newest, local_newest,
                    error=f"{type(error).__name__}: {error}",
                )
            )
    return comparisons


class VpsSyncService:
    """
    Orchestrates a full VPS -> local sync: opens the SSH tunnel, connects to
    both databases, syncs every table in SYNC_TABLES, pulls the health JSON,
    and always closes the tunnel + connections via try/finally, even if a
    step raises partway through.
    """

    def sync(self, progress_callback: Optional[Callable[[str], None]] = None) -> VpsSyncResult:
        """
        Run one full VPS -> local sync.

        Args:
            progress_callback: optional callable invoked with a
                human-readable progress line after each step (tunnel open,
                each table synced, health-json pull). GUI callers wire this
                to a Qt signal; CLI/test callers can pass ``logger.info`` or
                leave it as None.

        Returns:
            VpsSyncResult with the overall success flag, total rows synced,
            and the names of any tables that errored.

        Raises:
            Whatever open_ssh_tunnel/psycopg2.connect/_pull_health_json
            raise -- this method guarantees cleanup (tunnel/connections
            closed) before the exception propagates, it does not suppress
            it. Per-table sync failures do NOT raise here: sync_table
            already catches its own exceptions and reports them via the
            returned VpsSyncResult.errors instead.
        """
        def _emit(message: str) -> None:
            if progress_callback is not None:
                progress_callback(message)

        # Wave G re-review (Minor, folded in from the G2-A completeness-
        # disclosure round): this is an ELAPSED-duration measurement, not
        # a wall-clock timestamp -- time.monotonic() is immune to a DST
        # step or NTP correction landing mid-sync, unlike datetime.now(),
        # which would otherwise silently produce a wildly wrong (even
        # negative) duration. Same bug class this campaign keeps
        # re-finding under the naive-local-vs-UTC banner, just manifesting
        # here as "wrong clock source for a duration" instead.
        start = time.monotonic()
        tunnel = None
        vps_conn = None
        local_conn = None

        try:
            _emit("Opening SSH tunnel to VPS...")
            tunnel = open_ssh_tunnel()
            _emit("Tunnel established. Connecting to databases...")

            vps_conn = psycopg2.connect(**VPS_TUNNEL_CONN)
            vps_conn.autocommit = True
            local_conn = psycopg2.connect(**LOCAL_CONN)

            result = self._sync_all_tables(vps_conn, local_conn, _emit)
            result.duration_seconds = time.monotonic() - start
            return result

        finally:
            self._close_all(vps_conn, local_conn, tunnel)

    def sync_if_behind(
        self,
        table_names: Sequence[str] = REPORT_SYNC_TABLES,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> SyncCheckResult:
        """
        Compare the newest rows of ``table_names`` with the VPS and run the full
        incremental sync only when the VPS is ahead (or a table cannot be compared).

        Uses one SSH tunnel and one pair of connections for the check and the sync.
        Raises whatever open_ssh_tunnel / psycopg2.connect raise, after cleanup.
        """
        def _emit(message: str) -> None:
            if progress_callback is not None:
                progress_callback(message)

        tunnel = None
        vps_conn = None
        local_conn = None

        try:
            _emit("Opening SSH tunnel to VPS...")
            tunnel = open_ssh_tunnel()
            _emit("Tunnel established. Connecting to databases...")

            vps_conn = psycopg2.connect(**VPS_TUNNEL_CONN)
            vps_conn.autocommit = True
            local_conn = psycopg2.connect(**LOCAL_CONN)

            _emit(f"Comparing the newest rows of {len(table_names)} tables with the VPS...")
            vps_cursor = vps_conn.cursor()
            local_cursor = local_conn.cursor()
            try:
                comparisons = compare_watermarks(vps_cursor, local_cursor, table_names)
            finally:
                for cursor in (vps_cursor, local_cursor):
                    try:
                        cursor.close()
                    except Exception as error:  # best effort: must not mask the real outcome
                        logger.warning("Closing a cursor failed (ignored): %s", error)
            # End the local read transaction before the sync starts its own work.
            local_conn.rollback()

            for comparison in comparisons:
                _emit(f"  {self._describe_comparison(comparison)}")

            check = SyncCheckResult(comparisons=comparisons, sync_ran=False)
            if not (check.tables_behind or check.tables_unknown):
                return check

            start = time.monotonic()
            check.sync_result = self._sync_all_tables(vps_conn, local_conn, _emit)
            check.sync_result.duration_seconds = time.monotonic() - start
            check.sync_ran = True
            return check

        finally:
            self._close_all(vps_conn, local_conn, tunnel)

    @staticmethod
    def _describe_comparison(comparison: WatermarkComparison) -> str:
        """One progress line for one compared table."""
        if comparison.error is not None:
            return f"{comparison.table}: could not compare ({comparison.error})"
        if comparison.missing_on_vps:
            return f"{comparison.table}: not on the VPS (optional table)"
        state = "behind" if comparison.behind else "up to date"
        return (
            f"{comparison.table}: VPS {comparison.vps_newest}, "
            f"local {comparison.local_newest} ({state})"
        )

    def _sync_all_tables(self, vps_connection, local_connection, emit: Callable[[str], None]) -> VpsSyncResult:
        """Sync every table in SYNC_TABLES, then pull the health JSON. Duration is left at 0."""
        total_rows = 0
        errors: List[str] = []

        for table in SYNC_TABLES:
            count, msg = sync_table(vps_connection, local_connection, table)
            status = "OK " if count >= 0 else "ERR"
            emit(f"  [{status}] {table['name']}: {msg}")
            if count > 0:
                total_rows += count
            if count < 0:
                errors.append(table["name"])

        _pull_health_json()

        return VpsSyncResult(success=len(errors) == 0, total_rows=total_rows, errors=errors)

    @staticmethod
    def _close_all(vps_conn, local_conn, tunnel) -> None:
        """Close whatever was opened: connections first, then the tunnel."""
        if vps_conn is not None:
            vps_conn.close()
        if local_conn is not None:
            local_conn.close()
        if tunnel is not None:
            tunnel.terminate()
