"""
The one list of database tables the health checks watch.

Every table the data collection writes (everything in scripts/sync_from_vps.py
SYNC_TABLES) has an entry here; a test fails when a synced table is missing. For
each table: which column carries its time, how old the newest row may be before the
check warns or fails, and which columns must be filled.

Thresholds are in hours and follow the table's write cadence:
  - hourly tables are written every collection cycle (warn after 2 h, fail after 6 h;
    flow_delta_hourly warns after 3 h because it stores the previous hour)
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
            required: Tuple[str, ...] = (), optional: bool = False, warn_hours: float = 2.0) -> TableSpec:
    return TableSpec(table, time_column, time_kind, warn_hours, 6.0, required, optional)


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
    # Stores the hour that has just closed, written by the cycle after it: at the top of an hour
    # the newest bucket is already 2 hours old.
    _hourly("flow_delta_hourly", "snapshot_hour", warn_hours=3.0),
    _hourly("futures_basis", "timestamp"),
    # MenthorQ-style level monitor (migration 031): one live row per hour plus one daily-close
    # row per day. Optional: written only with MENTHORQ_MONITOR_ENABLED=1 (VPS). A row whose levels
    # could not be computed has NULLs, which the completeness check reports.
    _hourly("menthorq_level_snapshots", "computed_at", optional=True,
            required=("spot", "call_resistance", "put_support", "hvl", "total_net_gex")),
    # Hourly taker buy/sell volume of the Binance perpetuals (migration 033). Optional: written only with
    # TAKER_FLOW_ENABLED=1 (VPS). hour_utc is the START of the newest closed hour, so it is normally 1 to 2
    # hours old; the warning limit is 3.
    _hourly("taker_flow_hourly", "hour_utc", optional=True, required=("taker_buy_usd", "taker_sell_usd"),
            warn_hours=3.0),
    # Liquidation recorder (migration 034), optional: written only with LIQUIDATION_RECORDER_ENABLED=1 (VPS).
    # The snapshot runs at :12 every hour. OKX BTC-USDT-SWAP has about 300 liquidations an hour, so no event for 3 hours
    # means the recorder is not working (warning at 3 h, failure at 6 h).
    _hourly("liquidation_heatmap_snapshots", "computed_at", optional=True),
    _hourly("liquidation_events", "event_time", optional=True, warn_hours=3.0),
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
