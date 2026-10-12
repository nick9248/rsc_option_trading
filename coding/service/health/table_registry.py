"""
The one list of database tables the health checks watch.

Every table the data collection writes (everything in scripts/sync_from_vps.py
SYNC_TABLES) has an entry here; a test fails when a synced table is missing. For
each table: which column carries its time, how old the newest row may be before the
check warns or fails, and which columns must be filled.

Thresholds are in hours and follow the table's write cadence:
  - hourly tables are written every collection cycle (warn after 2 h, fail after 6 h)
  - dvol_history stores completed hourly candles, so its newest row is up to ~2 h old
  - daily tables are written once a day
  - event tables (our own fills, orders, settlements, positions) only change when we
    trade, so they have no freshness limit (``warn_hours`` is None)
"""
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class TableSpec:
    """What the health checks expect from one table."""

    table: str
    time_column: str
    time_kind: str  # "datetime" (timestamp or timestamptz), "date", or "ms_epoch" (bigint)
    warn_hours: Optional[float]  # None: event table, no freshness limit
    fail_hours: Optional[float]
    required_columns: Tuple[str, ...] = ()
    optional: bool = False  # True: an empty table is a warning (feature may be switched off)


def _hourly(table: str, time_column: str, time_kind: str = "datetime",
            required: Tuple[str, ...] = (), optional: bool = False) -> TableSpec:
    return TableSpec(table, time_column, time_kind, 2.0, 6.0, required, optional)


def _event(table: str, time_column: str) -> TableSpec:
    return TableSpec(table, time_column, "datetime", None, None, (), optional=True)


TABLE_SPECS: Tuple[TableSpec, ...] = (
    _hourly("historical_trades", "trade_timestamp", "ms_epoch", required=("direction", "iv")),
    _hourly("snapshots", "captured_at"),
    _hourly("hourly_snapshots", "snapshot_hour"),
    _hourly("onchain_analysis_snapshots", "snapshot_hour", required=("underlying_price",)),
    _hourly("onchain_volatility_snapshots", "snapshot_hour",
            required=("atm_iv", "iv_percentile_expiry", "iv_percentile_365d", "realized_vol", "underlying_price")),
    _hourly("volatility_skew_history", "snapshot_hour"),
    _hourly("flow_delta_hourly", "snapshot_hour"),
    _hourly("straddle_scan_history", "scan_time",
            required=("strike", "cost_usd", "breakeven_down", "breakeven_up",
                      "iv_percentile", "iv_percentile_n_obs", "iv_percentile_window_days")),
    _hourly("defined_risk_scan_history", "scan_time"),
    _hourly("forward_test_predictions", "snapshot_hour",
            required=("signal_direction", "signal_score", "signal_confidence", "spot_price_at_prediction")),
    _hourly("futures_basis", "timestamp"),
    _hourly("funding_rate_history", "timestamp", "ms_epoch"),
    TableSpec("dvol_history", "timestamp", "datetime", 3.0, 6.0, ("dvol_value",)),
    TableSpec("ohlcv_history", "date", "datetime", 48.0, 120.0, ("close",)),
    TableSpec("daily_oi_snapshots", "snapshot_date", "date", 36.0, 72.0),
    # Our own Deribit account (needs credentials, so an empty table is only a warning).
    _hourly("account_snapshots", "snapshot_hour", optional=True),
    _event("position_snapshots", "snapshot_hour"),
    _event("user_trades", "trade_time"),
    _event("orders", "last_update_at"),
    _event("settlements", "settled_at"),
)
