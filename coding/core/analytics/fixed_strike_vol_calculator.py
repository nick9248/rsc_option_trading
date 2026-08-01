"""
Fixed-strike vol change matrix (institutional_metrics_spec.md section 7 /
Task C8).

Day-over-day IV change PER STRIKE (not per delta -- that distinction is the
whole point of "fixed-strike") vs the same-period ATM IV change, with a
sticky-strike / sticky-delta / repriced attribution label
(institutional_metrics_spec.md section 7(b)):

    ΔIV_K = IV_K(t) - IV_K(t-1d)         per strike, same expiry, vol points
    ΔATM  = ATM_IV(t) - ATM_IV(t-1d)     same expiry
    rel_K = ΔIV_K - ΔATM                 strike move net of the ATM move

Pure: takes plain dicts and dates, never touches psycopg2 or the API. The
caller (``DatabaseRepository.get_chain_iv_at`` / ``OnChainAnalysisService``)
resolves what "today"/"yesterday" mean against a UTC clock and fetches the
two chain slices; this class only compares what it is given.

Section 11 judgment call #4 (institutional_metrics_spec.md): this metric
"cannot ship usefully before M1/M8" -- with the enabling data sources still
sparse, the spec's own decision is to "ship the calculator + the stale-prior
guard now and let it light up as the daemon fills in." This module is that
calculator; the stale-prior guard is ``_determine_regime``'s first check.
"""

import logging
from datetime import date as date_type, timedelta
from typing import Any, Dict, List, Optional

from coding.core.analytics.results.fixed_strike_vol_results import (
    FixedStrikeVolResult,
    StrikeIvChangeRow,
)

logger = logging.getLogger(__name__)

# institutional_metrics_spec.md section 7(b): "Evaluate the labels over
# strikes within +/-10% of spot only; wings are too illiquid to attribute."
ATM_REGION_PCT = 10.0

# institutional_metrics_spec.md section 7(b)'s literal ladder thresholds.
D_IV_STICKY_STRIKE_TOLERANCE = 0.5  # vol points
REL_STICKY_DELTA_TOLERANCE = 0.5  # vol points
ATM_MOVE_THRESHOLD = 1.0  # vol points

_RegimeKey = tuple  # (strike: float, option_type: str)


def compute_nearest_strike_atm_iv(
    rows: List[Dict[str, Any]], spot: Optional[float]
) -> Optional[float]:
    """
    ATM IV as the average of the closest call and put ``mark_iv`` to
    ``spot`` -- the nearest-strike convention
    (``VolatilitySurfaceCalculator._calculate_atm_iv`` uses the identical
    formula on the live chain).

    Neither ``daily_oi_snapshots`` nor (currently) ``snapshots`` carries a
    ``delta`` column, so section 3(b)'s delta-interpolated ATM read
    (|delta|=0.50) is not available for this task's historical data source.
    This helper is used for BOTH the today and prior chain in
    ``OnChainAnalysisService._build_fixed_strike_vol_matrix`` -- applying
    the SAME ATM definition on both days, rather than e.g. delta-
    interpolating "today" (where delta happens to be available from the
    live chain) against a nearest-strike "prior" (where it is not), which
    would silently compare two different quantities under one ``ΔATM``
    label.

    Args:
        rows: Chain rows, each with at least ``strike``, ``option_type``,
            ``mark_iv``. Rows with a ``None``/missing ``mark_iv`` are
            ignored.
        spot: Underlying price to measure distance from. ``None`` or
            non-positive returns ``None`` -- "nearest to spot" is undefined
            without a usable spot.

    Returns:
        Average of the closest call's and closest put's ``mark_iv``, or
        just one side's if only calls or only puts have a usable
        ``mark_iv``. ``None`` if ``rows`` has no usable entries at all.
    """
    if spot is None or spot <= 0:
        return None

    calls = [r for r in rows if r.get("option_type") == "C" and r.get("mark_iv") is not None]
    puts = [r for r in rows if r.get("option_type") == "P" and r.get("mark_iv") is not None]

    atm_ivs = []
    for group in (calls, puts):
        if group:
            closest = min(group, key=lambda r: abs(float(r["strike"]) - spot))
            atm_ivs.append(float(closest["mark_iv"]))

    return sum(atm_ivs) / len(atm_ivs) if atm_ivs else None


class FixedStrikeVolCalculator:
    """
    Pure: two dated chain slices -> per-strike IV deltas and a sticky-regime
    label (institutional_metrics_spec.md section 7(c)).

    Deviation from the spec's literal constructor signature (``today_rows,
    prior_rows, spot_today, spot_prior, atm_iv_today, atm_iv_prior`` only):
    this class also takes ``today_date`` and ``prior_date`` explicitly. The
    spec's own acceptance test T7.3 requires detecting a stale prior
    snapshot ("prior_rows dated 4 days back" -> INDETERMINATE) -- a real
    4-day-old snapshot and a real 1-day-old one look identical in row shape,
    so there is no way to make that determination from the rows/IVs alone.
    Passing the two dates explicitly keeps the class pure (no clock access;
    the caller resolves "today"/"yesterday" against UTC -- see
    ``OnChainAnalysisService._build_fixed_strike_vol_matrix``) while letting
    it own the "is this actually day-over-day" gate itself, per this
    campaign's repeated lesson (Tasks C4/C5/C7 fix rounds) that day-boundary
    logic must never be delegated past the component that needs to trust it.
    """

    def __init__(
        self,
        today_rows: List[Dict[str, Any]],
        prior_rows: List[Dict[str, Any]],
        spot_today: Optional[float],
        spot_prior: Optional[float],
        atm_iv_today: Optional[float],
        atm_iv_prior: Optional[float],
        today_date: date_type,
        prior_date: Optional[date_type],
        expiration: str = "",
    ):
        """
        Args:
            today_rows: Chain rows for "today", each a dict with at least
                ``strike``, ``option_type`` ("C"/"P"), and ``mark_iv``
                (percent, e.g. 35.71). A row with a ``None``/missing
                ``mark_iv`` is treated as if that strike were entirely
                absent from this side -- see ``_index_rows``.
            prior_rows: Same shape, for the comparison day.
            spot_today: Underlying price on the today snapshot, or ``None``
                if unavailable. Used only for ATM-region membership
                (moneyness) and ``spot_move_pct`` -- never for the IV
                arithmetic itself.
            spot_prior: Underlying price on the prior snapshot.
            atm_iv_today: ATM IV (percent) for this expiry, today. Computed
                by the caller (e.g. nearest-strike-to-spot average, or
                delta-interpolated at |delta|=0.50 per section 3 when delta
                is available) -- this class only consumes the number.
            atm_iv_prior: Same, for the comparison day.
            today_date: Calendar date (UTC) the "today" rows/IVs/spot were
                observed on.
            prior_date: Calendar date (UTC) the "prior" rows/IVs/spot were
                observed on, or ``None`` if there is no prior snapshot at
                all (never seen one, or the query found nothing).
            expiration: Expiration label, purely for display/identification
                on the returned result -- not used in any calculation.
        """
        self.today_rows = today_rows or []
        self.prior_rows = prior_rows or []
        self.spot_today = spot_today
        self.spot_prior = spot_prior
        self.atm_iv_today = atm_iv_today
        self.atm_iv_prior = atm_iv_prior
        self.today_date = today_date
        self.prior_date = prior_date
        self.expiration = expiration

    def calculate(self) -> FixedStrikeVolResult:
        """
        Build the full matrix. Never raises on any of the degenerate cases
        enumerated in the module/test docstrings -- every gap degrades a
        specific field to ``None``/``INDETERMINATE`` rather than fabricating
        a value or crashing.
        """
        expected_prior_date = self.today_date - timedelta(days=1)
        stale_prior = self.prior_date is None or self.prior_date != expected_prior_date

        today_by_key = self._index_rows(self.today_rows)
        prior_by_key = self._index_rows(self.prior_rows)

        today_keys = set(today_by_key)
        prior_keys = set(prior_by_key)
        matched_keys = today_keys & prior_keys
        unmatched_keys = today_keys ^ prior_keys

        d_atm: Optional[float] = None
        if self.atm_iv_today is not None and self.atm_iv_prior is not None:
            d_atm = self.atm_iv_today - self.atm_iv_prior

        spot_move_pct = self._compute_spot_move_pct()

        rows: List[StrikeIvChangeRow] = []
        for strike, option_type in sorted(matched_keys):
            iv_today = today_by_key[(strike, option_type)]
            iv_prior = prior_by_key[(strike, option_type)]
            d_iv = iv_today - iv_prior
            d_vs_atm = (d_iv - d_atm) if d_atm is not None else None
            moneyness_pct = self._moneyness_pct(strike)

            rows.append(StrikeIvChangeRow(
                strike=strike,
                option_type=option_type,
                iv_today=iv_today,
                iv_prior=iv_prior,
                d_iv=d_iv,
                d_vs_atm=d_vs_atm,
                moneyness_pct=moneyness_pct,
            ))

        regime = self._determine_regime(stale_prior, d_atm, rows)

        return FixedStrikeVolResult(
            expiration=self.expiration,
            today_date=self.today_date,
            prior_date=self.prior_date,
            expected_prior_date=expected_prior_date,
            stale_prior=stale_prior,
            spot_today=self.spot_today,
            spot_prior=self.spot_prior,
            spot_move_pct=spot_move_pct,
            atm_iv_today=self.atm_iv_today,
            atm_iv_prior=self.atm_iv_prior,
            d_atm=d_atm,
            rows=tuple(rows),
            n_strikes_matched=len(matched_keys),
            n_strikes_unmatched=len(unmatched_keys),
            regime=regime,
        )

    @staticmethod
    def _index_rows(rows: List[Dict[str, Any]]) -> Dict[_RegimeKey, float]:
        """
        Index rows by ``(strike, option_type)``, keeping only rows with a
        usable (non-null, numeric) ``mark_iv``.

        A row with ``mark_iv is None`` (or missing) is dropped entirely
        here -- NOT indexed with some sentinel -- so it is indistinguishable
        from that strike never having appeared on this side at all. This is
        deliberate: "IV missing today" and "strike didn't exist today" both
        mean the same thing for this calculator's purpose (there is nothing
        to compare), and both must land in ``n_strikes_unmatched`` rather
        than fabricate a comparison against a ``None``.

        A duplicate ``(strike, option_type)`` key on one side (should not
        occur given the DB's unique constraint on both
        ``daily_oi_snapshots`` and ``snapshots``, but defensive) keeps the
        LAST value seen -- no averaging, no crash.
        """
        indexed: Dict[_RegimeKey, float] = {}
        for row in rows:
            strike = row.get("strike")
            option_type = row.get("option_type")
            mark_iv = row.get("mark_iv")
            if strike is None or option_type is None or mark_iv is None:
                continue
            try:
                key = (float(strike), option_type)
                indexed[key] = float(mark_iv)
            except (TypeError, ValueError):
                # Defensive: a malformed strike/mark_iv value. Treated the
                # same as missing -- excluded, never crashes the whole
                # matrix over one bad row.
                logger.warning(
                    "Skipping unparseable chain row for fixed-strike vol matrix: %r", row,
                )
                continue
        return indexed

    def _compute_spot_move_pct(self) -> Optional[float]:
        if self.spot_today is None or self.spot_prior is None or self.spot_prior <= 0:
            return None
        return (self.spot_today - self.spot_prior) / self.spot_prior * 100.0

    def _moneyness_pct(self, strike: float) -> Optional[float]:
        """
        Distance from ``strike`` to ``spot_today``, as a percentage of
        spot. ``None`` when ``spot_today`` is missing/non-positive -- ATM
        region membership cannot be determined for any strike in that case,
        which the regime gate treats as "nothing to evaluate."
        """
        if self.spot_today is None or self.spot_today <= 0:
            return None
        return abs(strike - self.spot_today) / self.spot_today * 100.0

    def _determine_regime(
        self,
        stale_prior: bool,
        d_atm: Optional[float],
        rows: List[StrikeIvChangeRow],
    ) -> str:
        """
        Apply institutional_metrics_spec.md section 7(b)'s attribution
        ladder, evaluated only over matched rows within
        ``ATM_REGION_PCT`` of spot.

        Returns ``"INDETERMINATE"`` (never fabricates STICKY_*/REPRICED)
        when:
        - the prior snapshot is stale or absent (T7.3's guard), or
        - ``d_atm`` itself is unavailable (missing ATM IV on either day --
          the whole ladder is defined in terms of ΔATM), or
        - there are zero matched rows within the ATM region to evaluate the
          ladder against (empty prior day, missing spot so moneyness cannot
          be computed for anything, or every ATM-region strike happened to
          be unmatched) -- this is NOT in the spec's literal ladder text,
          but "otherwise -> REPRICED" would otherwise fire on a literal
          absence of data, which is exactly the "fabricate a comparison
          from nothing" failure this whole task exists to avoid.

        Otherwise applies the spec's ladder literally, including its
        "otherwise -> REPRICED" fallback for the case where |d_atm| <= 1.0
        (neither sticky condition is reachable, per the spec's own gating)
        -- see this module's test file
        (``test_small_atm_move_falls_through_to_repriced_per_literal_spec_ladder``)
        for the acknowledgment that this is a literal reading, not
        necessarily the most intuitive one, of a spec text with no
        acceptance test covering that specific combination.
        """
        if stale_prior or d_atm is None:
            return "INDETERMINATE"

        atm_region_rows = [
            row for row in rows
            if row.moneyness_pct is not None and row.moneyness_pct <= ATM_REGION_PCT
        ]
        if not atm_region_rows:
            return "INDETERMINATE"

        if abs(d_atm) > ATM_MOVE_THRESHOLD:
            if all(abs(row.d_iv) <= D_IV_STICKY_STRIKE_TOLERANCE for row in atm_region_rows):
                return "STICKY_STRIKE"
            if all(
                row.d_vs_atm is not None and abs(row.d_vs_atm) <= REL_STICKY_DELTA_TOLERANCE
                for row in atm_region_rows
            ):
                return "STICKY_DELTA"

        return "REPRICED"
