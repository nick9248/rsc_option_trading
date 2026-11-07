"""
Telegram replies of the monitor's bot commands: /levels, /changes,
/status, /mute, /help and the hint for an unknown command. (/brief is the
brief of brief_formatter.)

Same format rules as the brief: Telegram HTML with only <b>, <i> and <pre>,
every dynamic text escaped, ASCII only, at most ``TELEGRAM_MESSAGE_LIMIT``
UTF-16 code units (raw HTML length, never smaller than what Telegram counts).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Sequence, Tuple

from coding.core.analytics.menthorq_monitor.alert_mute import MenthorqMonitorState, is_muted
from coding.core.analytics.menthorq_monitor.alert_rules import AlertThresholds
from coding.core.analytics.menthorq_monitor.level_record import (
    LEVEL_LABELS,
    MODE_LIVE,
    MenthorqLevelRecord,
)
from coding.core.analytics.menthorq_monitor.level_trend_calculator import (
    RecordComparison,
    spot_distances,
)
from coding.service.on_chain.menthorq_monitor.brief_formatter import (
    LEVEL_ORDER,
    NOT_AVAILABLE,
    STALE_SNAPSHOT_AGE,
    TELEGRAM_MESSAGE_LIMIT,
    bullet_lines,
    comparison_block,
    escape,
    format_gex,
    format_signed_percent,
    format_signed_usd,
    format_strike,
    format_utc,
    notable_events,
    regime_sentence,
    regime_versus_net_gex_note,
    telegram_length,
)

# (command without slash, description) in menu order; also sent to setMyCommands.
BOT_COMMANDS: Tuple[Tuple[str, str], ...] = (
    ("brief", "Full brief: levels, regime, changes, caveats"),
    ("levels", "Key levels and distance to spot"),
    ("changes", "Changes vs previous run, 24h and 7d"),
    ("status", "Last runs, data age, health, alert state"),
    ("mute", "Silence URGENT alerts: /mute 24h, /mute 3d, /mute off"),
    ("help", "List the commands and alert rules"),
)
HOURLY_JOB_EXPECTED_WITHIN = timedelta(hours=2)
MUTE_USAGE = "Usage: /mute 24h | /mute 3d | /mute 90m | /mute off (longest 30d)."


def format_age(age: timedelta) -> str:
    """timedelta(minutes=55) -> "55 min"; 3 h -> "3.0 h"; 3 days -> "3.0 d"."""
    minutes = age.total_seconds() / 60
    if minutes < 120:
        return f"{minutes:.0f} min"
    if minutes < 48 * 60:
        return f"{minutes / 60:.1f} h"
    return f"{minutes / (24 * 60):.1f} d"


def _title(currency: str, text: str, record: Optional[MenthorqLevelRecord]) -> str:
    title = f"<b>{escape(currency)} {escape(text)}</b>"
    if record is not None:
        mode_text = "live" if record.mode == MODE_LIVE else "daily close"
        title += f" | {escape(mode_text)} {escape(format_utc(record.as_of_utc))}"
    return title


def _no_record(currency: str, text: str) -> str:
    return f"{_title(currency, text, None)}\nNo live record is stored yet."


def format_levels_reply(currency: str, record: Optional[MenthorqLevelRecord]) -> str:
    """
    /levels: Call Resistance, HVL and Put Support of the latest stored live
    record with their signed distance to spot (level - spot, USD and % of
    spot), spot, the regime sentence and total net GEX.
    """
    if record is None:
        return _no_record(currency, "key levels")
    distances = spot_distances(record)
    rows = [f"{'Level':<16}{'Strike':>9}{'vs spot':>9}{'USD':>9}"]
    for level_name in LEVEL_ORDER:
        distance = distances[level_name]
        rows.append(f"{LEVEL_LABELS[level_name]:<16}{format_strike(distance.level_value):>9}"
                    f"{format_signed_percent(distance.distance_percent_of_spot):>9}"
                    f"{format_signed_usd(distance.distance_usd):>9}")
    rows.append(f"{'Spot':<16}{format_strike(record.spot):>9}")
    lines = [
        _title(currency, "key levels", record),
        "<pre>" + escape("\n".join(rows)) + "</pre>",
        escape(regime_sentence(record)),
        f"Total net GEX {escape(format_gex(record.total_net_gex))} per 1% move.",
    ]
    note = regime_versus_net_gex_note(record)
    if note is not None:
        lines.append(f"<i>{escape(note)}</i>")
    return "\n".join(lines)


def format_changes_reply(
    currency: str,
    record: Optional[MenthorqLevelRecord],
    comparisons: Sequence[RecordComparison],
    limit: int = TELEGRAM_MESSAGE_LIMIT,
) -> str:
    """
    /changes: the latest stored live record vs the previous run, 24 h and
    7 d ago (one block each, settled expiries listed in the block), then
    the events, which label a level move that coincides with an expiry
    roll-off.
    """
    if record is None:
        return _no_record(currency, "changes")
    events = notable_events(record, comparisons)

    def render(maximum_events: Optional[int]) -> str:
        return "\n".join([
            _title(currency, "changes", record),
            "<i>USD; level % = % of current spot.</i>",
            *[comparison_block(comparison) for comparison in comparisons],
            "<b>Events and expiry roll-offs</b>",
            bullet_lines(events, maximum_events) if events else "- none",
        ])

    # Too long: cut the events to 3, then to none (the list still says how many were left out).
    for maximum_events in (None, 3, 0):
        text = render(maximum_events)
        if telegram_length(text) <= limit:
            return text
    return text


@dataclass(frozen=True)
class MonitorStatus:
    """Everything /status shows, gathered by the command service."""

    currency: str
    now: datetime
    latest_live_run: Optional[MenthorqLevelRecord]
    latest_daily_close_run: Optional[MenthorqLevelRecord]
    alerts_enabled: bool
    monitor_state: Optional[MenthorqMonitorState]
    monitor_state_error: Optional[str]
    last_alert_delivered: Optional[Tuple[str, datetime]]


def monitor_health_problems(status: MonitorStatus) -> List[str]:
    """Plain-text problems of the monitor job; empty when it looks healthy."""
    live = status.latest_live_run
    if live is None:
        return ["no live run stored"]
    problems = []
    since_run = status.now - live.computed_at_utc
    if since_run > HOURLY_JOB_EXPECTED_WITHIN:
        problems.append(f"hourly job has not run for {format_age(since_run)}")
    if live.spot is None:
        reason = live.not_computed_reasons[0] if live.not_computed_reasons else "reason not stored"
        problems.append(f"latest live run not computed: {reason}")
    elif live.snapshot_captured_at_utc is not None and \
            live.as_of_utc - live.snapshot_captured_at_utc > STALE_SNAPSHOT_AGE:
        problems.append(f"snapshot {format_age(live.as_of_utc - live.snapshot_captured_at_utc)} "
                        "older than the latest run")
    return problems


def _run_line(label: str, run: Optional[MenthorqLevelRecord], now: datetime) -> str:
    if run is None:
        return f"{label}: none stored"
    computed = "computed" if run.spot is not None else "NOT computed"
    return (f"{label}: {format_utc(run.as_of_utc)} ({format_age(now - run.as_of_utc)} ago), "
            f"{computed}")


def _alert_state_lines(status: MonitorStatus) -> List[str]:
    if not status.alerts_enabled:
        lines = ["Urgent alerts: OFF (MENTHORQ_ALERTS_ENABLED=0)"]
    elif status.monitor_state_error is not None:
        lines = [f"Urgent alerts: on; mute state not readable ({status.monitor_state_error}), "
                 "treated as not muted"]
    elif is_muted(status.monitor_state, status.now) and status.monitor_state is not None \
            and status.monitor_state.alerts_muted_until is not None:
        muted_until = status.monitor_state.alerts_muted_until
        lines = [f"Urgent alerts: MUTED until {format_utc(muted_until)} "
                 f"({format_age(muted_until - status.now)} left)"]
    else:
        lines = ["Urgent alerts: on, not muted"]
    if status.last_alert_delivered is None:
        lines.append("Last alert delivered: none")
    else:
        key, delivered_at = status.last_alert_delivered
        lines.append(f"Last alert delivered: {key} at {format_utc(delivered_at)}")
    state = status.monitor_state
    if state is not None and state.last_suppressed_at is not None:
        lines.append(f"Last alert held back by the mute: "
                     f"{', '.join(state.last_suppressed_alert_keys) or NOT_AVAILABLE} at "
                     f"{format_utc(state.last_suppressed_at)}")
    return lines


def format_status_reply(status: MonitorStatus) -> str:
    """
    /status: health of the monitor job, last run per mode, data age and
    capture instants, whether the hourly job ran in the last 2 h, the alert
    state (muted or not) and the last delivered alert.
    """
    now = status.now
    live = status.latest_live_run
    problems = monitor_health_problems(status)
    lines = [
        f"<b>{escape(status.currency)} MenthorQ monitor status</b> | {escape(format_utc(now))}",
        "Health: OK" if not problems else f"Health: PROBLEM: {escape('; '.join(problems))}",
        escape(_run_line("Live run", live, now)),
        escape(_run_line("Daily close", status.latest_daily_close_run, now)),
    ]
    if live is not None and live.snapshot_captured_at_utc is not None:
        lines.append(escape(
            f"Data age: snapshot {format_utc(live.snapshot_captured_at_utc)} "
            f"({format_age(now - live.snapshot_captured_at_utc)} old), open interest "
            f"{format_utc(live.open_interest_captured_at_utc)}"))
    else:
        lines.append("Data age: n/a (no computed live run)")
    ran_recently = live is not None and now - live.computed_at_utc <= HOURLY_JOB_EXPECTED_WITHIN
    lines.append(f"Hourly job ran in the last 2h: {'yes' if ran_recently else 'NO'}")
    lines.extend(escape(line) for line in _alert_state_lines(status))
    return "\n".join(lines)


def format_mute_set_reply(currency: str, muted_until: datetime, duration_text: str) -> str:
    """/mute <duration> saved."""
    return (f"<b>{escape(currency)} URGENT alerts muted</b> until "
            f"{escape(format_utc(muted_until))} ({escape(duration_text)}).\n"
            "Command replies and the weekly brief are still sent. /mute off ends it early.")


def format_mute_cleared_reply(currency: str) -> str:
    """/mute off saved."""
    return f"<b>{escape(currency)} URGENT alerts unmuted.</b> They are sent again from the next run."


def format_mute_state_reply(
    currency: str, state: Optional[MenthorqMonitorState], now: datetime
) -> str:
    """/mute without an argument: the current mute and the usage."""
    if is_muted(state, now) and state is not None:
        current = f"URGENT alerts are muted until {format_utc(state.alerts_muted_until)}."
    else:
        current = "URGENT alerts are not muted."
    return f"<b>{escape(currency)} mute</b>\n{escape(current)}\n{escape(MUTE_USAGE)}"


def format_mute_error_reply(problem: str) -> str:
    """/mute with an argument that cannot be read, or a mute that could not be saved."""
    return f"<b>Mute not changed</b>: {escape(problem)}\n{escape(MUTE_USAGE)}"


def format_help_reply(currency: str, thresholds: AlertThresholds) -> str:
    """/help: the commands and which urgent alert rules are on."""
    lines = [f"<b>{escape(currency)} MenthorQ monitor commands</b>"]
    lines.extend(f"/{command} - {escape(description)}" for command, description in BOT_COMMANDS)
    lines.append("<b>Urgent alert rules</b>")
    lines.append(bullet_lines(thresholds.describe(), None))
    return "\n".join(lines)


def format_unknown_command_reply(command: str) -> str:
    """Short hint for a command the bot does not know."""
    known = " ".join(f"/{name}" for name, _ in BOT_COMMANDS)
    return f"Unknown command {escape(command)}. Commands: {known}. Send /help for details."


def format_command_failed_reply(command: str) -> str:
    """A command whose reply could not be built (the error is in the collector log)."""
    return f"{escape(command)} failed (see the collector log)."
