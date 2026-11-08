"""
The numbers one monitor run stores (table ``menthorq_level_snapshots``,
migration 031), built from a ``replicate_menthorq`` result.

Level rules are the ones verified on three MenthorQ dates (see
``menthorq_replication_calculator.DEFAULT_LEVEL_CANDIDATES``): Call
Resistance = strike with the highest positive net GEX, Put Support = strike
with the lowest negative net GEX, HVL = strike where cumulative net GEX
(from the lowest strike up) is lowest. Gamma at the index price. Panels are
MenthorQ's four "NET GEX Multi Expirations" panels, ranked by |net GEX|.

Pure: no database, network or plotting imports.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from coding.core.analytics.menthorq_replication_calculator import (
    IV_SOURCE_MISSING,
    IV_SOURCE_REBUILT_FROM_MARK_PRICE,
    LEVEL_NAMES,
    PANEL_NAMES,
    PANEL_RANKING_ABSOLUTE_NET,
    select_drawn_levels,
)

MODE_LIVE = "live"
MODE_DAILY_CLOSE = "daily_close"
MODES = (MODE_LIVE, MODE_DAILY_CLOSE)

GAMMA_REGIME_POSITIVE = "positive"   # spot above HVL
GAMMA_REGIME_NEGATIVE = "negative"   # spot below HVL
GAMMA_REGIME_AT_HVL = "at_hvl"       # spot exactly on HVL

LEVEL_LABELS = {"call_resistance": "Call Resistance", "put_support": "Put Support", "hvl": "HVL"}
# A run counts as COMPUTED when at least one of these is set (``has_levels``).
# Spot alone does not count: a run can load its inputs and still compute nothing.
LEVEL_PRESENCE_COLUMNS = ("call_resistance", "put_support", "hvl", "total_net_gex")


def has_levels_sql_condition(row_reference: str) -> str:
    """
    The SQL form of ``MenthorqLevelRecord.has_levels`` for the
    ``menthorq_level_snapshots`` row ``row_reference`` (a table name or
    ``EXCLUDED``), e.g. ``(EXCLUDED.call_resistance IS NOT NULL OR ...)``.
    """
    return "(" + " OR ".join(f"{row_reference}.{column} IS NOT NULL"
                             for column in LEVEL_PRESENCE_COLUMNS) + ")"


@dataclass
class MenthorqLevelRecord:
    """
    One monitor run. Missing values are None, with the reason listed in
    ``not_computed_reasons``. Levels are strikes in USD; ``total_net_gex``
    is USD per 1% move.
    """

    currency: str
    mode: str
    as_of_utc: datetime
    computed_at_utc: datetime
    expiry_universe: str
    gamma_underlying: str
    spot: Optional[float]
    spot_time_utc: Optional[datetime]
    snapshot_captured_at_utc: Optional[datetime]
    open_interest_captured_at_utc: Optional[datetime]
    call_resistance: Optional[float]
    put_support: Optional[float]
    hvl: Optional[float]
    total_net_gex: Optional[float]
    gamma_regime: Optional[str]
    expirations: List[str] = field(default_factory=list)
    panel_levels: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    iv_source_counts: Dict[str, int] = field(default_factory=dict)
    not_computed_reasons: List[str] = field(default_factory=list)
    record_id: Optional[int] = None

    def level(self, level_name: str) -> Optional[float]:
        """Value of "call_resistance", "put_support" or "hvl"."""
        if level_name not in LEVEL_NAMES:
            raise ValueError(f"Unknown level {level_name!r}")
        return getattr(self, level_name)

    @property
    def has_levels(self) -> bool:
        """
        True when the run is COMPUTED: at least one of Call Resistance, Put
        Support, HVL and total net GEX is set (``LEVEL_PRESENCE_COLUMNS``).
        The one definition of "computed" for storing, retrying, health and
        alerts.
        """
        return any(getattr(self, column) is not None for column in LEVEL_PRESENCE_COLUMNS)


def classify_gamma_regime(spot: Optional[float], hvl: Optional[float]) -> Optional[str]:
    """
    MenthorQ's regime reading: spot above HVL = positive gamma, below =
    negative gamma. None when either value is missing.
    """
    if spot is None or hvl is None:
        return None
    if spot > hvl:
        return GAMMA_REGIME_POSITIVE
    if spot < hvl:
        return GAMMA_REGIME_NEGATIVE
    return GAMMA_REGIME_AT_HVL


def _not_computed_reasons(
    levels: Dict[str, Optional[float]],
    total_net_gex: Optional[float],
    replication: Dict[str, Any],
    panels: Dict[str, Optional[str]],
    iv_source_counts: Dict[str, int],
    rows_without_open_interest: int,
) -> List[str]:
    reasons: List[str] = []
    if not replication["settings"]["expirations"]:
        reasons.append("no unsettled expiry in the expiry universe")
    if total_net_gex is None:
        reasons.append("total net GEX not computed: no leg has both gamma and open interest")
    if levels["call_resistance"] is None:
        reasons.append("Call Resistance not computed: no strike with positive net GEX")
    if levels["put_support"] is None:
        reasons.append("Put Support not computed: no strike with negative net GEX")
    if levels["hvl"] is None:
        reasons.append("HVL not computed: no net GEX by strike")
    for expiration, exposure in replication["gex_by_expiration"].items():
        if exposure["total_net_gex"] is None:
            reasons.append(f"{expiration}: GEX not computed (no leg has both gamma and open interest)")
    for panel_name in PANEL_NAMES:
        if panels.get(panel_name) is None:
            reasons.append(f"panel {panel_name}: no expiry with computed GEX")
    if iv_source_counts.get(IV_SOURCE_MISSING):
        reasons.append(f"{iv_source_counts[IV_SOURCE_MISSING]} chain rows have no IV "
                       "(no mark_iv and none rebuilt): their gamma is not computed")
    if rows_without_open_interest:
        reasons.append(f"{rows_without_open_interest} chain rows have no open interest: "
                       "left out of GEX")
    return reasons


def build_level_record(
    replication: Dict[str, Any],
    inputs: Dict[str, Any],
    currency: str,
    mode: str,
    as_of_utc: datetime,
    computed_at_utc: datetime,
) -> MenthorqLevelRecord:
    """
    The stored numbers of one run.

    Args:
        replication: ``replicate_menthorq`` result for this run.
        inputs: ``MenthorqInputLoader.load_inputs`` result the replication
            was computed from (capture times, spot, IV source counts, rows).
        currency: e.g. "BTC".
        mode: "live" or "daily_close".
        as_of_utc: The evaluation instant (no data after it was used).
        computed_at_utc: Wall-clock time of the computation.

    Returns:
        The record (``record_id`` None until stored).
    """
    if mode not in MODES:
        raise ValueError(f"Unknown mode {mode!r}")
    settings = replication["settings"]
    levels = select_drawn_levels(replication["iv_times_open_interest"]["levels"])
    total_net_gex = replication["iv_times_open_interest"]["all_expiries_exposure"]["total_net_gex"]
    panels = replication["panels"][PANEL_RANKING_ABSOLUTE_NET]
    expiring_percent = replication["gex_expiring_percent"]

    panel_levels: Dict[str, Dict[str, Any]] = {}
    for panel_name in PANEL_NAMES:
        expiration = panels.get(panel_name)
        if expiration is None:
            panel_levels[panel_name] = {"expiration": None, "call_resistance": None,
                                        "put_support": None, "hvl": None,
                                        "total_net_gex": None, "gex_expiring_percent": None}
            continue
        exposure = replication["gex_by_expiration"][expiration]
        panel_levels[panel_name] = {
            "expiration": expiration,
            **select_drawn_levels(exposure["levels"]),
            "total_net_gex": exposure["total_net_gex"],
            # Approximate (errors up to ~5 points vs MenthorQ, see the vault note).
            "gex_expiring_percent": expiring_percent[expiration]["net_over_absolute_net_sum"],
        }

    wanted = set(settings["expirations"])
    rows_without_open_interest = sum(
        1 for row in inputs["chain_rows"]
        if row.get("expiration") in wanted and row.get("open_interest") is None
    )
    iv_source_counts = dict(inputs["iv_source_counts"])
    spot = settings["index_price"]
    return MenthorqLevelRecord(
        currency=currency,
        mode=mode,
        as_of_utc=as_of_utc,
        computed_at_utc=computed_at_utc,
        expiry_universe=settings["expiry_universe"],
        gamma_underlying=settings["gamma_underlying"],
        spot=spot,
        spot_time_utc=inputs["index_price_time_utc"],
        snapshot_captured_at_utc=inputs["snapshot_captured_at_utc"],
        open_interest_captured_at_utc=inputs["open_interest_captured_at_utc"],
        call_resistance=levels["call_resistance"],
        put_support=levels["put_support"],
        hvl=levels["hvl"],
        total_net_gex=total_net_gex,
        gamma_regime=classify_gamma_regime(spot, levels["hvl"]),
        expirations=list(settings["expirations"]),
        panel_levels=panel_levels,
        iv_source_counts=iv_source_counts,
        not_computed_reasons=_not_computed_reasons(
            levels, total_net_gex, replication, panels, iv_source_counts,
            rows_without_open_interest,
        ),
    )


def build_not_computed_record(
    currency: str,
    mode: str,
    as_of_utc: datetime,
    computed_at_utc: datetime,
    expiry_universe: str,
    gamma_underlying: str,
    reason: str,
) -> MenthorqLevelRecord:
    """
    A record for a run whose inputs could not be loaded (e.g. no snapshot or
    index price at or before ``as_of_utc``): every number None, the reason
    stored, so a gap is visible in the table instead of silent.
    """
    if mode not in MODES:
        raise ValueError(f"Unknown mode {mode!r}")
    return MenthorqLevelRecord(
        currency=currency, mode=mode, as_of_utc=as_of_utc, computed_at_utc=computed_at_utc,
        expiry_universe=expiry_universe, gamma_underlying=gamma_underlying,
        spot=None, spot_time_utc=None, snapshot_captured_at_utc=None,
        open_interest_captured_at_utc=None, call_resistance=None, put_support=None, hvl=None,
        total_net_gex=None, gamma_regime=None, not_computed_reasons=[reason],
    )


def rebuilt_iv_count(record: MenthorqLevelRecord) -> int:
    """Chain rows whose IV was rebuilt from mark_price (0 when none or unknown)."""
    return int(record.iv_source_counts.get(IV_SOURCE_REBUILT_FROM_MARK_PRICE, 0))
