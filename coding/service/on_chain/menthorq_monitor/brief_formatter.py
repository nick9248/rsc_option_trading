"""
Telegram messages of the MenthorQ-style monitor: the brief (weekly or on
demand) and the urgent alert.

Format: Telegram HTML (``parse_mode="HTML"``). Only <b>, <i> and <pre> are
used; every dynamic text is escaped with ``html.escape`` (&, <, >), which is
all Telegram's HTML mode requires. ASCII only.

Length: Telegram rejects messages over 4096 characters (counted after the
tags are parsed, in UTF-16 code units). The raw HTML length is counted here,
which is never smaller, so a message that passes ``telegram_length`` <=
``TELEGRAM_MESSAGE_LIMIT`` is accepted. A brief that would be longer is
re-rendered shorter step by step (panels dropped, lists cut, comparison
blocks dropped) and says what it left out.
"""

import html
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Sequence

from coding.core.analytics.menthorq_monitor.alert_rules import UrgentAlert
from coding.core.analytics.menthorq_monitor.level_record import (
    GAMMA_REGIME_AT_HVL,
    GAMMA_REGIME_NEGATIVE,
    GAMMA_REGIME_POSITIVE,
    LEVEL_LABELS,
    MODE_LIVE,
    MenthorqLevelRecord,
    rebuilt_iv_count,
)
from coding.core.analytics.menthorq_monitor.level_trend_calculator import (
    COMPARISON_ONE_DAY,
    COMPARISON_PREVIOUS,
    COMPARISON_SEVEN_DAYS,
    CROSSED_ABOVE_HVL,
    CROSSED_BELOW_HVL,
    RecordComparison,
    spot_distances,
)
from coding.core.analytics.menthorq_replication_calculator import PANEL_NAMES

TELEGRAM_MESSAGE_LIMIT = 4096
MAXIMUM_LIST_ITEM_CHARACTERS = 300
MAXIMUM_EXPIRIES_SHOWN = 12
STALE_SNAPSHOT_AGE = timedelta(minutes=45)
STALE_SPOT_AGE = timedelta(minutes=15)
NOT_AVAILABLE = "n/a"

COMPARISON_TITLES = {
    COMPARISON_PREVIOUS: "vs previous run",
    COMPARISON_ONE_DAY: "vs 24h ago",
    COMPARISON_SEVEN_DAYS: "vs 7d ago",
}
PANEL_TITLES = {
    "first_expiration": "first",
    "next_expiration": "next",
    "highest_gex": "highest GEX",
    "second_highest_gex": "2nd GEX",
}
LEVEL_ORDER = ("call_resistance", "hvl", "put_support")
SHORT_LEVEL_LABELS = {"call_resistance": "CR", "put_support": "PS", "hvl": "HVL"}


def escape(text: str) -> str:
    """Escape text for Telegram HTML (&, <, >)."""
    return html.escape(text, quote=False)


def telegram_length(text: str) -> int:
    """Length as Telegram counts it (UTF-16 code units)."""
    return len(text.encode("utf-16-le")) // 2


def format_strike(value: Optional[float]) -> str:
    """80000.0 -> "80,000"; None -> "n/a"."""
    return NOT_AVAILABLE if value is None else f"{value:,.0f}"


def format_signed_usd(value: Optional[float]) -> str:
    """5000.0 -> "+5,000"; 0 -> "0"; None -> "n/a"."""
    if value is None:
        return NOT_AVAILABLE
    if round(value) == 0:
        return "0"
    return f"{value:+,.0f}"


def format_signed_percent(value: Optional[float]) -> str:
    """1.234 -> "+1.23%"; None -> "n/a"."""
    return NOT_AVAILABLE if value is None else f"{value:+.2f}%"


def format_gex(value: Optional[float]) -> str:
    """8670788.5 -> "+$8.67M" (USD per 1% move); None -> "n/a"."""
    if value is None:
        return NOT_AVAILABLE
    sign = "+" if value >= 0 else "-"
    magnitude = abs(value)
    if magnitude >= 1e9:
        return f"{sign}${magnitude / 1e9:.2f}B"
    if magnitude >= 1e6:
        return f"{sign}${magnitude / 1e6:.2f}M"
    return f"{sign}${magnitude / 1e3:.0f}K"


def format_utc(instant: Optional[datetime], with_date: bool = True) -> str:
    """UTC display, e.g. "2026-11-07 09:59 UTC"; None -> "n/a"."""
    if instant is None:
        return NOT_AVAILABLE
    utc = instant.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%d %H:%M UTC" if with_date else "%H:%M:%S UTC")


def _cap(text: str) -> str:
    if len(text) <= MAXIMUM_LIST_ITEM_CHARACTERS:
        return text
    return text[:MAXIMUM_LIST_ITEM_CHARACTERS - 3] + "..."


def bullet_lines(items: Sequence[str], maximum: Optional[int]) -> str:
    """Escaped "- item" lines; beyond ``maximum`` a count of the rest."""
    shown = list(items) if maximum is None else list(items)[:maximum]
    lines = [f"- {escape(_cap(item))}" for item in shown]
    if len(items) > len(shown):
        lines.append(f"- ... and {len(items) - len(shown)} more")
    return "\n".join(lines)


def regime_sentence(record: MenthorqLevelRecord) -> str:
    """Plain-text gamma regime with the spot-to-HVL gap, e.g. "POSITIVE gamma: spot ... above HVL."."""
    distance = spot_distances(record)["hvl"]
    if record.gamma_regime is None or distance.distance_usd is None:
        return "Gamma regime not computed (spot or HVL missing)."
    gap = f"{format_strike(abs(distance.distance_usd))} ({abs(distance.distance_percent_of_spot or 0.0):.2f}%)"
    if record.gamma_regime == GAMMA_REGIME_POSITIVE:
        return f"POSITIVE gamma: spot {gap} above HVL."
    if record.gamma_regime == GAMMA_REGIME_NEGATIVE:
        return f"NEGATIVE gamma: spot {gap} below HVL."
    if record.gamma_regime == GAMMA_REGIME_AT_HVL:
        return "Spot exactly at HVL."
    return f"Gamma regime: {record.gamma_regime}."


@dataclass(frozen=True)
class _RenderOptions:
    """What a brief rendering includes (reduced step by step to fit the limit)."""

    include_panels: bool = True
    maximum_events: Optional[int] = None
    maximum_caveats: Optional[int] = None
    comparison_labels: tuple = (COMPARISON_PREVIOUS, COMPARISON_ONE_DAY, COMPARISON_SEVEN_DAYS)


def _header(record: MenthorqLevelRecord, title: str) -> str:
    mode_text = ("live: newest snapshot and its own open interest" if record.mode == MODE_LIVE
                 else "daily close: 23:59 UTC, open interest from 00:00 UTC")
    expiries = record.expirations[:MAXIMUM_EXPIRIES_SHOWN]
    expiries_text = ", ".join(expiries) if expiries else "none"
    if len(record.expirations) > len(expiries):
        expiries_text += f" (+{len(record.expirations) - len(expiries)} more)"
    lines = [
        f"<b>{escape(record.currency)} MenthorQ-style levels | {escape(title)}</b>",
        f"Data {escape(format_utc(record.as_of_utc))} ({escape(mode_text)})",
        f"Spot {escape(format_strike(record.spot))} (Deribit index, trade at "
        f"{escape(format_utc(record.spot_time_utc, with_date=False))})",
        f"Snapshot {escape(format_utc(record.snapshot_captured_at_utc))}, open interest "
        f"{escape(format_utc(record.open_interest_captured_at_utc))}",
        f"Universe {escape(record.expiry_universe)}, gamma at {escape(record.gamma_underlying)}; "
        f"expiries: {escape(expiries_text)}",
    ]
    return "\n".join(lines)


def _levels_table(record: MenthorqLevelRecord, daily_close: Optional[MenthorqLevelRecord]) -> str:
    distances = spot_distances(record)
    close_title = ("Close " + daily_close.as_of_utc.astimezone(timezone.utc).strftime("%m-%d")
                   if daily_close is not None else "")
    rows = [f"{'Level':<16}{'Now':>8}{'vs spot':>9}{close_title:>12}"]
    for level_name in LEVEL_ORDER:
        close_value = format_strike(daily_close.level(level_name)) if daily_close is not None else ""
        rows.append(
            f"{LEVEL_LABELS[level_name]:<16}{format_strike(record.level(level_name)):>8}"
            f"{format_signed_percent(distances[level_name].distance_percent_of_spot):>9}"
            f"{close_value:>12}"
        )
    note = ("" if daily_close is None else
            "\n<i>Close = MenthorQ-style daily record (spot 23:59 UTC, OI 00:00 UTC).</i>")
    return "<b>Key levels</b>\n<pre>" + escape("\n".join(rows)) + "</pre>" + note


def regime_versus_net_gex_note(record: MenthorqLevelRecord) -> Optional[str]:
    """
    One plain-text line when the gamma regime (spot vs HVL) and the sign of
    total net GEX disagree, e.g. spot below HVL (negative-gamma regime)
    while total net GEX is positive; None when they agree or a value is
    missing.
    """
    if record.total_net_gex is None or record.total_net_gex == 0:
        return None
    gex_sign = "positive" if record.total_net_gex > 0 else "negative"
    if record.gamma_regime == GAMMA_REGIME_NEGATIVE and gex_sign == "positive":
        regime_text = "spot below HVL = negative-gamma regime"
    elif record.gamma_regime == GAMMA_REGIME_POSITIVE and gex_sign == "negative":
        regime_text = "spot above HVL = positive-gamma regime"
    else:
        return None
    return (f"Not a contradiction: {regime_text}, while total net GEX (sum over all strikes) "
            f"is {gex_sign}; these are different measures.")


def _regime_section(record: MenthorqLevelRecord) -> str:
    text = (f"<b>Gamma regime</b>\n{escape(regime_sentence(record))}\n"
            f"Total net GEX {escape(format_gex(record.total_net_gex))} per 1% move.")
    note = regime_versus_net_gex_note(record)
    if note is not None:
        text += f"\n<i>{escape(note)}</i>"
    return text


def comparison_block(comparison: RecordComparison) -> str:
    """One comparison (spot, levels, net GEX, settled expiries) as a <pre> block."""
    title = COMPARISON_TITLES.get(comparison.label, comparison.label)
    reference = comparison.reference
    if reference is None:
        return f"{escape(title)}: no stored record"
    lines = [f"{title} ({format_utc(reference.as_of_utc)})"]
    spot_text = format_signed_usd(comparison.spot_change_usd)
    if comparison.spot_change_percent is not None:
        spot_text += f" ({format_signed_percent(comparison.spot_change_percent)})"
    lines.append(f"  {'Spot':<8}{spot_text}")
    for level_name in LEVEL_ORDER:
        change = comparison.level_changes[level_name]
        text = format_signed_usd(change.change_usd)
        if change.change_usd is not None and round(change.change_usd) != 0:
            text += f" ({format_signed_percent(change.change_percent_of_spot)} of spot)"
        lines.append(f"  {SHORT_LEVEL_LABELS[level_name]:<8}{text}")
    gex_text = format_gex(comparison.net_gex_change)
    if comparison.net_gex_sign_flipped:
        gex_text += " SIGN FLIP"
    lines.append(f"  {'Net GEX':<8}{gex_text}")
    if comparison.settled_expiries:
        lines.append(f"  Settled: {', '.join(comparison.settled_expiries)}")
    return "<pre>" + escape("\n".join(lines)) + "</pre>"


def _changes_section(comparisons: Sequence[RecordComparison], labels: tuple) -> str:
    blocks = [comparison_block(comparison) for comparison in comparisons
              if comparison.label in labels]
    return "<b>Changes</b> (USD; level % = % of current spot)\n" + "\n".join(blocks)


def _panels_section(record: MenthorqLevelRecord) -> str:
    rows = []
    for panel_name in PANEL_NAMES:
        panel = record.panel_levels.get(panel_name) or {}
        expiration = panel.get("expiration")
        if expiration is None:
            rows.append(f"{PANEL_TITLES[panel_name]:<12}{NOT_AVAILABLE}")
            continue
        rows.append(f"{PANEL_TITLES[panel_name]:<12}{expiration} GEX {format_gex(panel.get('total_net_gex'))}")
        rows.append(f"  CR {format_strike(panel.get('call_resistance'))}"
                    f"  PS {format_strike(panel.get('put_support'))}"
                    f"  HVL {format_strike(panel.get('hvl'))}")
    return "<b>Panels</b> (per expiry)\n<pre>" + escape("\n".join(rows)) + "</pre>"


def notable_events(
    record: MenthorqLevelRecord, comparisons: Sequence[RecordComparison]
) -> List[str]:
    """Plain-text events: crossings, sign flips, expiry roll-offs, level moves."""
    events: List[str] = []
    for comparison in comparisons:
        if comparison.reference is None:
            continue
        title = COMPARISON_TITLES.get(comparison.label, comparison.label)
        if comparison.hvl_crossing == CROSSED_BELOW_HVL:
            events.append(f"Spot crossed BELOW HVL {title} (now negative gamma).")
        elif comparison.hvl_crossing == CROSSED_ABOVE_HVL:
            events.append(f"Spot crossed ABOVE HVL {title} (now positive gamma).")
        if comparison.net_gex_sign_flipped:
            events.append(f"Total net GEX changed sign {title} "
                          f"({format_gex(comparison.reference.total_net_gex)} -> "
                          f"{format_gex(record.total_net_gex)}).")
        moved = [LEVEL_LABELS[name] for name, change in comparison.level_changes.items()
                 if change.change_usd is not None and round(change.change_usd) != 0]
        if comparison.settled_expiries:
            text = f"{', '.join(comparison.settled_expiries)} settled since the {comparison.label} record"
            if moved:
                text += (f"; the moves of {', '.join(moved)} coincide with this roll-off "
                         "(its open interest left the sum)")
            events.append(text + ".")
        if comparison.new_expiries:
            events.append(f"New in the universe {title}: {', '.join(comparison.new_expiries)}.")
    return events


def data_caveats(record: MenthorqLevelRecord, comparisons: Sequence[RecordComparison]) -> List[str]:
    """Plain-text caveats about the inputs of ``record``."""
    caveats: List[str] = []
    if record.mode == MODE_LIVE:
        caveats.append("Live mode: open interest from the newest snapshot. MenthorQ's open "
                       "interest is about one day older, so its levels can differ.")
    caveats.append("Expiry set not pinned down vs MenthorQ (no single universe matched all "
                   "checked dates).")
    if record.snapshot_captured_at_utc is not None and \
            record.as_of_utc - record.snapshot_captured_at_utc > STALE_SNAPSHOT_AGE:
        minutes = (record.as_of_utc - record.snapshot_captured_at_utc).total_seconds() / 60
        caveats.append(f"Snapshot is {minutes:.0f} min older than the data time.")
    if record.spot_time_utc is not None and record.as_of_utc - record.spot_time_utc > STALE_SPOT_AGE:
        minutes = (record.as_of_utc - record.spot_time_utc).total_seconds() / 60
        caveats.append(f"Spot comes from a trade {minutes:.0f} min before the data time.")
    rebuilt = rebuilt_iv_count(record)
    if rebuilt:
        caveats.append(f"IV rebuilt from mark price on {rebuilt} rows (mark_iv missing).")
    caveats.extend(record.not_computed_reasons)
    for comparison in comparisons:
        if comparison.reference is None:
            caveats.append(f"No stored record for the {comparison.label} comparison.")
    return caveats


def _render_brief(
    record: MenthorqLevelRecord,
    comparisons: Sequence[RecordComparison],
    daily_close: Optional[MenthorqLevelRecord],
    title: str,
    options: _RenderOptions,
    omitted: Sequence[str],
) -> str:
    sections = [
        _header(record, title),
        _levels_table(record, daily_close),
        _regime_section(record),
        _changes_section(comparisons, options.comparison_labels),
    ]
    if options.include_panels:
        sections.append(_panels_section(record))
    events = notable_events(record, comparisons)
    sections.append("<b>Notable events</b>\n" + (bullet_lines(events, options.maximum_events)
                                                  if events else "- none"))
    sections.append("<b>Data caveats</b>\n" + bullet_lines(data_caveats(record, comparisons),
                                                       options.maximum_caveats))
    if omitted:
        sections.append(f"<i>Shortened to fit Telegram: {escape(', '.join(omitted))}.</i>")
    return "\n\n".join(sections)


# Each step makes the brief shorter; the list is applied cumulatively.
_SHORTENING_STEPS = (
    ("panels omitted", lambda options: replace(options, include_panels=False)),
    ("events cut", lambda options: replace(options, maximum_events=3)),
    ("caveats cut", lambda options: replace(options, maximum_caveats=3)),
    ("7d block omitted", lambda options: replace(
        options, comparison_labels=(COMPARISON_PREVIOUS, COMPARISON_ONE_DAY))),
    ("previous-run block omitted", lambda options: replace(
        options, comparison_labels=(COMPARISON_ONE_DAY,))),
)


def format_brief(
    record: MenthorqLevelRecord,
    comparisons: Sequence[RecordComparison],
    daily_close: Optional[MenthorqLevelRecord] = None,
    title: str = "Brief",
    limit: int = TELEGRAM_MESSAGE_LIMIT,
) -> str:
    """
    The brief: header, key levels, gamma regime, changes vs previous / 24h /
    7d, panels, notable events, data caveats.

    Args:
        record: The latest (live) record.
        comparisons: ``MenthorqMonitorService.build_comparisons(record)``.
        daily_close: Latest daily-close record, shown as an extra column.
        title: e.g. "Weekly brief" or "On-demand brief".
        limit: Maximum ``telegram_length``.

    Returns:
        Telegram HTML no longer than ``limit``.
    """
    options = _RenderOptions()
    omitted: List[str] = []
    text = _render_brief(record, comparisons, daily_close, title, options, omitted)
    for description, step in _SHORTENING_STEPS:
        if telegram_length(text) <= limit:
            return text
        options = step(options)
        omitted.append(description)
        text = _render_brief(record, comparisons, daily_close, title, options, omitted)
    if telegram_length(text) <= limit:
        return text
    # Not reachable with the item caps above; kept so an oversize text is never sent.
    return (f"<b>{escape(record.currency)} MenthorQ-style brief</b>\n"
            f"Too long to send ({telegram_length(text)} characters); see stored record "
            f"{escape(str(record.record_id))}.")


def format_urgent_alert(
    record: MenthorqLevelRecord,
    alerts: Sequence[UrgentAlert],
    limit: int = TELEGRAM_MESSAGE_LIMIT,
) -> str:
    """
    The URGENT message for the alerts that passed the cooldown: one line per
    alert (with the expiry roll-off label when an expiry settled over the
    compared window), the current levels and the caveats.
    """
    lines = [
        f"<b>URGENT | {escape(record.currency)} MenthorQ-style levels</b>",
        f"{escape(format_utc(record.as_of_utc))} | spot {escape(format_strike(record.spot))}",
        "",
    ]
    alert_lines = []
    for alert in alerts:
        text = alert.title
        if alert.change_usd is not None and alert.level_name is not None:
            text += (f": {format_signed_usd(alert.change_usd)} "
                     f"({format_signed_percent(alert.change_percent_of_spot)} of spot)")
        elif alert.change_usd is not None:
            text += f": net GEX change {format_gex(alert.change_usd)}"
        if alert.expiry_roll:
            text += (f". Coincides with the settlement of {', '.join(alert.expiry_roll)} "
                     "(expiry roll-off)")
        alert_lines.append(text + ".")
    lines.append(bullet_lines(alert_lines, None))
    lines.append("")
    lines.append(escape(
        f"Now: CR {format_strike(record.call_resistance)} | HVL {format_strike(record.hvl)} | "
        f"PS {format_strike(record.put_support)} | net GEX {format_gex(record.total_net_gex)} | "
        f"regime {record.gamma_regime or NOT_AVAILABLE}"))
    lines.append("<i>Live mode (newest open interest). Thresholds are chosen by hand until "
                 "a backtest sets them. /mute 24h silences these alerts.</i>")
    text = "\n".join(lines)
    if telegram_length(text) <= limit:
        return text
    return "\n".join(lines[:3] + [bullet_lines(alert_lines, 5)] + lines[4:])
