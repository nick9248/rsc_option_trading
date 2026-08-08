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
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, List, Optional

import psycopg2

from scripts.sync_from_vps import (
    LOCAL_CONN,
    SYNC_TABLES,
    VPS_TUNNEL_CONN,
    _pull_health_json,
    open_ssh_tunnel,
    sync_table,
)

logger = logging.getLogger(__name__)


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

        start = datetime.now()
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

            total_rows = 0
            errors: List[str] = []

            for table in SYNC_TABLES:
                count, msg = sync_table(vps_conn, local_conn, table)
                status = "OK " if count >= 0 else "ERR"
                _emit(f"  [{status}] {table['name']}: {msg}")
                if count > 0:
                    total_rows += count
                if count < 0:
                    errors.append(table["name"])

            _pull_health_json()

            duration = (datetime.now() - start).total_seconds()
            return VpsSyncResult(
                success=len(errors) == 0,
                total_rows=total_rows,
                errors=errors,
                duration_seconds=duration,
            )

        finally:
            if vps_conn is not None:
                vps_conn.close()
            if local_conn is not None:
                local_conn.close()
            if tunnel is not None:
                tunnel.terminate()
