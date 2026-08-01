"""
Result models for the fixed-strike vol change matrix
(institutional_metrics_spec.md section 7 / Task C8).

Frozen dataclasses, mirroring the pattern established by
``delta_flow_results.py``/``dealer_inventory_results.py``.
"""

from dataclasses import dataclass
from datetime import date as date_type
from typing import Optional, Tuple


@dataclass(frozen=True)
class StrikeIvChangeRow:
    """
    One matched strike's day-over-day IV change vs the same-period ATM
    change, produced by ``FixedStrikeVolCalculator.calculate``.

    "Matched" means this exact ``(strike, option_type)`` had a non-null
    ``mark_iv`` on BOTH the today and prior snapshot -- a strike present on
    only one side never gets a row (it is counted in
    ``FixedStrikeVolResult.n_strikes_unmatched`` instead, see
    institutional_metrics_spec.md section 7(c)'s edge cases).
    """

    strike: float
    option_type: str  # "C" | "P"
    iv_today: float  # mark_iv, in percent (e.g. 35.71 == 35.71%)
    iv_prior: float
    d_iv: float  # iv_today - iv_prior, in vol points (never a fraction)
    d_vs_atm: Optional[float]  # d_iv - d_atm; None only when d_atm itself is None
    moneyness_pct: Optional[float]  # |strike - spot_today| / spot_today * 100; None if spot_today unusable


@dataclass(frozen=True)
class FixedStrikeVolResult:
    """
    Full per-strike vol change matrix for one expiration, one day-over-day
    comparison (institutional_metrics_spec.md section 7(c)).

    ``regime`` is one of ``"STICKY_STRIKE"``, ``"STICKY_DELTA"``,
    ``"REPRICED"``, or ``"INDETERMINATE"`` -- the last one whenever the
    comparison cannot be trusted (stale/missing prior snapshot, missing ATM
    IV on either day, or zero matched strikes within the ATM region to
    evaluate the ladder against). See ``FixedStrikeVolCalculator.
    _determine_regime`` for the full gate.
    """

    expiration: str
    today_date: date_type
    prior_date: Optional[date_type]
    expected_prior_date: date_type  # today_date - 1 day; always computable
    stale_prior: bool  # True when prior_date is None or != expected_prior_date
    spot_today: Optional[float]
    spot_prior: Optional[float]
    spot_move_pct: Optional[float]
    atm_iv_today: Optional[float]
    atm_iv_prior: Optional[float]
    d_atm: Optional[float]
    rows: Tuple[StrikeIvChangeRow, ...]
    n_strikes_matched: int
    n_strikes_unmatched: int
    regime: str
