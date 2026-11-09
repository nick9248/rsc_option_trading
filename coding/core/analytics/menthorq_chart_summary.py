"""
Display-ready summary of a ``MenthorqReplicationService.run`` output for the
MenthorQ Charts GUI tab: which PNG belongs to which chart, the levels each
chart draws (Call Resistance, Put Support, HVL, spot), the panel expiries
with their GEX Expiring %, a one-line description of the inputs, data
warnings, and the drawn levels next to a MenthorQ reference.

Pure: reads the output dict only (no database, no files). The only
arithmetic is "ours minus reference" in the comparison rows. A value that
was not computed stays None; the GUI shows it as "not computed", never 0.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from coding.core.analytics.menthorq_chart_renderer import PANEL_TITLES
from coding.core.analytics.menthorq_monitor.level_record import LEVEL_LABELS, all_expiries_levels
from coding.core.analytics.menthorq_replication_calculator import (
    DEFAULT_GEX_EXPIRING_CANDIDATE,
    DEFAULT_LEVEL_CANDIDATES,
    EXPIRY_UNIVERSE_ALL,
    IV_SOURCE_MISSING,
    IV_SOURCE_REBUILT_FROM_MARK_PRICE,
    PANEL_NAMES,
    PANEL_RANKING_ABSOLUTE_NET,
    iv_source_text,
    parse_expiry_settlement,
    select_drawn_levels,
)

CHART_IV_TIMES_OPEN_INTEREST = "iv_times_open_interest"
CHART_IV_TIMES_OPEN_INTEREST_EXPIRY = "iv_times_open_interest_expiry"
CHART_NET_GEX_PANELS = "net_gex_panels"
CHART_NET_DEX = "net_dex"
CHART_ALL = "all"
HVL_CHAIN_EDGE_NOTE = "(chain edge: not used by the monitor)"

RUN_MODE_DESCRIPTIONS = {
    "as_of": "Replay",
    "menthorq_daily_close": "MenthorQ daily close",
    "live": "Live (newest stored snapshot)",
}


def _primary_replication(output: Dict[str, Any]) -> Dict[str, Any]:
    """The replication of the configuration the charts were rendered for."""
    return output["configurations"][output["primary_configuration"]]["replication"]


def _expiration_date(expiration: Optional[str]) -> Optional[str]:
    """ISO settlement date of a Deribit expiry label, or None."""
    settlement = parse_expiry_settlement(expiration) if expiration else None
    return settlement.date().isoformat() if settlement else None


def _format_utc(iso_text: str) -> str:
    """'2026-11-06T23:30:12.5+00:00' -> '2026-11-06 23:30:12 UTC'."""
    return datetime.fromisoformat(iso_text).strftime("%Y-%m-%d %H:%M:%S UTC")


def expiry_choices(output: Dict[str, Any]) -> List[str]:
    """Expiries that have their own IV*OI chart, in chronological order."""
    return [entry["expiration"] for entry in output["per_expiry_iv_times_open_interest"]]


def chart_file_names(
    output: Dict[str, Any], chart: str, expiration: Optional[str] = None
) -> List[str]:
    """
    PNG file names (in the run's output folder) shown for ``chart``.

    Args:
        output: ``MenthorqReplicationService.run`` result.
        chart: One of the ``CHART_*`` constants; ``CHART_ALL`` lists the
            three all-expiry charts and then every per-expiry chart.
        expiration: Expiry label, used by ``CHART_IV_TIMES_OPEN_INTEREST_EXPIRY``.

    Returns:
        File names; empty when the chart (or the expiry) does not exist.
    """
    per_expiry = {entry["expiration"]: entry["chart"]
                  for entry in output["per_expiry_iv_times_open_interest"]}
    if chart == CHART_ALL:
        names = [output["charts"][name] for name in
                 (CHART_IV_TIMES_OPEN_INTEREST, CHART_NET_GEX_PANELS, CHART_NET_DEX)]
        return names + list(per_expiry.values())
    if chart == CHART_IV_TIMES_OPEN_INTEREST_EXPIRY:
        return [per_expiry[expiration]] if expiration in per_expiry else []
    name = output["charts"].get(chart)
    return [name] if name else []


def _level_row(
    scope: str, expiration: Optional[str], levels: Dict[str, Optional[float]],
    spot: float, gex_expiring_percent: Optional[float] = None,
    gex_expiring_percent_applies: bool = False,
) -> Dict[str, Any]:
    """One row of the levels table."""
    return {
        "scope": scope,
        "expiration": expiration,
        "expiration_date": _expiration_date(expiration),
        "call_resistance": levels.get("call_resistance"),
        "put_support": levels.get("put_support"),
        "hvl": levels.get("hvl"),
        "spot": spot,
        "gex_expiring_percent": gex_expiring_percent,
        "gex_expiring_percent_applies": gex_expiring_percent_applies,
        "hvl_note": None,
    }


def levels_rows(
    output: Dict[str, Any], chart: str, expiration: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Levels drawn on ``chart``: one row per level set.

    - IV*OI all expiries / Net DEX: the all-expiry levels of the chosen
      universe (both charts draw the same levels).
    - Net GEX panels: one row per panel (first, next, highest, second
      highest |net GEX|) with the expiry, its levels and GEX Expiring %
      (``DEFAULT_GEX_EXPIRING_CANDIDATE``, as drawn).
    - IV*OI one expiry: that expiry's own levels.
    - All charts: every row above, with every expiry.

    Returns:
        Rows with scope, expiration, expiration_date, call_resistance,
        put_support, hvl, spot, gex_expiring_percent,
        gex_expiring_percent_applies (False where the column has no meaning)
        and hvl_note: ``HVL_CHAIN_EDGE_NOTE`` on the all-expiry row when its
        HVL sits at the chain edge (shown as drawn, but the monitor and the
        brief report "n/a"), else None. Display it after the HVL value.
    """
    replication = _primary_replication(output)
    settings = replication["settings"]
    spot = output["inputs"]["index_price"]
    rows: List[Dict[str, Any]] = []

    if chart in (CHART_IV_TIMES_OPEN_INTEREST, CHART_NET_DEX, CHART_ALL):
        # The chart value is kept (it mirrors MenthorQ's drawing); the note says the monitor
        # and the brief report "n/a" for an HVL at the chain edge (all_expiries_levels).
        row = _level_row(
            f"All expiries ({settings['expiry_universe']}, {len(settings['expirations'])})",
            None, select_drawn_levels(replication["iv_times_open_interest"]["levels"]), spot,
        )
        _, hvl_at_chain_edge = all_expiries_levels(replication)
        if hvl_at_chain_edge:
            row["hvl_note"] = HVL_CHAIN_EDGE_NOTE
        rows.append(row)
    if chart in (CHART_NET_GEX_PANELS, CHART_ALL):
        panels = replication["panels"][PANEL_RANKING_ABSOLUTE_NET]
        for panel_name in PANEL_NAMES:
            panel_expiration = panels.get(panel_name)
            if panel_expiration is None:
                rows.append(_level_row(PANEL_TITLES[panel_name], None, {}, spot, None, True))
                continue
            rows.append(_level_row(
                PANEL_TITLES[panel_name], panel_expiration,
                select_drawn_levels(replication["gex_by_expiration"][panel_expiration]["levels"]),
                spot,
                replication["gex_expiring_percent"][panel_expiration][DEFAULT_GEX_EXPIRING_CANDIDATE],
                True,
            ))
    if chart in (CHART_IV_TIMES_OPEN_INTEREST_EXPIRY, CHART_ALL):
        for entry in output["per_expiry_iv_times_open_interest"]:
            if chart == CHART_ALL or entry["expiration"] == expiration:
                rows.append(_level_row("This expiry only", entry["expiration"],
                                       entry["levels"], spot))
    return rows


def info_line(output: Dict[str, Any]) -> str:
    """
    Which data the charts were built from: mode and instant, snapshot
    capture time, index price and the time of the trade it comes from,
    open-interest snapshot time, IV source, universe and gamma underlying.
    """
    inputs = output["inputs"]
    settings = _primary_replication(output)["settings"]
    mode = RUN_MODE_DESCRIPTIONS.get(output.get("run_mode", "as_of"), output.get("run_mode"))
    return " | ".join([
        f"{output['currency']} {mode}, as of {_format_utc(output['as_of_utc'])}",
        f"snapshot captured {_format_utc(inputs['snapshot_captured_at_utc'])}",
        f"index {inputs['index_price']:,.2f} from trade at {_format_utc(inputs['index_price_time_utc'])}",
        f"open interest from snapshot {_format_utc(inputs['open_interest_captured_at_utc'])}",
        f"IV source: {iv_source_text(inputs['iv_source_counts'])}",
        f"universe {settings['expiry_universe']}, gamma at {settings['gamma_underlying']}",
    ])


def data_warnings(
    output: Dict[str, Any], reference: Optional[Dict[str, Any]] = None
) -> List[str]:
    """
    Plain-language warnings about the inputs: IV rebuilt or missing, expiries
    whose GEX was not computed, rows without open interest, a currency the
    formulas were not fitted on, and a reference JSON for another date.
    Empty when there is nothing to warn about.
    """
    warnings: List[str] = []
    inputs = output["inputs"]
    counts = inputs["iv_source_counts"]
    total = sum(counts.values())
    rebuilt = counts.get(IV_SOURCE_REBUILT_FROM_MARK_PRICE, 0)
    missing = counts.get(IV_SOURCE_MISSING, 0)
    if rebuilt:
        warnings.append(f"IV rebuilt from mark_price (Black-76) on {rebuilt:,} of {total:,} rows: "
                        "the snapshot has no mark_iv there")
    if missing:
        warnings.append(f"{missing:,} rows have no IV at all: their gamma is not computed")

    # The per-expiry charts come from the "all" universe, which contains
    # every expiry of the chosen universe, so it is the one checked.
    gamma_underlying = _primary_replication(output)["settings"]["gamma_underlying"]
    all_expiries = output["configurations"][
        f"{EXPIRY_UNIVERSE_ALL}__gamma_at_{gamma_underlying}"]["replication"]
    not_computed = [expiration for expiration, exposure in all_expiries["gex_by_expiration"].items()
                    if exposure["total_net_gex"] is None]
    if not_computed:
        noun = "expiry" if len(not_computed) == 1 else "expiries"
        warnings.append(f"GEX not computed for {len(not_computed)} {noun} "
                        f"({', '.join(not_computed)}): no leg has both gamma and open interest; "
                        "their levels show as not computed")

    without_open_interest = {entry["expiration"]: entry["rows_without_open_interest"]
                             for entry in output["per_expiry_iv_times_open_interest"]
                             if entry["rows_without_open_interest"]}
    if without_open_interest:
        details = ", ".join(f"{expiration} {count}" for expiration, count in without_open_interest.items())
        warnings.append(f"{sum(without_open_interest.values()):,} rows have no open interest in the "
                        f"open-interest snapshot and are left out ({details})")

    if output["currency"] != "BTC":
        warnings.append(f"{output['currency']}: the formulas were fitted on MenthorQ's BTC charts "
                        "only and are not validated for this currency")

    if reference is not None and reference.get("timestamp_utc"):
        reference_date = datetime.fromisoformat(
            str(reference["timestamp_utc"]).replace("Z", "+00:00")).date()
        chart_date = datetime.fromisoformat(output["as_of_utc"]).date()
        if reference_date != chart_date:
            warnings.append(f"The reference JSON is for {reference_date.isoformat()}, "
                            f"the charts are for {chart_date.isoformat()}")
    return warnings


def _difference(ours: Optional[float], theirs: Optional[float]) -> Optional[float]:
    """ours - theirs, or None when either is missing."""
    if ours is None or theirs is None:
        return None
    return ours - theirs


def comparison_rows(
    output: Dict[str, Any], reference: Optional[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Our drawn values next to MenthorQ's (the reference JSON read off its
    charts): spot, the all-expiry levels and, per reference panel, the
    panel's expiry, its levels and GEX Expiring %. Levels use the drawn
    candidates (``DEFAULT_LEVEL_CANDIDATES``), evaluated at the expiry whose
    settlement date equals the reference panel's date.

    Returns:
        Rows {"item", "ours", "reference", "difference"}; ``difference`` is
        ours - reference for numbers, "same" / "different" for expiry dates
        and None when either side is missing. Empty without a reference.
    """
    if reference is None:
        return []
    comparison = output["configurations"][output["primary_configuration"]]["comparison"] or {}
    rows: List[Dict[str, Any]] = []

    if reference.get("spot_price") is not None:
        ours = output["inputs"]["index_price"]
        rows.append({"item": "Spot", "ours": ours, "reference": reference["spot_price"],
                     "difference": _difference(ours, reference["spot_price"])})

    for level_name, entry in comparison.get("iv_times_open_interest", {}).get("levels", {}).items():
        ours = entry["candidates"].get(DEFAULT_LEVEL_CANDIDATES.get(level_name), {}).get("ours")
        rows.append({"item": f"All expiries: {LEVEL_LABELS.get(level_name, level_name)}",
                     "ours": ours, "reference": entry["reference"],
                     "difference": _difference(ours, entry["reference"])})

    for panel_name, panel_reference in reference.get("gex_panels", {}).items():
        entry = comparison.get("gex_panels", {}).get(panel_name, {})
        title = f"{PANEL_TITLES.get(panel_name, panel_name)} ({panel_reference['expiration_date']})"
        our_date = _expiration_date(entry.get("our_panel_choice", {}).get(PANEL_RANKING_ABSOLUTE_NET))
        rows.append({
            "item": f"{title}: expiry", "ours": our_date,
            "reference": panel_reference["expiration_date"],
            "difference": (None if our_date is None
                           else "same" if our_date == panel_reference["expiration_date"]
                           else "different"),
        })
        for level_name in ("call_resistance", "put_support", "hvl"):
            candidates = entry.get(level_name, {}).get("candidates", {})
            ours = candidates.get(DEFAULT_LEVEL_CANDIDATES[level_name], {}).get("ours")
            theirs = panel_reference.get(level_name)
            rows.append({"item": f"{title}: {LEVEL_LABELS[level_name]}", "ours": ours,
                         "reference": theirs, "difference": _difference(ours, theirs)})
        ours = entry.get("gex_expiring_percent", {}).get("candidates", {}).get(
            DEFAULT_GEX_EXPIRING_CANDIDATE)
        theirs = panel_reference.get("gex_expiring_percent")
        rows.append({"item": f"{title}: GEX Expiring %", "ours": ours, "reference": theirs,
                     "difference": _difference(ours, theirs)})
    return rows
