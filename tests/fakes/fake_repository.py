"""
Fake DatabaseRepository that replays a recorded fixture for read methods and
records calls into a list (for assertion) instead of touching the database
for write methods. See refactor_design_spec.md section 7.2.

Only implements the methods ``OnChainAnalysisService.fetch_and_analyze`` and
its collaborators (``BuySellFlowAnalyzer``) actually call. Any other
``DatabaseRepository`` method is not defined here, so calling it raises a
normal ``AttributeError`` — a refactor that introduces a new DB call fails
loudly instead of silently hitting a real connection.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tests.fakes.fixture_io import load_json_gz

logger = logging.getLogger(__name__)


class FakeDatabaseRepository:
    """Fixture replayer for DatabaseRepository read methods; records writes."""

    def __init__(self, fixture_dir: Path):
        self.fixture_dir = Path(fixture_dir)
        db_dir = self.fixture_dir / "db"

        self._trades_for_flow: Dict[str, List[Dict[str, Any]]] = self._load_per_expiration(
            db_dir, "trades_for_flow_"
        )

        previous_oi_raw: Dict[str, List[List[Any]]] = self._load_per_expiration(
            db_dir, "previous_oi_"
        )
        self._previous_oi: Dict[str, Dict[Tuple[float, str], float]] = {
            exp: {(float(row[0]), row[1]): float(row[2]) for row in rows}
            for exp, rows in previous_oi_raw.items()
        }

        self._atm_iv_history: Dict[str, Dict[str, Any]] = self._load_per_expiration(
            db_dir, "atm_iv_history_"
        )
        self._onchain_snapshot_history: Dict[str, List[Dict[str, Any]]] = self._load_per_expiration(
            db_dir, "onchain_snapshot_history_"
        )

        # Write methods record calls here instead of hitting a real DB.
        self.saved_flow_metrics: List[Dict[str, Any]] = []
        self.saved_daily_oi_snapshots: List[Dict[str, Any]] = []

    @staticmethod
    def _load_per_expiration(db_dir: Path, prefix: str) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for path in sorted(db_dir.glob(f"{prefix}*.json.gz")):
            expiration = path.name[len(prefix):-len(".json.gz")]
            result[expiration] = load_json_gz(path)
        return result

    # ── Read methods (replay) ────────────────────────────────────────────────

    def get_trades_for_flow_analysis(
        self,
        currency: str,
        expiration: str,
        start_ts: int,
        end_ts: int,
        trade_filter: str = "all",
    ) -> List[Dict[str, Any]]:
        if expiration not in self._trades_for_flow:
            raise KeyError(f"No recorded trades_for_flow fixture for expiration {expiration!r}")
        return self._trades_for_flow[expiration]

    def get_previous_oi_snapshot(
        self,
        currency: str,
        expiration: str,
        before_date: Optional[Any] = None,
    ) -> Dict[Tuple[float, str], float]:
        if expiration not in self._previous_oi:
            raise KeyError(f"No recorded previous_oi fixture for expiration {expiration!r}")
        return self._previous_oi[expiration]

    def get_atm_iv_history(
        self,
        currency: str,
        expiration: str,
        strike: float,
        option_type: str = "C",
        limit: int = 90,
    ) -> List[Dict[str, Any]]:
        entry = self._atm_iv_history.get(expiration)
        if entry is None:
            raise KeyError(f"No recorded atm_iv_history fixture for expiration {expiration!r}")
        if abs(entry["strike"] - strike) > 1e-6:
            raise KeyError(
                f"ATM strike mismatch for {expiration!r}: fixture recorded {entry['strike']}, "
                f"pipeline computed {strike} — this usually means upstream logic changed how "
                "the ATM strike is derived, not a fixture problem."
            )
        return entry["history"]

    def get_onchain_snapshot_history(
        self, currency: str, expiration: str, limit: int = 2
    ) -> List[Dict[str, Any]]:
        if expiration not in self._onchain_snapshot_history:
            raise KeyError(
                f"No recorded onchain_snapshot_history fixture for expiration {expiration!r}"
            )
        return self._onchain_snapshot_history[expiration]

    # ── Write methods (record, never persist) ───────────────────────────────

    def save_flow_metrics(
        self,
        currency: str,
        expiration: str,
        flow_data: Dict[float, Dict[str, Dict[str, float]]],
        underlying_price: float,
        window_hours: int = 24,
        captured_at: Optional[Any] = None,
    ) -> int:
        self.saved_flow_metrics.append(
            {
                "currency": currency,
                "expiration": expiration,
                "flow_data": flow_data,
                "underlying_price": underlying_price,
                "window_hours": window_hours,
            }
        )
        return sum(len(type_data) for type_data in flow_data.values())

    def save_daily_oi_snapshot(
        self,
        currency: str,
        expiration: str,
        instruments: List[Dict[str, Any]],
        underlying_price: float,
        snapshot_date: Optional[Any] = None,
    ) -> int:
        self.saved_daily_oi_snapshots.append(
            {
                "currency": currency,
                "expiration": expiration,
                "instrument_count": len(instruments),
                "underlying_price": underlying_price,
            }
        )
        return len(instruments)
