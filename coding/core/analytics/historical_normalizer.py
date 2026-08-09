"""
Historical normalization framework (institutional_metrics_spec.md section 1).

Turns a metric's raw current value into a percentile + z-score + regime
label against its own trailing history, so a report reads "where does this
sit relative to its own recent distribution" instead of a bare number
compared to an unrelated equity-derived threshold.

Pure math only: takes plain lists of floats, never touches psycopg2,
requests, or any repository. Everything else (repository queries, service
wiring, report rendering) feeds this class -- see
``coding/core/database/repository.py:get_metric_history`` and
``OnChainAnalysisService._build_normalized_metrics`` for the DB/service side.
"""

from dataclasses import dataclass
from math import sqrt
from typing import Dict, List, Optional, Sequence

MIN_OBS = 30
"""Below this many observations, percentile/z are None -- the honest answer
for "not enough history for a meaningful comparison" (spec T1.3)."""

WINDOW_DAYS_30D = 30.0
WINDOW_DAYS_90D = 90.0
"""Calendar days a "30d"/"90d" window claims to cover. Used only by the
span-sufficiency check below -- unrelated to ``MIN_OBS``, which is a pure
observation count."""

MIN_SPAN_FRACTION = 0.8
"""Task G2-E (confirmed live, cross-checked against the DB): MIN_OBS alone
is not a sufficient sufficiency gate. A per-expiry percentile series (net
GEX/PCR-OI/total OI for one specific expiry string, e.g. "8AUG26") is keyed
to that expiry's own identifier, which structurally cannot exist -- and so
cannot have been observed by the hourly collector -- 30 or 90 days before
its own expiration. The audit's confirmed case: one expiry's series had 89
hourly observations (>= MIN_OBS for BOTH the 30d and 90d windows) spanning
only 3.75 calendar days (oldest=2026-08-04 08:00, newest=2026-08-08 02:00),
yet the report rendered "90d: p97 z+2.49 EXTREME HIGH" -- identical to its
own 30d column -- when the data cannot possibly represent 90, or even 30,
days of history.

Fix: in addition to n >= MIN_OBS, require the oldest observation to be at
least MIN_SPAN_FRACTION of the claimed window's days-ago before labeling a
percentile with that window's name. 0.8 is a deliberate compromise:

- Strict enough to reject the confirmed case for BOTH windows (a 3.75-day
  span is 3.75/30 = 12.5% of the 30d window and 3.75/90 = 4.2% of the 90d
  window -- nowhere near 80%).
- Loose enough to tolerate ordinary collection gaps (daemon restarts, VPS
  downtime -- documented elsewhere in this codebase as a real occurrence,
  not a hypothetical) in a genuinely 30-to-90-day-old series without
  spuriously flagging real history as insufficient.

There is no prior convention in this codebase for calendar-span-vs-count
sufficiency to copy -- this constant and its 0.8 threshold are this task's
own judgment call, not a value pulled from institutional_metrics_spec.md.
"""


@dataclass(frozen=True)
class NormalizedMetric:
    """One metric's value plus its 30d/90d percentile, z-score, and regime."""

    name: str
    value: float
    percentile_30d: Optional[float]
    z_30d: Optional[float]
    percentile_90d: Optional[float]
    z_90d: Optional[float]
    regime_30d: Optional[str]  # ladder applied to percentile_30d
    n_30d: int
    n_90d: int
    sufficient: bool  # n_30d >= MIN_OBS AND span_days_30d passes the span gate
    unit: str  # "USD" | "ratio" | "vol pts" | "%" | "coins"
    span_days_30d: Optional[float] = None
    span_days_90d: Optional[float] = None
    """Age in days (as of when the history was fetched) of the OLDEST
    observation in each window -- Task G2-E. For a continuously-collected
    trailing series this approximates how many calendar days of history
    the window actually contains, which is what the count-only MIN_OBS
    gate could not see. None when the caller didn't supply span data (see
    ``HistoricalNormalizer.normalize``'s ``oldest_age_days_30d``/``_90d``
    params) -- callers that skip this are exempted from the span gate, not
    silently failed by it; every live call site is expected to supply it."""


@dataclass(frozen=True)
class MetricSpec:
    """One metric's raw inputs, batched through ``normalize_many``."""

    name: str
    value: float
    history_30d: Sequence[float]
    history_90d: Sequence[float]
    unit: str
    oldest_age_days_30d: Optional[float] = None
    oldest_age_days_90d: Optional[float] = None
    """Age in days (as of the fetch) of the oldest row in ``history_30d``/
    ``history_90d`` -- Task G2-E. The repository/service layer owns turning
    a DB timestamp into this plain float (mirrors how Decimal -> float
    conversion happens at the repository boundary, never inside this pure-
    math module); None means "span unknown," which exempts that window
    from the calendar-span gate rather than failing it -- see
    ``HistoricalNormalizer``'s ``MIN_SPAN_FRACTION`` docstring."""


class HistoricalNormalizer:
    """
    Computes percentile, z-score, and regime label for a metric's current
    value against its own trailing history (institutional_metrics_spec.md
    section 1(b)).

    Formulas (given trailing window W of observations x_1..x_n, oldest to
    newest, EXCLUDING the current value, and the current value v):

        percentile(v, W) = 100 * (count(x < v) + 0.5 * count(x == v)) / n
        mean             = (1/n) sum(x)
        sd               = sqrt((1/n) sum((x - mean)^2))   [population, ddof=0]
        z(v, W)          = (v - mean) / sd   if sd > 0 else None

    Mid-rank percentile (not strict `<`) so a constant series returns 50,
    not 0 -- the honest "where does this sit" answer when the distribution
    is degenerate. ddof=0 because the window is the complete observed
    history for that lookback, not a sample drawn from it.
    """

    MIN_OBS = MIN_OBS
    WINDOW_DAYS_30D = WINDOW_DAYS_30D
    WINDOW_DAYS_90D = WINDOW_DAYS_90D
    MIN_SPAN_FRACTION = MIN_SPAN_FRACTION

    # Regime ladder: percentile -> label. Checked high-to-low; a single
    # ladder applied to every metric, no per-metric folklore thresholds.
    REGIME_BANDS = (
        (95.0, "EXTREME HIGH"),
        (80.0, "HIGH"),
        (60.0, "ELEVATED"),
        (40.0, "NORMAL"),
        (20.0, "SUBDUED"),
        (5.0, "LOW"),
    )
    REGIME_FLOOR_LABEL = "EXTREME LOW"

    @staticmethod
    def percentile(value: float, series: Sequence[float]) -> Optional[float]:
        """Mid-rank percentile of ``value`` within ``series``. None if empty."""
        n = len(series)
        if n == 0:
            return None
        below = sum(1 for x in series if x < value)
        equal = sum(1 for x in series if x == value)
        return 100.0 * (below + 0.5 * equal) / n

    @staticmethod
    def zscore(value: float, series: Sequence[float]) -> Optional[float]:
        """Population z-score of ``value`` within ``series``.

        None if ``series`` is empty or has zero variance (a constant
        series has no meaningful "how many standard deviations away").
        """
        n = len(series)
        if n == 0:
            return None
        mean = sum(series) / n
        variance = sum((x - mean) ** 2 for x in series) / n
        sd = sqrt(variance)
        if sd == 0.0:
            return None
        return (value - mean) / sd

    @classmethod
    def has_sufficient_span(
        cls, span_days: Optional[float], window_days: float,
    ) -> bool:
        """
        True iff ``span_days`` (the oldest observation's age in days) is at
        least ``MIN_SPAN_FRACTION`` of ``window_days`` (Task G2-E).

        ``span_days is None`` returns True -- "span unknown" exempts a
        window from this gate rather than failing it, so callers that
        genuinely have no timestamp data (this module's own count-only
        unit tests, or a caller that hasn't been wired up yet) keep the
        prior n-only behavior. Every LIVE call site (the service layer
        feeding real DB history into ``MetricSpec``) is expected to supply
        a real ``span_days`` so the gate actually applies -- see
        ``OnChainAnalysisService._build_normalized_metrics``.

        This is the single source of truth for the span check: both
        ``normalize`` (the 30d gate) and
        ``historical_context_formatter._format_metric_line`` (recomputing
        the 90d gate at render time, matching the existing pattern for
        ``regime_90d``) call this same method so the two windows can never
        silently disagree on what "sufficient" means.
        """
        if span_days is None:
            return True
        return span_days >= cls.MIN_SPAN_FRACTION * window_days

    @classmethod
    def regime_label(cls, percentile: Optional[float]) -> Optional[str]:
        """Map a percentile to its regime label via the shared ladder."""
        if percentile is None:
            return None
        for floor, label in cls.REGIME_BANDS:
            if percentile >= floor:
                return label
        return cls.REGIME_FLOOR_LABEL

    def normalize(
        self,
        name: str,
        value: float,
        history_30d: Sequence[float],
        history_90d: Sequence[float],
        unit: str,
        oldest_age_days_30d: Optional[float] = None,
        oldest_age_days_90d: Optional[float] = None,
    ) -> NormalizedMetric:
        """
        Build a ``NormalizedMetric`` for one value against its 30d and 90d
        trailing history.

        Args:
            name: Metric name (e.g. "net_gex", "pcr_oi").
            value: Current reading. Signed metrics that legitimately cross
                zero (net GEX, RR, funding) are normalized on the raw signed
                value, never abs(value).
            history_30d: Trailing 30-day history, oldest-first, current
                value excluded, NULLs already dropped.
            history_90d: Trailing 90-day history, same conventions.
            unit: Display unit ("USD" | "ratio" | "vol pts" | "%" | "coins").
            oldest_age_days_30d: Age in days of the oldest observation in
                ``history_30d`` (Task G2-E). None skips the calendar-span
                gate for this window (see ``has_sufficient_span``) --
                real callers are expected to supply this so a per-expiry
                series that racks up n >= MIN_OBS in a handful of calendar
                days (a front-month expiry's own percentile series, by
                construction, cannot span 30-90 days) is correctly gated
                as insufficient rather than silently reporting as if it
                had genuine 30d/90d depth.
            oldest_age_days_90d: Same, for ``history_90d``.

        Returns:
            NormalizedMetric. When len(history_30d) < MIN_OBS, or the
            calendar span requirement isn't met, percentile_30d/z_30d/
            regime_30d are None and sufficient=False, but ``value`` and
            both ``n_*``/``span_days_*`` fields are still populated.
        """
        n_30d = len(history_30d)
        n_90d = len(history_90d)
        sufficient = n_30d >= self.MIN_OBS and self.has_sufficient_span(
            oldest_age_days_30d, self.WINDOW_DAYS_30D,
        )
        sufficient_90d = n_90d >= self.MIN_OBS and self.has_sufficient_span(
            oldest_age_days_90d, self.WINDOW_DAYS_90D,
        )

        if sufficient:
            percentile_30d = self.percentile(value, history_30d)
            z_30d = self.zscore(value, history_30d)
            regime_30d = self.regime_label(percentile_30d)
        else:
            percentile_30d = None
            z_30d = None
            regime_30d = None

        if sufficient_90d:
            percentile_90d = self.percentile(value, history_90d)
            z_90d = self.zscore(value, history_90d)
        else:
            percentile_90d = None
            z_90d = None

        return NormalizedMetric(
            name=name,
            value=value,
            percentile_30d=percentile_30d,
            z_30d=z_30d,
            percentile_90d=percentile_90d,
            z_90d=z_90d,
            regime_30d=regime_30d,
            n_30d=n_30d,
            n_90d=n_90d,
            sufficient=sufficient,
            unit=unit,
            span_days_30d=oldest_age_days_30d,
            span_days_90d=oldest_age_days_90d,
        )

    def normalize_many(self, specs: List[MetricSpec]) -> Dict[str, NormalizedMetric]:
        """Normalize a batch of ``MetricSpec``s, keyed by metric name."""
        return {
            spec.name: self.normalize(
                name=spec.name,
                value=spec.value,
                history_30d=spec.history_30d,
                history_90d=spec.history_90d,
                unit=spec.unit,
                oldest_age_days_30d=spec.oldest_age_days_30d,
                oldest_age_days_90d=spec.oldest_age_days_90d,
            )
            for spec in specs
        }
