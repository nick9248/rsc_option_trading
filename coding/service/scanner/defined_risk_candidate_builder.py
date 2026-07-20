"""
Shared candidate construction for the defined-risk (iron condor / long
butterfly) scanners. Validated 2026-07-20 via
scripts/backtest_iron_condor_butterfly.py against 15-16 settled BTC/ETH
expiries -- ported here as-is. Read
docs/superpowers/specs/2026-07-20-defined-risk-scanner-design.md before
changing any grid constant below; re-run the backtest script against any
change before trusting it.

CANDIDATE GRID: short strikes placed at a fixed % distance from the future
price F, wing width also a fixed % of F, each target snapped to the nearest
strike passing the liquidity gate. Ranked by RV-implied EV, not IV-implied
(IV-implied EV is ~always negative by construction; RV-implied EV is where
a real VRP-driven edge would show up -- mirrors StraddleScanService's own
min_pnl_score).

RETURN CONVENTION (validated bug fix -- do not regress): iron condor is a
credit structure, so its capital at risk is max_loss (the credit itself is
income, not capital deployed). Butterfly is a debit structure, so its
capital at risk is cost_or_credit (= the cost paid). Callers must divide
pnl by max_loss for iron condor and by cost_or_credit for butterfly, not
uniformly by cost_or_credit for both.
"""
import math
from typing import Dict, List, Optional

MAX_SPREAD_PCT = 0.15
MIN_OPEN_INTEREST = 25

SHORT_DISTANCES_PCT = [0.06, 0.08, 0.10, 0.12, 0.15]
WING_WIDTHS_PCT = [0.02, 0.03, 0.05]
BF_MID_BAND_PCT = 0.03
BF_WIDTHS_PCT = [0.02, 0.03, 0.05, 0.08]


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _nearest_strike(strikes: List[float], target: float) -> Optional[float]:
    if not strikes:
        return None
    return min(strikes, key=lambda k: abs(k - target))


def passes_liquidity(bid: Optional[float], ask: Optional[float], oi: Optional[float]) -> bool:
    """Both sides quoted, spread within MAX_SPREAD_PCT, OI >= MIN_OPEN_INTEREST."""
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return False
    mid = (bid + ask) / 2.0
    if mid <= 0 or (ask - bid) / mid > MAX_SPREAD_PCT:
        return False
    if (oi or 0) < MIN_OPEN_INTEREST:
        return False
    return True


def _prob_and_ev(be_lo, be_hi, F, sigma_sqrt_t, payoff_if_win, payoff_if_lose):
    """P(price ends between be_lo and be_hi) under lognormal(F), and the EV of a win/lose-only payoff."""
    if not sigma_sqrt_t:
        return None, None
    z_lo = (math.log(be_lo / F) + 0.5 * sigma_sqrt_t ** 2) / sigma_sqrt_t
    z_hi = (math.log(be_hi / F) + 0.5 * sigma_sqrt_t ** 2) / sigma_sqrt_t
    prob = (_normal_cdf(z_hi) - _normal_cdf(z_lo)) * 100.0
    ev = prob / 100.0 * payoff_if_win - (1 - prob / 100.0) * payoff_if_lose
    return prob, ev


def build_iron_condor_candidates(liquid: Dict[float, Dict], F: float, sigma_sqrt_t: Optional[float]) -> List[Dict]:
    """
    liquid: {strike: {call_bid, call_ask, call_ok, put_bid, put_ask, put_ok}}
    in USD terms. Returns candidates sorted by nothing in particular --
    callers rank by whatever metric they need (this repo ranks by ev).
    """
    liquid_call = sorted(k for k in liquid if liquid[k]["call_ok"])
    liquid_put = sorted(k for k in liquid if liquid[k]["put_ok"])
    rows: List[Dict] = []
    seen = set()
    for dist_pct in SHORT_DISTANCES_PCT:
        k1 = _nearest_strike(liquid_call, F * (1 + dist_pct))
        k3 = _nearest_strike(liquid_put, F * (1 - dist_pct))
        if k1 is None or k3 is None or k1 <= F or k3 >= F:
            continue
        for wing_pct in WING_WIDTHS_PCT:
            k2 = _nearest_strike([k for k in liquid_call if k > k1], k1 + F * wing_pct)
            k4 = _nearest_strike([k for k in liquid_put if k < k3], k3 - F * wing_pct)
            if k2 is None or k4 is None or k2 <= k1 or k4 >= k3:
                continue
            key = (k1, k2, k3, k4)
            if key in seen:
                continue
            seen.add(key)

            credit = (liquid[k1]["call_bid"] - liquid[k2]["call_ask"]) + (liquid[k3]["put_bid"] - liquid[k4]["put_ask"])
            if credit is None or credit <= 0:
                continue
            wing_width = max(k2 - k1, k3 - k4)
            max_loss = wing_width - credit
            if max_loss <= 0:
                continue
            be_up = k1 + credit
            be_down = k3 - credit
            prob, ev = _prob_and_ev(be_down, be_up, F, sigma_sqrt_t, credit, max_loss)

            rows.append({
                "structure_type": "iron_condor",
                "short_call": k1, "long_call": k2, "short_put": k3, "long_put": k4,
                "cost_or_credit": credit, "max_loss": max_loss, "max_profit": credit,
                "breakeven_lo": be_down, "breakeven_hi": be_up,
                "prob_profit": prob, "ev": ev,
                "reward_risk": credit / max_loss,
            })
    return rows


def build_butterfly_candidates(liquid: Dict[float, Dict], F: float, sigma_sqrt_t: Optional[float]) -> List[Dict]:
    """Same liquid-chain shape as build_iron_condor_candidates."""
    liquid_call = sorted(k for k in liquid if liquid[k]["call_ok"])
    rows: List[Dict] = []
    seen = set()
    mid_candidates = [k for k in liquid_call if abs(k - F) / F <= BF_MID_BAND_PCT]
    for k2 in mid_candidates:
        for wpct in BF_WIDTHS_PCT:
            target_width = F * wpct
            k1 = _nearest_strike([k for k in liquid_call if k < k2], k2 - target_width)
            k3 = _nearest_strike([k for k in liquid_call if k > k2], k2 + target_width)
            if k1 is None or k3 is None:
                continue
            w_lo, w_hi = k2 - k1, k3 - k2
            if w_lo <= 0 or w_hi <= 0 or abs(w_lo - w_hi) / max(w_lo, w_hi) > 0.2:
                continue
            key = (k1, k2, k3)
            if key in seen:
                continue
            seen.add(key)

            cost = liquid[k1]["call_ask"] + liquid[k3]["call_ask"] - 2 * liquid[k2]["call_bid"]
            if cost is None or cost <= 0:
                continue
            max_profit = min(w_lo, w_hi) - cost
            if max_profit <= 0:
                continue
            be_lo = k1 + cost
            be_hi = k3 - cost
            prob, ev = _prob_and_ev(be_lo, be_hi, F, sigma_sqrt_t, max_profit, cost)

            rows.append({
                "structure_type": "butterfly",
                "k1": k1, "k2": k2, "k3": k3,
                "cost_or_credit": cost, "max_loss": cost, "max_profit": max_profit,
                "breakeven_lo": be_lo, "breakeven_hi": be_hi,
                "prob_profit": prob, "ev": ev,
                "reward_risk": max_profit / cost,
            })
    return rows


def iron_condor_payoff(candidate: Dict, settlement: float) -> float:
    """pnl in USD; caller divides by candidate['max_loss'] for return %, not cost_or_credit."""
    k1, k2, k3, k4 = candidate["short_call"], candidate["long_call"], candidate["short_put"], candidate["long_put"]
    call_spread_owed = max(settlement - k1, 0) - max(settlement - k2, 0)
    put_spread_owed = max(k3 - settlement, 0) - max(k4 - settlement, 0)
    return candidate["cost_or_credit"] - call_spread_owed - put_spread_owed


def butterfly_payoff(candidate: Dict, settlement: float) -> float:
    """pnl in USD; caller divides by candidate['cost_or_credit'] for return %."""
    k1, k2, k3 = candidate["k1"], candidate["k2"], candidate["k3"]
    payout = max(settlement - k1, 0) - 2 * max(settlement - k2, 0) + max(settlement - k3, 0)
    return payout - candidate["cost_or_credit"]
