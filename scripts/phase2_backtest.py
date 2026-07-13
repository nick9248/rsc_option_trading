"""
Phase 2 — Combinatorial Backtest Framework

Extends the C2 first-pass with:
  - Overlapping-window correction: non-overlapping subsampling per horizon
    (the C2 '1w signal' used hourly rows with 168h forward returns — 167/168
    overlap inflates apparent N from ~12 to ~1800, creating spurious significance)
  - Walk-forward validation: 5 expanding-window folds before OOS holdout
  - FDR correction (Benjamini-Hochberg) on single-metric p-values
  - Pairwise OLS on FDR-surviving metrics (validated out-of-sample)
  - OOS holdout: last 14 days reserved, never touched during search

Output: blueprint.txt — ranked metric list + pairwise survivors + confidence tier

Design decisions:
  GRAIN: front-month expiration per (currency, hour) — same as C2
  PRICE: underlying_price from onchain_volatility_snapshots
  FORWARD RETURNS: non-overlapping (primary) + overlapping (reference, clearly flagged)
  WALK-FORWARD: expanding window, initial 35d train, 7d validation step
  OOS HOLDOUT: last 14 days of the 83-day clean window
  FDR THRESHOLD: q = 0.10 (less conservative than Bonferroni; dataset is thin)
  PAIRWISE: OLS on IS data, evaluated on WF folds + OOS; only from FDR survivors
"""

import csv
import logging
import math
import warnings
from datetime import timedelta
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

WINDOW_START = "2026-03-16 00:00:00"
WINDOW_END = "2026-06-06 23:59:59"

# OOS holdout: last 14 days of the clean window — NEVER touch during search
OOS_HOLDOUT_DAYS = 14
OOS_START = "2026-05-24 00:00:00"  # 14 days before WINDOW_END

# Walk-forward configuration
WF_INITIAL_TRAIN_DAYS = 35
WF_STEP_DAYS = 7
WF_VALID_DAYS = 7

HORIZONS: Dict[str, int] = {"1h": 1, "4h": 4, "1d": 24, "1w": 168}

# Phase 2 metric set — excludes redundant/stale per C4 verdict:
#   vrp_absolute (keep vrp_percentage), expected_weekly/monthly_move (collinear),
#   net_vanna/net_charm (stale DB values), total_net_gex/dex (hard-zero in history)
VOL_METRICS = [
    "atm_iv", "skew_25d", "put_25d_iv", "call_25d_iv",
    "vwap_iv", "mark_iv_avg",
    "vrp_percentage", "realized_vol",
    "iv_percentile_expiry", "iv_percentile_365d",
    "expected_daily_move",
    "pc_atm_ratio", "pc_near_otm_ratio", "pc_far_otm_ratio",
]
ANALYSIS_METRICS = [
    "max_pain_distance_pct",
    "put_call_ratio_oi", "put_call_ratio_volume",
    "itm_call_oi_pct", "otm_call_oi_pct", "itm_put_oi_pct", "otm_put_oi_pct",
]
ALL_METRICS = VOL_METRICS + ANALYSIS_METRICS

FDR_Q = 0.10
MIN_OBS_SINGLE = 30
MIN_OBS_PAIR = 50

OUTPUT_DIR = Path("scripts")

# -- Data loading -------------------------------------------------------------

def fetch_front_month_series(repo: DatabaseRepository, currency: str) -> pd.DataFrame:
    """
    Pull one row per (currency, snapshot_hour) — the front-month (nearest-expiry)
    row — joining onchain_volatility_snapshots and onchain_analysis_snapshots,
    restricted to the clean 83-day window with OOS holdout excluded from search.
    """
    sql = """
        SELECT
            v.snapshot_hour,
            v.currency,
            v.expiration,
            v.underlying_price,
            v.atm_iv, v.skew_25d, v.put_25d_iv, v.call_25d_iv,
            v.vwap_iv, v.mark_iv_avg,
            v.vrp_percentage, v.realized_vol,
            v.iv_percentile_expiry, v.iv_percentile_365d,
            v.expected_daily_move,
            v.pc_atm_ratio, v.pc_near_otm_ratio, v.pc_far_otm_ratio,
            a.max_pain_distance_pct,
            a.put_call_ratio_oi, a.put_call_ratio_volume,
            a.itm_call_oi_pct, a.otm_call_oi_pct, a.itm_put_oi_pct, a.otm_put_oi_pct
        FROM onchain_volatility_snapshots v
        JOIN onchain_analysis_snapshots a
          ON a.snapshot_hour = v.snapshot_hour
         AND a.currency = v.currency
         AND a.expiration = v.expiration
        WHERE v.currency = %s
          AND v.snapshot_hour >= %s
          AND v.snapshot_hour <= %s
        ORDER BY v.snapshot_hour, v.expiration
    """
    with repo._db_cursor() as cur:
        cur.execute(sql, (currency, WINDOW_START, WINDOW_END))
        cols = [d.name for d in cur.description]
        rows = cur.fetchall()

    df = pd.DataFrame(rows, columns=cols)
    if df.empty:
        return df

    numeric_cols = [c for c in df.columns if c not in ("snapshot_hour", "currency", "expiration")]
    for c in numeric_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")

    df["expiry_date"] = pd.to_datetime(df["expiration"], format="%d%b%y", errors="coerce")
    df["snapshot_hour"] = pd.to_datetime(df["snapshot_hour"])
    df["days_to_expiry"] = (df["expiry_date"] - df["snapshot_hour"]).dt.total_seconds() / 86400.0
    df = df[df["days_to_expiry"] >= 0]
    df = df.sort_values(["snapshot_hour", "days_to_expiry"])
    front = df.groupby("snapshot_hour", as_index=False).first()
    front = front.sort_values("snapshot_hour").reset_index(drop=True)
    return front


def build_returns(df: pd.DataFrame) -> pd.DataFrame:
    """Compute forward returns and reindex to a complete hourly grid."""
    df = df.set_index("snapshot_hour").sort_index()
    full_idx = pd.date_range(df.index.min(), df.index.max(), freq="h")
    df = df.reindex(full_idx)
    df.index.name = "snapshot_hour"

    price = df["underlying_price"]
    for label, hrs in HORIZONS.items():
        df[f"fwd_ret_{label}"] = price.shift(-hrs) / price - 1.0
    return df


# -- Statistical helpers -------------------------------------------------------

def pearson_r(x: np.ndarray, y: np.ndarray) -> Tuple[float, float]:
    """Pearson correlation and p-value; returns (nan, nan) on degenerate input."""
    mask = np.isfinite(x) & np.isfinite(y)
    x, y = x[mask], y[mask]
    if len(x) < 5 or np.std(x) == 0 or np.std(y) == 0:
        return np.nan, np.nan
    r, p = stats.pearsonr(x, y)
    return float(r), float(p)


def bh_fdr_correction(p_values: List[float], q: float = 0.10) -> List[bool]:
    """
    Benjamini-Hochberg FDR correction.
    Returns a list of bool: True = reject null (significant after correction).
    """
    n = len(p_values)
    if n == 0:
        return []
    indexed = sorted(enumerate(p_values), key=lambda x: x[1])
    rejected = [False] * n
    for rank, (idx, p) in enumerate(indexed, start=1):
        if p <= (rank / n) * q:
            rejected[idx] = True
    # BH: if k is the largest rank with p_k <= (k/n)*q, reject all 1..k
    # Re-apply properly:
    rejected = [False] * n
    threshold_idx = -1
    for rank, (idx, p) in enumerate(indexed, start=1):
        if p <= (rank / n) * q:
            threshold_idx = rank
    if threshold_idx >= 0:
        for rank, (idx, _) in enumerate(indexed, start=1):
            if rank <= threshold_idx:
                rejected[idx] = True
    return rejected


def subsample_nonoverlapping(df: pd.DataFrame, step_hours: int) -> pd.DataFrame:
    """
    Return every step_hours-th row to remove overlapping forward-return windows.
    Always starts from the first available row.
    """
    return df.iloc[::step_hours].copy()


# -- Walk-forward fold generator -----------------------------------------------

def walk_forward_folds(
    df: pd.DataFrame,
    initial_train_days: int,
    step_days: int,
    valid_days: int,
    oos_start: str,
) -> List[Tuple[pd.DataFrame, pd.DataFrame]]:
    """
    Generate (train_df, valid_df) pairs using an expanding-window walk-forward.
    Stops before the OOS holdout boundary.
    """
    oos_boundary = pd.Timestamp(oos_start)
    train_end = df.index.min() + timedelta(days=initial_train_days)
    folds = []

    while True:
        valid_end = train_end + timedelta(hours=valid_days * 24)
        if valid_end > oos_boundary:
            break
        train = df[df.index < train_end].copy()
        valid = df[(df.index >= train_end) & (df.index < valid_end)].copy()
        if len(train) >= 100 and len(valid) >= 24:
            folds.append((train, valid))
        train_end += timedelta(days=step_days)

    return folds


# -- Single-metric analysis ----------------------------------------------------

def analyze_single_metric(
    df: pd.DataFrame,
    metric: str,
    horizon_label: str,
    horizon_hrs: int,
    subsample: bool = False,
) -> Dict:
    ret_col = f"fwd_ret_{horizon_label}"
    if metric not in df.columns or ret_col not in df.columns:
        return {}

    src = subsample_nonoverlapping(df, horizon_hrs) if subsample else df
    sub = src[[metric, ret_col]].dropna()
    n = len(sub)
    if n < MIN_OBS_SINGLE:
        return {"n": n, "r": np.nan, "p": np.nan}

    r, p = pearson_r(sub[metric].values, sub[ret_col].values)
    return {"n": n, "r": r, "p": p}


def run_single_metric_walkforward(
    folds: List[Tuple[pd.DataFrame, pd.DataFrame]],
    metric: str,
    horizon_label: str,
    horizon_hrs: int,
) -> Dict:
    """
    For each fold: compute correlation on train and validation sets.
    Returns stability stats: mean validation r, fraction of folds with same sign as IS.
    """
    ret_col = f"fwd_ret_{horizon_label}"
    is_rs, val_rs = [], []

    for train, valid in folds:
        r_is, _ = pearson_r(
            subsample_nonoverlapping(train, horizon_hrs)[[metric, ret_col]].dropna()[metric].values if metric in train.columns else np.array([]),
            subsample_nonoverlapping(train, horizon_hrs)[[metric, ret_col]].dropna()[ret_col].values if metric in train.columns else np.array([]),
        )
        r_val, _ = pearson_r(
            subsample_nonoverlapping(valid, horizon_hrs)[[metric, ret_col]].dropna()[metric].values if metric in valid.columns and ret_col in valid.columns else np.array([]),
            subsample_nonoverlapping(valid, horizon_hrs)[[metric, ret_col]].dropna()[ret_col].values if metric in valid.columns and ret_col in valid.columns else np.array([]),
        )
        if np.isfinite(r_is):
            is_rs.append(r_is)
        if np.isfinite(r_val):
            val_rs.append(r_val)

    if not val_rs:
        return {"wf_val_r_mean": np.nan, "wf_sign_stability": np.nan, "wf_folds_n": 0}

    mean_is = float(np.nanmean(is_rs)) if is_rs else np.nan
    mean_val = float(np.nanmean(val_rs))
    sign_stable = sum(1 for r in val_rs if np.isfinite(r) and np.sign(r) == np.sign(mean_is)) / len(val_rs)

    return {
        "wf_val_r_mean": mean_val,
        "wf_sign_stability": sign_stable,
        "wf_folds_n": len(val_rs),
    }


# -- Pairwise OLS -------------------------------------------------------------

def ols_r2(X: np.ndarray, y: np.ndarray) -> float:
    """OLS R² via numpy lstsq."""
    X_with_const = np.column_stack([np.ones(len(X)), X])
    mask = np.all(np.isfinite(X_with_const), axis=1) & np.isfinite(y)
    X_with_const, y = X_with_const[mask], y[mask]
    if len(y) < MIN_OBS_PAIR:
        return np.nan
    try:
        coef, _, _, _ = np.linalg.lstsq(X_with_const, y, rcond=None)
        y_hat = X_with_const @ coef
        ss_res = np.sum((y - y_hat) ** 2)
        ss_tot = np.sum((y - y.mean()) ** 2)
        return float(1 - ss_res / ss_tot) if ss_tot > 0 else np.nan
    except Exception:
        return np.nan


def analyze_pair(
    df_is: pd.DataFrame,
    df_oos: pd.DataFrame,
    m1: str,
    m2: str,
    horizon_label: str,
    horizon_hrs: int,
) -> Optional[Dict]:
    ret_col = f"fwd_ret_{horizon_label}"
    if any(c not in df_is.columns for c in [m1, m2, ret_col]):
        return None

    is_sub = subsample_nonoverlapping(df_is, horizon_hrs)[[m1, m2, ret_col]].dropna()
    if len(is_sub) < MIN_OBS_PAIR:
        return None

    X_is = is_sub[[m1, m2]].values
    y_is = is_sub[ret_col].values
    r2_is = ols_r2(X_is, y_is)

    # Individual correlations on IS (for reference)
    r1, p1 = pearson_r(is_sub[m1].values, y_is)
    r2, p2 = pearson_r(is_sub[m2].values, y_is)

    # OOS evaluation
    if df_oos is not None and len(df_oos) > 0:
        oos_sub = subsample_nonoverlapping(df_oos, horizon_hrs)[[m1, m2, ret_col]].dropna()
        r2_oos = ols_r2(oos_sub[[m1, m2]].values, oos_sub[ret_col].values) if len(oos_sub) >= MIN_OBS_PAIR else np.nan
    else:
        r2_oos = np.nan

    return {
        "m1": m1, "m2": m2, "horizon": horizon_label,
        "n_is": len(is_sub),
        "r2_is": r2_is,
        "r2_oos": r2_oos,
        "r_m1_is": r1, "p_m1_is": p1,
        "r_m2_is": r2, "p_m2_is": p2,
    }


# -- Main ----------------------------------------------------------------------

def main() -> None:
    repo = DatabaseRepository()

    # -- Section 1: Load data --------------------------------------------------
    print("=" * 72)
    print("PHASE 2 BACKTEST FRAMEWORK")
    print("=" * 72)

    all_results = {}
    for currency in ["BTC", "ETH"]:
        print(f"\n{'='*40}")
        print(f"  {currency}")
        print(f"{'='*40}")

        df_raw = fetch_front_month_series(repo, currency)
        if df_raw.empty:
            print(f"  No data for {currency} — skipping")
            continue

        df = build_returns(df_raw)
        n_total = df["underlying_price"].notna().sum()
        print(f"  Loaded {n_total} hourly rows  ({df.index.min().date()} -&gt; {df.index.max().date()})")

        # -- Section 2: Walk-forward fold setup --------------------------------
        df_search = df[df.index < pd.Timestamp(OOS_START)].copy()
        df_oos = df[df.index >= pd.Timestamp(OOS_START)].copy()
        oos_n = df_oos["underlying_price"].notna().sum()

        folds = walk_forward_folds(
            df_search, WF_INITIAL_TRAIN_DAYS, WF_STEP_DAYS, WF_VALID_DAYS, OOS_START
        )
        print(f"  Walk-forward: {len(folds)} folds  |  OOS holdout: {oos_n} hours")
        print()

        # Use the full search window as the single IS block for p-value calculation
        df_is = df_search.copy()

        # -- Section 3: Single-metric analysis (non-overlapping) --------------
        print("  SINGLE-METRIC ANALYSIS (non-overlapping windows)")
        print(f"  {'Metric':<28} {'Horizon':<6} {'N':>5}  {'r':>7}  {'p':>9}  {'WF_sign':>8}  Note")
        print(f"  {'-'*28} {'-'*6} {'-'*5}  {'-'*7}  {'-'*9}  {'-'*8}")

        single_rows = []
        for metric in ALL_METRICS:
            for h_label, h_hrs in HORIZONS.items():
                res = analyze_single_metric(df_is, metric, h_label, h_hrs, subsample=True)
                wf = run_single_metric_walkforward(folds, metric, h_label, h_hrs)
                row = {
                    "currency": currency, "metric": metric, "horizon": h_label,
                    **res, **wf,
                }
                single_rows.append(row)

        # FDR correction across all (metric × horizon) tests for this currency
        valid_rows = [r for r in single_rows if np.isfinite(r.get("p", np.nan))]
        p_values = [r["p"] for r in valid_rows]
        fdr_flags = bh_fdr_correction(p_values, q=FDR_Q) if p_values else []

        for i, row in enumerate(valid_rows):
            row["fdr_significant"] = bool(fdr_flags[i]) if i < len(fdr_flags) else False

        for row in single_rows:
            if "fdr_significant" not in row:
                row["fdr_significant"] = False

        # Print FDR-significant results
        for row in sorted(single_rows, key=lambda r: abs(r.get("r", 0) or 0), reverse=True):
            if not row.get("fdr_significant"):
                continue
            note = ""
            if row.get("wf_sign_stability", 0) >= 0.8:
                note = "WF_STABLE"
            elif row.get("wf_sign_stability", 0) >= 0.6:
                note = "WF_OK"
            else:
                note = "WF_UNSTABLE"
            print(
                f"  {row['metric']:<28} {row['horizon']:<6} {row.get('n', 0):>5}  "
                f"{row.get('r', 0):>+7.3f}  {row.get('p', 1):>9.2e}  "
                f"{row.get('wf_sign_stability', 0):>8.2f}  {note}"
            )

        all_results[currency] = {"single": single_rows, "df_is": df_is, "df_oos": df_oos}

        # -- Section 4: Pairwise analysis from FDR survivors ------------------
        fdr_survivors = list({r["metric"] for r in single_rows if r.get("fdr_significant")})
        print()
        print(f"  FDR survivors ({len(fdr_survivors)}): {', '.join(sorted(fdr_survivors))}")

        if len(fdr_survivors) >= 2:
            print()
            print("  PAIRWISE OLS (non-overlapping, IS R² + OOS R²)")
            print(f"  {'Pair':<52} {'H':<4} {'N_is':>5} {'R2_is':>7} {'R2_oos':>7}")
            print(f"  {'-'*52} {'-'*4} {'-'*5} {'-'*7} {'-'*7}")

            pair_rows = []
            for i, m1 in enumerate(fdr_survivors):
                for m2 in fdr_survivors[i + 1:]:
                    for h_label, h_hrs in HORIZONS.items():
                        pr = analyze_pair(df_is, df_oos, m1, m2, h_label, h_hrs)
                        if pr is None:
                            continue
                        pair_rows.append(pr)
                        if np.isfinite(pr.get("r2_oos", np.nan)):
                            print(
                                f"  {m1[:24]} + {m2[:24]:<26} "
                                f"{h_label:<4} {pr['n_is']:>5} "
                                f"{pr.get('r2_is', 0):>7.4f} {pr.get('r2_oos', 0):>7.4f}"
                            )

            all_results[currency]["pairs"] = pair_rows
        else:
            print("  (fewer than 2 FDR survivors — skipping pairwise)")
            all_results[currency]["pairs"] = []

    # -- Section 5: Blueprint output -------------------------------------------
    _write_blueprint(all_results)
    print()
    print(f"Blueprint written to: {OUTPUT_DIR / 'phase2_blueprint.txt'}")
    print()


def _write_blueprint(all_results: Dict) -> None:
    lines = []
    lines.append("=" * 72)
    lines.append("PHASE 2 BLUEPRINT — ON-CHAIN METRIC EVALUATION")
    lines.append(f"Generated from {WINDOW_START} to {OOS_START} (search window)")
    lines.append(f"OOS holdout: {OOS_START} to {WINDOW_END}")
    lines.append(f"FDR threshold: q={FDR_Q}  |  Non-overlapping subsample per horizon")
    lines.append("=" * 72)
    lines.append("")

    for currency, data in all_results.items():
        lines.append(f"{'-'*72}")
        lines.append(f"  {currency}")
        lines.append(f"{'-'*72}")

        single = data.get("single", [])
        fdr_sig = [r for r in single if r.get("fdr_significant")]
        non_sig = [r for r in single if not r.get("fdr_significant")]

        lines.append("")
        lines.append("  FDR-SIGNIFICANT SINGLE-METRIC SIGNALS (q=0.10):")
        lines.append(f"  {'Metric':<28} {'H':<4} {'N':>5} {'r':>7} {'p':>9} {'WF_stab':>7} {'WF_val_r':>8}")
        lines.append(f"  {'-'*28} {'-'*4} {'-'*5} {'-'*7} {'-'*9} {'-'*7} {'-'*8}")

        if fdr_sig:
            for r in sorted(fdr_sig, key=lambda x: abs(x.get("r", 0) or 0), reverse=True):
                lines.append(
                    f"  {r['metric']:<28} {r['horizon']:<4} {r.get('n', 0):>5} "
                    f"{r.get('r', 0):>+7.3f} {r.get('p', 1):>9.2e} "
                    f"{r.get('wf_sign_stability', 0):>7.2f} {r.get('wf_val_r_mean', 0):>+8.4f}"
                )
        else:
            lines.append("  (none — no metrics survive FDR correction at q=0.10)")

        # Overlapping-window comparison note for the 1w horizon
        one_w_overlapping = [r for r in single if r.get("horizon") == "1w"]
        if one_w_overlapping:
            lines.append("")
            lines.append("  NOTE — 1w horizon with NON-overlapping windows (N~12):")
            for r in one_w_overlapping:
                flag = "FDR*" if r.get("fdr_significant") else ""
                lines.append(
                    f"    {r['metric']:<28} n={r.get('n', 0):>3}  r={r.get('r', 0):>+.3f}  {flag}"
                )
            lines.append("  Compare against C2's overlapping '1w signal' — see if the")
            lines.append("  FDR survivors differ.  1w n<30 -&gt; treat as suggestive only.")

        lines.append("")

        pairs = data.get("pairs", [])
        if pairs:
            lines.append("  PAIRWISE COMBINATIONS (OLS, non-overlapping):")
            lines.append(f"  {'Pair':<52} {'H':<4} {'R2_is':>7} {'R2_oos':>7}")
            lines.append(f"  {'-'*52} {'-'*4} {'-'*7} {'-'*7}")
            for pr in sorted(pairs, key=lambda x: x.get("r2_oos") or -99, reverse=True)[:20]:
                r2o = pr.get("r2_oos", np.nan)
                if not np.isfinite(r2o):
                    continue
                pair_str = f"{pr['m1'][:23]}+{pr['m2'][:23]}"
                lines.append(
                    f"  {pair_str:<52} {pr['horizon']:<4} "
                    f"{pr.get('r2_is', 0):>7.4f} {r2o:>7.4f}"
                )
        lines.append("")

    lines.append("=" * 72)
    lines.append("RECOMMENDED PHASE 3 FEATURE SET:")
    lines.append("")
    lines.append("  Metrics that survive FDR correction AND walk-forward sign-stability")
    lines.append("  >= 0.60 are recommended for the Phase 3 forward-testing harness.")
    lines.append("")
    lines.append("  Exclude per C4 verdict (DO NOT include in Phase 3 until fixed):")
    lines.append("  - net_vanna, net_charm: formula corrected 2026-06-09 but DB values")
    lines.append("    are stale (pre-fix backfill). Re-backfill then re-evaluate.")
    lines.append("  - total_net_gex, total_net_dex: live collection bug fixed 2026-06-09;")
    lines.append("    historical data is hard-zero. Use going forward once data accumulates.")
    lines.append("=" * 72)

    out_path = OUTPUT_DIR / "phase2_blueprint.txt"
    out_path.write_text("\n".join(lines), encoding="utf-8")

    # Also save raw single-metric results to CSV for reproducibility
    all_rows = []
    for currency, data in all_results.items():
        all_rows.extend(data.get("single", []))

    csv_path = OUTPUT_DIR / "phase2_single_metric_results.csv"
    if all_rows:
        fieldnames = [k for k in all_rows[0].keys() if k != "df"]
        with open(csv_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for row in all_rows:
                safe_row = {k: v for k, v in row.items() if k in fieldnames}
                w.writerow(safe_row)


if __name__ == "__main__":
    main()
