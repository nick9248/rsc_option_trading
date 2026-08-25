"""
Result models for per-strike vanna/charm exposure profiles (VEX/CEX,
institutional_metrics_spec.md section 4, Task C5).

Frozen dataclasses, mirroring the pattern established by
``gex_dex_results.py`` (bugfix_spec.md Item 8 / task B1's holder-side-raw +
labeled-assumed-dealer-view convention -- D7, established Wave B/task B2 and
reused here unchanged). ``ExposureProfileCalculator.calculate()`` itself
returns one convention's numbers per call (a plain dict, matching
institutional_metrics_spec.md section 4(c)'s Core class signature); this
module's ``ExposureProfileResult`` is the SERVICE-layer composition of two
calls (holder + assumed_dealer) into the single typed shape the report
formatter and ``ExpirationBundle`` consume -- the same "same shape as
GexDexCalculator output" the spec's Core-class docstring asks for.
"""

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class ExposureStrikeRow:
    """
    Per-strike vanna/charm exposure, both side conventions side by side
    (matching the report sample's "holder value [ dealer value ]" columns).

    ``call_vanna``/``put_vanna`` (and the charm equivalents) are the SAME
    positive/negative number regardless of option leg for a given strike/IV/
    tau -- institutional_metrics_spec.md section 4(b): "Both quantities are
    identical for calls and puts... all sign information comes from the
    position side." Kept per-leg (rather than a single ``vanna``/``charm``
    field) because each leg can carry a slightly different mark_iv (smile
    noise), so they are not FORCED equal, only expected to be close.
    """

    strike: float
    call_oi: float
    put_oi: float
    call_vanna: float
    put_vanna: float
    call_charm: float
    put_charm: float
    vex_holder: float
    cex_holder: float
    vex_assumed_dealer: float
    cex_assumed_dealer: float


@dataclass(frozen=True)
class ExposureProfileResult:
    """
    Full per-expiration VEX/CEX exposure-profile result (holder-side raw +
    assumed-dealer view together), Task C5 / institutional_metrics_spec.md
    section 4.

    Per-strike history is deliberately NOT persisted (~15.8M rows/year, spec
    4(c)) -- only ``total_vex_holder``/``total_cex_holder``/
    ``total_vex_assumed_dealer``/``total_cex_assumed_dealer``/
    ``peak_vanna_strike``/``peak_charm_strike`` are (Migration 019, populated
    by ``VolatilityReconstructionService._calculate_exposure_aggregates``).
    This full per-strike result exists only for the live report/GUI display.
    """

    strike_rows: Tuple[ExposureStrikeRow, ...]
    spot_price: float
    currency: str
    total_vex_holder: float
    total_cex_holder: float
    total_vex_assumed_dealer: float
    total_cex_assumed_dealer: float
    peak_vanna_strike: Optional[float] = None
    """Strike with the largest |VEX| (HOLDER-side convention, matching
    Migration 019's persisted vex_peak_strike column)."""

    peak_charm_strike: Optional[float] = None
    """Strike with the largest |CEX| (HOLDER-side convention, matching
    Migration 019's persisted cex_peak_strike column)."""

    skipped_instruments: int = 0
