"""
Straddle Deal-Quality Backtest

Question: which entry-time "deal quality" metrics predicted realized P&L of a
long ATM straddle (buy call + put, same strike) on BTC and ETH options?

Candidate metrics come from the live-scanner design doc: VRP (DTE-matched and
30d), DVOL/IV percentile, a min-P&L-at-realized-range-edges score, raw cost
percentage, vol-momentum ratio, dealer gamma exposure, and funding stress.

DATA SOURCES (all local DB, no external calls):
  hourly_snapshots               - per (hour, currency, instrument): vwap,
                                    mark_price, index_price. Used to build the
                                    ATM straddle entry (strike, cost) and the
                                    hourly underlying-price proxy series.
  onchain_volatility_snapshots    - per (hour, currency, expiration): atm_iv,
                                    iv_percentile_expiry, iv_percentile_365d.
                                    iv_percentile_365d is *already* a DVOL
                                    percentile-vs-365d reconstruction (see
                                    VolatilityReconstructionService._reconstruct
                                    _market_metrics) - reused directly rather
                                    than recomputed from dvol_history.
  onchain_analysis_snapshots      - per (hour, currency, expiration):
                                    total_net_gex, total_call_oi, total_put_oi.
  ohlcv_history                   - daily (currency, date, close). date is
                                    stamped 08:00 UTC - the same instant
                                    Deribit settles options. Used for (a) the
                                    realized-vol lookback series and (b) the
                                    settlement price (exact-time match).
  funding_rate_history            - (currency, date, funding_rate), ~hourly.

CRITICAL DESIGN DECISIONS (read before trusting the numbers):
  PRICE BASIS: option cost = vwap (preferred) or mark_price (fallback) in
    coin terms, multiplied by that hour's underlying/index price -> USD.
    Historical bid/ask columns exist but are NOT used as if they were
    executable quotes at entry time - the aggregation pipeline populates them
    from the last observed quote within the hourly bucket, not a guaranteed
    fill price. This means entry costs are optimistic relative to what a
    trader crossing the spread would actually pay.
  UNDERLYING/FORWARD: futures_price is NULL for 100% of hourly_snapshots rows
    in this DB (checked directly) - there is no stored forward curve. F is
    approximated by the Deribit index price at entry, so "cost_pct = cost/F"
    is a spot-based moneyness measure, not a true forward-moneyness measure.
  SETTLEMENT: primary source is ohlcv_history.close on the expiry's calendar
    date (its `date` column is stamped 08:00 UTC, exactly Deribit's daily
    settlement instant - no approximation needed when that row exists).
    Fallback: nearest hourly_snapshots index_price within +/-6h of
    expiry_date 08:00 UTC (used only when the ohlcv row is missing).
    Expiries with neither available are excluded (logged as a caveat).
  REALIZED VOL: log returns of daily OHLCV closes, std * sqrt(365), matching
    VRPCalculator's existing methodology (core/analytics/vrp_calculator.py)
    but computed here for arbitrary trailing windows (DTE-matched, 10d, 30d)
    since only a fixed 30d value is persisted historically.
  GRAIN / PSEUDO-REPLICATION: one raw observation per (currency, expiration,
    snapshot_hour) that has DTE in [5, 120]. Adjacent hourly observations
    within the same expiry share almost the same outcome (the realized
    straddle P&L at settlement) - naive hourly correlation would wildly
    overstate significance. PRIMARY statistic is Spearman rho on entries
    aggregated to (currency, expiration, entry_day) [mean of metric and
    return within the day]. ROBUSTNESS check collapses further to one row
    per (currency, expiration) [median across all its day-level rows].
  MULTIPLE TESTING: Benjamini-Hochberg FDR at q=0.10 across the 10 metrics x
    2 currencies = 20 day-grain tests (pattern follows scripts/phase2_backtest.py).
  WINDOW: entries require onchain_volatility_snapshots coverage, which starts
    2026-03-16 - about 4 months of data. Any conclusion here is provisional;
    see the "what cannot be concluded" note printed at the end.
"""

import logging
import math
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy import stats

from dotenv import load_dotenv

load_dotenv()

from coding.core.database.repository import DatabaseRepository
from coding.core.logging.logging_setup import init_logging

init_logging(level="WARNING")
logger = logging.getLogger(__name__)
warnings.filterwarnings("ignore")

# -- Configuration ------------------------------------------------------------

CURRENCIES = ["BTC", "ETH"]

VOL_DATA_START = pd.Timestamp("2026-03-16 00:00:00")  # onchain_volatility_snapshots floor

MIN_DTE = 5
MAX_DTE = 120

# Data-quality guard: hourly_snapshots occasionally has only ONE side (call or
# put) quoted for most strikes in a given hour (a collection gap, not a strike-
# grid issue - confirmed by direct inspection: e.g. BTC 27MAR26 at
# 2026-03-16 01:00 had 12 strikes listed but only strike 85000 (17% away from
# the 72417 spot) had both legs priced). Picking "the only paired strike"
# there would silently mislabel a far-OTM combo as an ATM straddle. Cap how
# far the nearest both-legs-priced strike may be from spot; hours where
# nothing qualifies within the cap are dropped (not substituted).
MAX_ATM_MONEYNESS = 0.10  # |K/F - 1| <= 10%

SETTLEMENT_HOUR_TOLERANCE = pd.Timedelta(hours=6)  # fallback hourly-spot search window

RV_MIN_WINDOW = 2
RV_MAX_WINDOW = 120
RV_FIXED_WINDOWS = (10, 30)  # RV_10d, RV_30d (rv_trend, vrp_30d)

FDR_Q = 0.10
MIN_OBS_DAY = 30  # minimum (expiry, entry_day) rows for a metric x currency test

OUTPUT_DIR = Path("scripts")
CSV_PATH = OUTPUT_DIR / "straddle_backtest_observations.csv"

METRICS = [
    "vrp_dte", "vrp_30d", "rv_iv_ratio",
    "dvol_percentile_365d", "iv_percentile_expiry",
    "min_pnl_score", "straddle_cost_pct", "rv_trend",
    "net_gex_normalized", "funding_rate_abs",
]

METRIC_DESCRIPTIONS = {
    "vrp_dte": "ATM IV - DTE-matched realized vol",
    "vrp_30d": "ATM IV - 30d realized vol",
    "rv_iv_ratio": "RV_dte / ATM IV",
    "dvol_percentile_365d": "DVOL percentile vs trailing 365d (reused iv_percentile_365d)",
    "iv_percentile_expiry": "Per-expiry ATM-IV percentile (reused column)",
    "min_pnl_score": "Min P&L at RV-implied range edges, normalized by cost",
    "straddle_cost_pct": "cost / F (spot-based moneyness of the straddle price)",
    "rv_trend": "RV_10d / RV_30d (vol momentum)",
    "net_gex_normalized": "total_net_gex / (total_call_oi + total_put_oi)",
    "funding_rate_abs": "abs(funding rate) nearest to entry hour",
}


# -- Realized vol lookup -------------------------------------------------------

class RealizedVolLookup:
    """
    Precomputes annualized realized vol for arbitrary trailing day-windows,
    from a currency's daily OHLCV close series, so per-entry RV lookups are
    O(1) merge_asof calls instead of recomputing log-return std per row.

    Methodology matches VRPCalculator.calculate_realized_volatility:
    log returns of daily closes, std * sqrt(365). Window is expressed as a
    fixed number of trailing daily rows (not a calendar-day cutoff) - a
    reasonable proxy since OHLCV is one row per day with negligible gaps.
    """

    def __init__(self, ohlcv: pd.DataFrame):
        ohlcv = ohlcv.sort_values("date").reset_index(drop=True)
        self.dates = ohlcv["date"].values
        closes = ohlcv["close"].astype(float).values
        log_ret = np.full(len(closes), np.nan)
        log_ret[1:] = np.log(closes[1:] / closes[:-1])
        self._log_ret = pd.Series(log_ret, index=ohlcv["date"])
        self._cache: Dict[int, pd.Series] = {}

    def _rolling_rv(self, window: int) -> pd.Series:
        if window not in self._cache:
            rv = self._log_ret.rolling(window).std() * math.sqrt(365.0)
            self._cache[window] = rv
        return self._cache[window]

    def attach(self, entry_dates: pd.Series, windows: pd.Series) -> pd.Series:
        """
        For each (entry_date, window) pair, look up the RV as of the most
        recent OHLCV date <= entry_date, using the window-specific rolling
        series. Windows are clipped to [RV_MIN_WINDOW, RV_MAX_WINDOW].
        """
        windows_clipped = windows.clip(lower=RV_MIN_WINDOW, upper=RV_MAX_WINDOW).round().astype(int)
        result = pd.Series(np.nan, index=entry_dates.index)
        for w in windows_clipped.unique():
            mask = windows_clipped == w
            rv_series = self._rolling_rv(int(w))

            sub = pd.DataFrame({"date": entry_dates[mask].values}, index=entry_dates[mask].index)
            sub = sub.reset_index().rename(columns={"index": "_orig_idx"})
            sub = sub.sort_values("date")

            merged = pd.merge_asof(
                sub, rv_series.rename("rv").reset_index().rename(columns={"index": "date"}),
                on="date", direction="backward"
            )
            merged = merged.set_index("_orig_idx")
            result.loc[merged.index] = merged["rv"].values
        return result


# -- Data loading ---------------------------------------------------------------

def fetch_option_rows(repo: DatabaseRepository, currency: str) -> pd.DataFrame:
    """Full hourly_snapshots option history for a currency."""
    sql = """
        SELECT snapshot_hour, expiration, strike, option_type,
               vwap, mark_price, index_price
        FROM hourly_snapshots
        WHERE currency = %s AND option_type IN ('C', 'P')
    """
    with repo._db_cursor() as cur:
        cur.execute(sql, (currency,))
        cols = [d.name for d in cur.description]
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=cols)
    if df.empty:
        return df
    for c in ("strike", "vwap", "mark_price", "index_price"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    df["snapshot_hour"] = pd.to_datetime(df["snapshot_hour"])
    return df


def fetch_volatility_rows(repo: DatabaseRepository, currency: str) -> pd.DataFrame:
    sql = """
        SELECT snapshot_hour, expiration, atm_iv,
               iv_percentile_expiry, iv_percentile_365d, realized_vol
        FROM onchain_volatility_snapshots
        WHERE currency = %s
    """
    with repo._db_cursor() as cur:
        cur.execute(sql, (currency,))
        cols = [d.name for d in cur.description]
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=cols)
    if df.empty:
        return df
    for c in ("atm_iv", "iv_percentile_expiry", "iv_percentile_365d", "realized_vol"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    df["snapshot_hour"] = pd.to_datetime(df["snapshot_hour"])
    return df


def fetch_analysis_rows(repo: DatabaseRepository, currency: str) -> pd.DataFrame:
    sql = """
        SELECT snapshot_hour, expiration, total_net_gex, total_call_oi, total_put_oi
        FROM onchain_analysis_snapshots
        WHERE currency = %s
    """
    with repo._db_cursor() as cur:
        cur.execute(sql, (currency,))
        cols = [d.name for d in cur.description]
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=cols)
    if df.empty:
        return df
    for c in ("total_net_gex", "total_call_oi", "total_put_oi"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    df["snapshot_hour"] = pd.to_datetime(df["snapshot_hour"])
    return df


def fetch_ohlcv(repo: DatabaseRepository, currency: str) -> pd.DataFrame:
    sql = "SELECT date, close FROM ohlcv_history WHERE currency = %s ORDER BY date"
    with repo._db_cursor() as cur:
        cur.execute(sql, (currency,))
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=["date", "close"])
    df["date"] = pd.to_datetime(df["date"])
    df["close"] = pd.to_numeric(df["close"], errors="coerce").astype("float64")
    return df


def fetch_funding(repo: DatabaseRepository, currency: str) -> pd.DataFrame:
    sql = "SELECT date, funding_rate FROM funding_rate_history WHERE currency = %s ORDER BY date"
    with repo._db_cursor() as cur:
        cur.execute(sql, (currency,))
        rows = cur.fetchall()
    df = pd.DataFrame(rows, columns=["date", "funding_rate"])
    df["date"] = pd.to_datetime(df["date"])
    df["funding_rate"] = pd.to_numeric(df["funding_rate"], errors="coerce").astype("float64")
    return df


# -- Universe / entry construction ---------------------------------------------

def build_atm_entries(df_opt: pd.DataFrame) -> Tuple[pd.DataFrame, pd.Series]:
    """
    From raw option rows, build:
      - underlying_hourly: Series indexed by snapshot_hour, the median
        index_price across ALL instruments at that hour (spot proxy).
      - atm: one row per (snapshot_hour, expiration) with the ATM strike
        (both C and P present, usable price), coin cost, and USD cost.
    """
    df = df_opt.copy()
    price_used = df["vwap"].where(df["vwap"].notna() & (df["vwap"] > 0))
    price_used = price_used.fillna(df["mark_price"].where(df["mark_price"].notna() & (df["mark_price"] > 0)))
    df["price_used"] = price_used
    df = df.dropna(subset=["price_used", "index_price"])
    df = df[df["index_price"] > 0]

    underlying_hourly = df.groupby("snapshot_hour")["index_price"].median()

    paired = df.pivot_table(
        index=["snapshot_hour", "expiration", "strike"],
        columns="option_type", values="price_used", aggfunc="first"
    ).reset_index()
    if "C" not in paired.columns or "P" not in paired.columns:
        return pd.DataFrame(), underlying_hourly
    paired = paired.dropna(subset=["C", "P"])
    if paired.empty:
        return pd.DataFrame(), underlying_hourly

    paired = paired.merge(underlying_hourly.rename("spot"), left_on="snapshot_hour", right_index=True)
    paired["abs_diff"] = (paired["strike"] - paired["spot"]).abs()
    paired["moneyness"] = paired["abs_diff"] / paired["spot"]
    n_before_moneyness = paired.groupby(["snapshot_hour", "expiration"]).ngroups
    paired = paired[paired["moneyness"] <= MAX_ATM_MONEYNESS]
    n_after_moneyness = paired.groupby(["snapshot_hour", "expiration"]).ngroups if not paired.empty else 0
    dropped_by_moneyness_cap = n_before_moneyness - n_after_moneyness
    if paired.empty:
        return pd.DataFrame(), underlying_hourly
    paired = paired.sort_values(["snapshot_hour", "expiration", "abs_diff"])
    atm = paired.groupby(["snapshot_hour", "expiration"], as_index=False).first()
    atm.attrs["dropped_by_moneyness_cap"] = dropped_by_moneyness_cap

    atm["cost_coin"] = atm["C"] + atm["P"]
    atm["F"] = atm["spot"]
    atm["cost_usd"] = atm["cost_coin"] * atm["F"]
    atm = atm.rename(columns={"strike": "K"})
    return atm[["snapshot_hour", "expiration", "K", "C", "P", "F", "cost_coin", "cost_usd"]], underlying_hourly


def parse_expiry_dates(expirations: List[str]) -> Dict[str, pd.Timestamp]:
    """Deribit expiration strings ('14JUL26') -> settlement datetime (08:00 UTC)."""
    unique = pd.Series(sorted(set(e for e in expirations if e)))
    parsed = pd.to_datetime(unique, format="%d%b%y", errors="coerce")
    settle = parsed + pd.Timedelta(hours=8)
    return dict(zip(unique, settle))


def resolve_settlements(
    expirations: List[str],
    expiry_settle_map: Dict[str, pd.Timestamp],
    ohlcv: pd.DataFrame,
    underlying_hourly: pd.Series,
    max_hour: pd.Timestamp,
) -> Tuple[Dict[str, float], Dict[str, str], List[str]]:
    """
    Resolve settlement price per expiration.
    Returns (settlement_price_map, source_map, excluded_expirations).
    """
    ohlcv_by_date = ohlcv.set_index(ohlcv["date"].dt.normalize())["close"]
    hourly_idx = underlying_hourly.index.values

    settlement: Dict[str, float] = {}
    source: Dict[str, str] = {}
    excluded: List[str] = []

    for exp in sorted(set(expirations)):
        settle_dt = expiry_settle_map.get(exp)
        if settle_dt is None or pd.isna(settle_dt):
            excluded.append(f"{exp}: unparseable expiration string")
            continue
        if settle_dt > max_hour:
            excluded.append(f"{exp}: settles after data coverage ends ({settle_dt.date()})")
            continue

        exp_date_norm = settle_dt.normalize()
        if exp_date_norm in ohlcv_by_date.index:
            settlement[exp] = float(ohlcv_by_date.loc[exp_date_norm])
            source[exp] = "ohlcv_daily_close_0800utc"
            continue

        if len(hourly_idx) > 0:
            diffs = np.abs(hourly_idx - np.datetime64(settle_dt))
            nearest_pos = int(np.argmin(diffs))
            nearest_diff = pd.Timedelta(diffs[nearest_pos])
            if nearest_diff <= SETTLEMENT_HOUR_TOLERANCE:
                settlement[exp] = float(underlying_hourly.iloc[nearest_pos])
                source[exp] = f"hourly_spot_fallback(+/-{nearest_diff})"
                continue

        excluded.append(f"{exp}: no settlement price within tolerance (ohlcv missing, "
                         f"nearest hourly spot too far)")

    return settlement, source, excluded


# -- Metric computation ----------------------------------------------------------

def compute_metrics(
    atm: pd.DataFrame,
    vol_df: pd.DataFrame,
    analysis_df: pd.DataFrame,
    funding_df: pd.DataFrame,
    rv_lookup: RealizedVolLookup,
) -> pd.DataFrame:
    df = atm.copy()

    df = df.merge(vol_df, on=["snapshot_hour", "expiration"], how="left")
    df = df.merge(analysis_df, on=["snapshot_hour", "expiration"], how="left")

    df["entry_date"] = df["snapshot_hour"].dt.normalize()

    df["rv_dte"] = rv_lookup.attach(df["entry_date"], df["dte"])
    df["rv_10d"] = rv_lookup.attach(df["entry_date"], pd.Series(10.0, index=df.index))
    df["rv_30d_own"] = rv_lookup.attach(df["entry_date"], pd.Series(30.0, index=df.index))

    atm_iv_dec = df["atm_iv"] / 100.0
    df["vrp_dte"] = atm_iv_dec - df["rv_dte"]
    df["vrp_30d"] = atm_iv_dec - df["rv_30d_own"]
    df["rv_iv_ratio"] = df["rv_dte"] / atm_iv_dec.replace(0, np.nan)
    df["rv_trend"] = df["rv_10d"] / df["rv_30d_own"].replace(0, np.nan)

    df["dvol_percentile_365d"] = df["iv_percentile_365d"]
    # iv_percentile_expiry already present from merge

    df["net_gex_normalized"] = df["total_net_gex"] / (df["total_call_oi"] + df["total_put_oi"]).replace(0, np.nan)

    T = df["dte"] / 365.0
    edge_high = df["F"] * np.exp(df["rv_dte"] * np.sqrt(T))
    edge_low = df["F"] * np.exp(-df["rv_dte"] * np.sqrt(T))
    min_edge_value = np.minimum((edge_high - df["K"]).abs(), (edge_low - df["K"]).abs())
    df["min_pnl_score"] = (min_edge_value - df["cost_usd"]) / df["cost_usd"]

    df["straddle_cost_pct"] = df["cost_usd"] / df["F"]

    funding_sorted = funding_df.sort_values("date")
    df_sorted = df.sort_values("snapshot_hour")
    merged = pd.merge_asof(
        df_sorted, funding_sorted, left_on="snapshot_hour", right_on="date", direction="backward"
    )
    df = merged.drop(columns=["date"])
    df["funding_rate_abs"] = df["funding_rate"].abs()

    return df


# -- Statistics -------------------------------------------------------------------

def spearman(x: np.ndarray, y: np.ndarray) -> Tuple[float, float, int]:
    mask = np.isfinite(x) & np.isfinite(y)
    x, y = x[mask], y[mask]
    n = len(x)
    if n < 5 or np.std(x) == 0 or np.std(y) == 0:
        return np.nan, np.nan, n
    rho, p = stats.spearmanr(x, y)
    return float(rho), float(p), n


def bh_fdr_correction(p_values: List[float], q: float = 0.10) -> List[bool]:
    """Benjamini-Hochberg FDR correction. Returns bool list: True = survives."""
    n = len(p_values)
    if n == 0:
        return []
    indexed = sorted(enumerate(p_values), key=lambda t: t[1])
    threshold_rank = -1
    for rank, (_, p) in enumerate(indexed, start=1):
        if p <= (rank / n) * q:
            threshold_rank = rank
    rejected = [False] * n
    if threshold_rank >= 0:
        for rank, (idx, _) in enumerate(indexed, start=1):
            if rank <= threshold_rank:
                rejected[idx] = True
    return rejected


def quintile_table(df: pd.DataFrame, metric: str, ret_col: str = "straddle_return") -> Optional[pd.DataFrame]:
    sub = df[[metric, ret_col]].dropna()
    if len(sub) < 15:
        return None
    try:
        sub["quintile"] = pd.qcut(sub[metric], 5, labels=False, duplicates="drop")
    except ValueError:
        return None
    table = sub.groupby("quintile").agg(
        n=(ret_col, "size"),
        mean_return=(ret_col, "mean"),
        median_return=(ret_col, "median"),
        metric_mean=(metric, "mean"),
    ).reset_index()
    return table


def aggregate(df: pd.DataFrame, keys: List[str]) -> pd.DataFrame:
    agg_cols = METRICS + ["straddle_return"]
    return df.groupby(keys, as_index=False)[agg_cols].mean()


# -- Main --------------------------------------------------------------------------

def main() -> None:
    repo = DatabaseRepository()

    print("=" * 78)
    print("STRADDLE DEAL-QUALITY BACKTEST")
    print("=" * 78)

    per_currency_hourly: Dict[str, pd.DataFrame] = {}
    caveats: Dict[str, List[str]] = {}

    for currency in CURRENCIES:
        print(f"\n{'-'*78}\n  {currency} - loading and building entries\n{'-'*78}")

        df_opt = fetch_option_rows(repo, currency)
        vol_df = fetch_volatility_rows(repo, currency)
        analysis_df = fetch_analysis_rows(repo, currency)
        ohlcv = fetch_ohlcv(repo, currency)
        funding_df = fetch_funding(repo, currency)

        print(f"  hourly_snapshots option rows: {len(df_opt):,}")
        print(f"  onchain_volatility_snapshots rows: {len(vol_df):,}")
        print(f"  onchain_analysis_snapshots rows: {len(analysis_df):,}")
        print(f"  ohlcv_history daily rows: {len(ohlcv):,}  ({ohlcv['date'].min()} -> {ohlcv['date'].max()})")
        print(f"  funding_rate_history rows: {len(funding_df):,}")

        atm, underlying_hourly = build_atm_entries(df_opt)
        dropped_moneyness = atm.attrs.get("dropped_by_moneyness_cap", 0)
        print(f"  ATM (hour, expiration) pairs within {MAX_ATM_MONEYNESS:.0%} moneyness cap: {len(atm):,} "
              f"(dropped {dropped_moneyness:,} hour/expiry slices where the nearest both-legs-priced "
              f"strike was farther than {MAX_ATM_MONEYNESS:.0%} from spot)")

        max_hour = df_opt["snapshot_hour"].max()
        expiry_settle_map = parse_expiry_dates(atm["expiration"].tolist())

        atm["expiry_settle_dt"] = atm["expiration"].map(expiry_settle_map)
        atm["dte"] = (atm["expiry_settle_dt"] - atm["snapshot_hour"]).dt.total_seconds() / 86400.0

        n_before_dte = len(atm)
        atm = atm[(atm["dte"] >= MIN_DTE) & (atm["dte"] <= MAX_DTE)].copy()
        print(f"  Entries with DTE in [{MIN_DTE}, {MAX_DTE}]: {len(atm):,} (of {n_before_dte:,})")

        settlement_map, source_map, excluded = resolve_settlements(
            atm["expiration"].tolist(), expiry_settle_map, ohlcv, underlying_hourly, max_hour
        )
        n_expiries_total = atm["expiration"].nunique()
        n_expiries_settled = len(settlement_map)
        print(f"  Expiries in DTE-filtered universe: {n_expiries_total}  |  "
              f"settlement resolved: {n_expiries_settled}  |  excluded: {len(excluded)}")
        if excluded:
            for reason in excluded[:15]:
                print(f"    excluded: {reason}")
            if len(excluded) > 15:
                print(f"    ... and {len(excluded) - 15} more")

        atm["settlement_price"] = atm["expiration"].map(settlement_map)
        atm["settlement_source"] = atm["expiration"].map(source_map)
        atm = atm.dropna(subset=["settlement_price"])

        atm["settlement_value"] = (atm["settlement_price"] - atm["K"]).abs()
        atm["straddle_return"] = (atm["settlement_value"] - atm["cost_usd"]) / atm["cost_usd"]

        rv_lookup = RealizedVolLookup(ohlcv)
        full = compute_metrics(atm, vol_df, analysis_df, funding_df, rv_lookup)
        full = full[full["snapshot_hour"] >= VOL_DATA_START]
        full["currency"] = currency

        print(f"  Final hourly observations (>= {VOL_DATA_START.date()}, settlement known): {len(full):,}")
        print(f"  Expiries contributing: {full['expiration'].nunique()}")

        per_currency_hourly[currency] = full
        caveats[currency] = excluded

    all_hourly = pd.concat(per_currency_hourly.values(), ignore_index=True)

    # -- Sanity checks ----------------------------------------------------------
    print(f"\n{'='*78}\nSANITY CHECKS\n{'='*78}")
    for currency, df in per_currency_hourly.items():
        r = df["straddle_return"].dropna()
        print(f"  {currency} straddle_return: n={len(r):,}  mean={r.mean():+.4f}  "
              f"median={r.median():+.4f}  std={r.std():.4f}  min={r.min():+.4f}  max={r.max():+.4f}")
    r_all = all_hourly["straddle_return"].dropna()
    print(f"  POOLED straddle_return: n={len(r_all):,}  mean={r_all.mean():+.4f}  "
          f"median={r_all.median():+.4f}  std={r_all.std():.4f}")
    if r_all.mean() > 0.15:
        print("  WARNING: pooled mean return is strongly positive - re-examine pricing/settlement logic "
              "for a systematic bug (buyers of options should not show a large structural edge here).")

    print("\n  Observations per expiry (hourly grain), all currencies:")
    counts = all_hourly.groupby(["currency", "expiration"]).size().sort_values(ascending=False)
    for (cur, exp), n in counts.head(15).items():
        print(f"    {cur} {exp}: {n}")
    print(f"    ... {len(counts)} expiries total")

    # -- Hand-verified example ----------------------------------------------------
    print(f"\n{'='*78}\nHAND-VERIFIED EXAMPLE (check the arithmetic)\n{'='*78}")
    biggest_expiry = counts.idxmax()
    cur_ex, exp_ex = biggest_expiry
    example_pool = per_currency_hourly[cur_ex]
    example_pool = example_pool[example_pool["expiration"] == exp_ex].dropna(subset=["straddle_return"])
    example_pool = example_pool.sort_values("snapshot_hour").reset_index(drop=True)
    example = example_pool.iloc[len(example_pool) // 2]
    print(f"  currency: {cur_ex}   expiration: {exp_ex}")
    print(f"  entry hour (snapshot_hour): {example['snapshot_hour']}")
    print(f"  days to expiry at entry: {example['dte']:.2f}")
    print(f"  ATM strike K: {example['K']:.2f}")
    print(f"  call price (coin): {example['C']:.6f}   put price (coin): {example['P']:.6f}")
    print(f"  underlying/index at entry (F): {example['F']:.2f}")
    print(f"  cost_coin = C + P = {example['cost_coin']:.6f}")
    print(f"  cost_usd  = cost_coin * F = {example['cost_coin']:.6f} * {example['F']:.2f} "
          f"= {example['cost_usd']:.2f}")
    print(f"  settlement price: {example['settlement_price']:.2f}  (source: {example['settlement_source']})")
    print(f"  settlement_value = |S_settle - K| = |{example['settlement_price']:.2f} - {example['K']:.2f}| "
          f"= {example['settlement_value']:.2f}")
    recomputed_return = (example["settlement_value"] - example["cost_usd"]) / example["cost_usd"]
    print(f"  straddle_return = (settlement_value - cost_usd) / cost_usd "
          f"= ({example['settlement_value']:.2f} - {example['cost_usd']:.2f}) / {example['cost_usd']:.2f} "
          f"= {recomputed_return:+.4f}")
    print(f"  (stored value: {example['straddle_return']:+.4f} - "
          f"{'MATCH' if abs(recomputed_return - example['straddle_return']) < 1e-9 else 'MISMATCH!'})")

    # -- Aggregation grains -------------------------------------------------------
    day_grain = aggregate(all_hourly, ["currency", "expiration", "entry_date"])
    expiry_grain_by_currency = aggregate(day_grain, ["currency", "expiration"])
    expiry_grain_pooled = expiry_grain_by_currency.copy()

    print(f"\n{'='*78}\nUNIVERSE SIZE SUMMARY\n{'='*78}")
    for currency in CURRENCIES:
        n_hourly = len(per_currency_hourly[currency])
        n_day = len(day_grain[day_grain["currency"] == currency])
        n_expiry = len(expiry_grain_by_currency[expiry_grain_by_currency["currency"] == currency])
        print(f"  {currency}: hourly={n_hourly:,}  (expiry, entry_day)={n_day:,}  expiries={n_expiry}")
    print(f"  POOLED: hourly={len(all_hourly):,}  (expiry, entry_day)={len(day_grain):,}  "
          f"expiries={len(expiry_grain_pooled)}")

    # -- Correlation tests ---------------------------------------------------------
    results = []
    for metric in METRICS:
        row = {"metric": metric}
        for currency in CURRENCIES:
            sub = day_grain[day_grain["currency"] == currency]
            rho, p, n = spearman(sub[metric].values, sub["straddle_return"].values)
            row[f"rho_{currency}_day"] = rho
            row[f"p_{currency}_day"] = p
            row[f"n_{currency}_day"] = n

            sub_e = expiry_grain_by_currency[expiry_grain_by_currency["currency"] == currency]
            rho_e, p_e, n_e = spearman(sub_e[metric].values, sub_e["straddle_return"].values)
            row[f"rho_{currency}_expiry"] = rho_e
            row[f"n_{currency}_expiry"] = n_e

        rho_p, p_p, n_p = spearman(day_grain[metric].values, day_grain["straddle_return"].values)
        row["rho_pooled_day"] = rho_p
        row["p_pooled_day"] = p_p
        row["n_pooled_day"] = n_p

        rho_pe, p_pe, n_pe = spearman(expiry_grain_pooled[metric].values, expiry_grain_pooled["straddle_return"].values)
        row["rho_pooled_expiry"] = rho_pe
        row["n_pooled_expiry"] = n_pe

        results.append(row)

    results_df = pd.DataFrame(results)

    # FDR across metric x currency tests (day grain, per CLAUDE-provided spec)
    test_rows = []
    for _, row in results_df.iterrows():
        for currency in CURRENCIES:
            p = row[f"p_{currency}_day"]
            n = row[f"n_{currency}_day"]
            if np.isfinite(p) and n >= MIN_OBS_DAY:
                test_rows.append((row["metric"], currency, p))
    p_list = [t[2] for t in test_rows]
    fdr_flags = bh_fdr_correction(p_list, q=FDR_Q)
    fdr_survives = {(m, c): flag for (m, c, _), flag in zip(test_rows, fdr_flags)}

    for currency in CURRENCIES:
        results_df[f"fdr_{currency}"] = results_df["metric"].apply(
            lambda m: fdr_survives.get((m, currency), False)
        )
    results_df["fdr_any"] = results_df[[f"fdr_{c}" for c in CURRENCIES]].any(axis=1)

    results_df = results_df.sort_values(
        by="rho_pooled_day", key=lambda s: s.abs(), ascending=False
    ).reset_index(drop=True)

    # -- Ranked report table -----------------------------------------------------
    print(f"\n{'='*78}\nRANKED RESULTS (by |pooled rho|, day grain primary)\n{'='*78}")
    print(f"  FDR: Benjamini-Hochberg q={FDR_Q} across {len(test_rows)} (metric x currency) day-grain tests "
          f"with n>={MIN_OBS_DAY}\n")
    header = (f"  {'metric':<24} {'rho_pool':>9} {'p_pool':>10} {'n_pool':>7} | "
              f"{'rho_BTC':>8} {'n_BTC':>6} {'FDR':>4} | {'rho_ETH':>8} {'n_ETH':>6} {'FDR':>4} | "
              f"{'rho_pool_exp':>12} {'n_exp':>6}")
    print(header)
    print("  " + "-" * (len(header) - 2))
    for _, row in results_df.iterrows():
        fdr_mark = "*" if row["fdr_any"] else ""
        print(
            f"  {row['metric']:<24} {row['rho_pooled_day']:>+9.3f} {row['p_pooled_day']:>10.2e} "
            f"{row['n_pooled_day']:>7} | "
            f"{row['rho_BTC_day']:>+8.3f} {row['n_BTC_day']:>6} {str(row['fdr_BTC']):>4} | "
            f"{row['rho_ETH_day']:>+8.3f} {row['n_ETH_day']:>6} {str(row['fdr_ETH']):>4} | "
            f"{row['rho_pooled_expiry']:>+12.3f} {row['n_pooled_expiry']:>6} {fdr_mark}"
        )
    print(f"\n  descriptions:")
    for m in METRICS:
        print(f"    {m:<24} {METRIC_DESCRIPTIONS[m]}")

    # -- Quintile tables for top 3 -------------------------------------------------
    print(f"\n{'='*78}\nQUINTILE ANALYSIS - top 3 by |pooled rho| (day grain, pooled)\n{'='*78}")
    for metric in results_df["metric"].head(3):
        print(f"\n  metric: {metric}")
        qt = quintile_table(day_grain, metric)
        if qt is None:
            print("    insufficient data for quintiles")
            continue
        print(f"    {'quintile':>8} {'n':>6} {'metric_mean':>12} {'mean_return':>12} {'median_return':>13}")
        for _, r in qt.iterrows():
            print(f"    {int(r['quintile']):>8} {int(r['n']):>6} {r['metric_mean']:>12.4f} "
                  f"{r['mean_return']:>+12.4f} {r['median_return']:>+13.4f}")

    # -- Output CSV ---------------------------------------------------------------
    out_cols = [
        "currency", "expiration", "snapshot_hour", "entry_date", "dte", "K", "C", "P", "F",
        "cost_coin", "cost_usd", "settlement_price", "settlement_source", "settlement_value",
        "straddle_return",
    ] + METRICS
    all_hourly[out_cols].to_csv(CSV_PATH, index=False)
    print(f"\nRaw per-observation dataset written to: {CSV_PATH}  ({len(all_hourly):,} rows)")

    # -- Caveats --------------------------------------------------------------------
    print(f"\n{'='*78}\nCAVEATS\n{'='*78}")
    print("  - Entry cost uses vwap (preferred) or mark_price (fallback) in coin terms x index "
          "price. Historical bid/ask columns are NOT treated as executable quotes - real fills "
          "would have paid the ask, so realized returns here are optimistic vs. a live trader.")
    print("  - No stored forward curve (futures_price is NULL for all hourly_snapshots option rows "
          "in this DB) - F is the spot/index price, not a true forward.")
    print("  - Settlement price is exact (ohlcv close stamped 08:00 UTC = Deribit settlement instant) "
          "when that daily row exists; a small number of expiries fall back to the nearest hourly "
          "index price within +/-6h, or are excluded entirely - see per-currency exclusion lists above.")
    print("  - Data window is ~4 months (2026-03-16 to data end) - one BTC/ETH volatility regime. "
          "FDR survival here is not proof of a durable edge; it rules out the metric being pure "
          "noise IN THIS WINDOW, nothing more.")
    print("  - dvol_percentile_365d reuses onchain_volatility_snapshots.iv_percentile_365d, which "
          "the existing pipeline already computes from dvol_history (see "
          "VolatilityReconstructionService._reconstruct_market_metrics) - not recomputed here.")
    for currency in CURRENCIES:
        if caveats[currency]:
            print(f"  - {currency}: {len(caveats[currency])} expiries excluded (settlement unresolved "
                  f"or unparseable) - see per-currency detail printed above.")

    print()


if __name__ == "__main__":
    main()
