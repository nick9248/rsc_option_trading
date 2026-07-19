"""Local check comparing local DB state against the last-synced VPS snapshot."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from coding.core.health.models import CheckEnvironment, CheckResult, CheckStatus
from coding.service.health.base import HealthCheck

_DEFAULT_HEALTH_PATH = Path(__file__).parents[4] / "logs" / "vps_health.json"
_ROW_LAG_WARN_PCT = 1.0


class DatabaseSyncGapCheck(HealthCheck):
    """
    Compares local DB row counts against the VPS's last-known-good numbers
    (synced vps_health.json) to answer "is my local copy caught up" --
    distinct from VPS-side internal continuity (DatabaseVpsGapCheck).
    """

    category = "Database — VPS Sync"
    environment = CheckEnvironment.LOCAL

    def __init__(self, health_json_path: Optional[Path] = None):
        self._health_json_path = health_json_path or _DEFAULT_HEALTH_PATH

    def run(self, repo) -> List[CheckResult]:
        if not self._health_json_path.exists():
            return [CheckResult(
                name="VPS sync status", status=CheckStatus.WARN,
                message="No logs/vps_health.json found — never synced from VPS yet",
            )]

        data = json.loads(self._health_json_path.read_text())
        tables = data.get("tables", {})

        if not tables:
            return [CheckResult(
                name="VPS sync status", status=CheckStatus.WARN,
                message="vps_health.json has no 'tables' section — run the updated check_vps_health.py on the VPS",
            )]

        results: List[CheckResult] = []
        conn = repo._get_connection()
        try:
            cursor = conn.cursor()
            try:
                for table, vps_info in tables.items():
                    try:
                        results.append(self._diff_result(cursor, table, vps_info))
                    except Exception as exc:
                        conn.rollback()
                        results.append(CheckResult(
                            name=f"{table} sync", status=CheckStatus.FAIL,
                            message=f"{table}: sync check failed: {exc}",
                        ))
            finally:
                cursor.close()
        finally:
            repo._return_connection(conn)
        return results

    def _diff_result(self, cursor, table: str, vps_info: Dict[str, Any]) -> CheckResult:
        vps_rows = vps_info.get("rows")

        if vps_rows is None:
            return CheckResult(
                name=f"{table} sync", status=CheckStatus.WARN,
                message=(
                    f"{table}: VPS-side row count unavailable "
                    f"({vps_info.get('error', 'unknown reason')}) — cannot compare"
                ),
                details={"vps_error": vps_info.get("error")},
            )

        cursor.execute(f"SELECT COUNT(*) FROM {table}")
        local_rows = cursor.fetchone()[0]

        rows_behind = max(vps_rows - local_rows, 0)
        pct_behind = (rows_behind / vps_rows * 100.0) if vps_rows else 0.0

        if pct_behind < _ROW_LAG_WARN_PCT:
            return CheckResult(
                name=f"{table} sync", status=CheckStatus.PASS,
                message=f"{table}: local caught up ({local_rows}/{vps_rows} rows)",
                details={"local_rows": local_rows, "vps_rows": vps_rows},
            )
        return CheckResult(
            name=f"{table} sync", status=CheckStatus.WARN,
            message=(
                f"{table}: local {rows_behind} rows behind VPS "
                f"({local_rows}/{vps_rows}) — run 'Sync from VPS' in the Database tab"
            ),
            details={"local_rows": local_rows, "vps_rows": vps_rows},
        )
