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
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from coding.core.database.repository import DatabaseRepository
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
        # Task Wave-J-A Fix 4: the real DatabaseRepository.get_atm_iv_history
        # reads a psycopg2 DATE column, which comes back as a real
        # datetime.date object -- never a string. The recorded JSON fixture
        # only has strings (JSON has no native date type), so without this
        # parse, this fake would hand callers a shape the real repository
        # never produces, silently hiding a TypeError this fixture should
        # have caught (`str - str` in the service's calendar-span
        # calculation raises, `date - date` does not).
        for entry in self._atm_iv_history.values():
            for row in entry.get("history", []):
                if isinstance(row.get("snapshot_date"), str):
                    row["snapshot_date"] = date.fromisoformat(row["snapshot_date"])
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

    def get_first_trade_timestamp(self, currency: str, expiration: str) -> Optional[int]:
        """
        institutional_metrics_spec.md section 2 / task C3: this offline
        fixture never recorded ``historical_trades`` (it captures one live
        book-summary/ticker snapshot, not the trade-history table) -- returns
        None honestly, same reasoning as get_metric_history/get_metric_
        freshness below. ``DealerInventoryCalculator``'s T0 then falls back
        to the coverage-stable date, and D9's gate fails on zero
        trade-history coverage (see get_trade_hour_coverage below) -- a
        legitimate "INFERRED DEALER VIEW UNAVAILABLE" golden delta (task C3:
        new additive section), not a silently degraded/masked computation.

        Task C3 review (self-caught): the FIRST implementation of this fake
        omitted these three methods entirely, relying on
        OnChainAnalysisService._calculate_inferred_dealer_positioning's own
        broad ``except Exception`` guard to swallow the resulting
        AttributeError. That defeated this fake's whole stated purpose (its
        module docstring: "a refactor that introduces a new DB call fails
        loudly instead of silently hitting a real connection") -- the
        characterization suite passed, but without ever exercising the D9
        gate at all. Caught by checking the golden report for the new
        section text and finding it absent.
        """
        return None

    def get_signed_taker_flow_by_strike(
        self, currency: str, expiration: str, since_ts: int
    ) -> List[Dict[str, Any]]:
        """No recorded ``historical_trades`` fixture -- honestly returns no
        signed-flow rows (see get_first_trade_timestamp above)."""
        return []

    def get_trade_hour_coverage(
        self, currency: str, since_ts: int
    ) -> Tuple[int, int]:
        """
        Fix round (Important #2): signature dropped ``expiration`` --
        real coverage is table-wide (currency-wide) now, not per-expiry.

        No recorded ``historical_trades`` fixture -- honestly returns
        ``(0, 0)`` (zero present hours; zero expected hours too, since this
        fake does not reproduce the real repository's wall-clock
        ``datetime.now(timezone.utc)`` formula -- doing so would add a
        second, harder-to-keep-frozen-clock-synchronized "now" dependency
        for no benefit, as the service's own division-by-zero guard already
        treats ``expected_hours == 0`` as coverage 0.0, which correctly
        fails D9's gate either way).
        """
        return (0, 0)

    def get_metric_history(
        self,
        table: str,
        column: str,
        currency: str,
        lookback_hours: int,
        expiration: Optional[str] = None,
        time_column: Optional[str] = None,
    ) -> List[float]:
        """
        institutional_metrics_spec.md section 1: this offline fixture never
        recorded a trailing 30d/90d metric-history table (it captures one
        live hour), so this honestly returns no history rather than
        fabricating one -- HistoricalNormalizer's own MIN_OBS gate then
        renders every percentile/z line as "insufficient history", a
        legitimate and explained golden-master delta (task-C1: new
        percentile/z display), not a silent degraded computation.

        C1 review Critical #1: this used to return ``[]`` unconditionally,
        with NO whitelist check -- which meant a caller passing an
        un-whitelisted (table, column) pair (e.g. a composite SQL
        expression like ``"(total_call_oi + total_put_oi)"``, never added
        to ``DatabaseRepository._METRIC_HISTORY_WHITELIST``) sailed through
        the golden master completely unnoticed, even though the REAL
        repository would raise ``ValueError`` on every single production
        run. Delegating the whitelist check to the real class (never
        touching the DB -- ``DatabaseRepository.__new__`` skips
        ``__init__``'s connection-pool setup) closes that gap: a future
        un-whitelisted (table, column) pair now fails the golden master
        loudly, the same way it fails in production.
        """
        key = (table, column)
        if key not in DatabaseRepository._METRIC_HISTORY_WHITELIST:
            raise ValueError(
                f"get_metric_history: ({table!r}, {column!r}) is not whitelisted. "
                f"Allowed pairs: {sorted(DatabaseRepository._METRIC_HISTORY_WHITELIST)}"
            )
        return []

    def get_metric_history_oldest_timestamp(
        self,
        table: str,
        column: str,
        currency: str,
        lookback_hours: int,
        expiration: Optional[str] = None,
        time_column: Optional[str] = None,
    ) -> Optional[datetime]:
        """
        Task G2-E: this offline fixture never recorded a metric-history
        table (see ``get_metric_history`` above), so there is no oldest
        timestamp to replay either -- returns None honestly. Combined with
        ``get_metric_history`` returning ``[]``, every normalized metric's
        n_30d/n_90d is already 0 against this fixture, so the calendar-span
        gate this method feeds never has a chance to matter here (n=0 <
        MIN_OBS already fails the count gate on its own) -- this is purely
        about not raising AttributeError on the new call, same as
        get_metric_freshness's existing "un-whitelisted call fails loudly,
        even offline" contract below.
        """
        key = (table, column)
        if key not in DatabaseRepository._METRIC_HISTORY_WHITELIST:
            raise ValueError(
                f"get_metric_history_oldest_timestamp: ({table!r}, {column!r}) is not "
                f"whitelisted. Allowed pairs: {sorted(DatabaseRepository._METRIC_HISTORY_WHITELIST)}"
            )
        return None

    def get_metric_freshness(
        self,
        table: str,
        currency: str,
        expiration: Optional[str] = None,
        time_column: Optional[str] = None,
    ) -> Optional[datetime]:
        """
        institutional_metrics_spec.md section 1(c) / C1 review Important #4:
        this offline fixture never recorded a freshness timestamp either --
        returns None honestly (no STALE prefix renders against the
        fixture), same reasoning as get_metric_history above. Still
        validates the table against the real whitelist for the same
        "un-whitelisted call fails loudly, even offline" reason.
        """
        if table not in DatabaseRepository._TABLE_TIME_COLUMNS:
            raise ValueError(f"get_metric_freshness: table {table!r} is not whitelisted")
        return None

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
