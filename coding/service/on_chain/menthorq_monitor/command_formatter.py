"""
Telegram replies of the monitor's bot commands: /levels, /changes,
/status, /mute, /help and the hint for an unknown command. (/brief is the
brief of brief_formatter.)

Same format rules as the brief: Telegram HTML with only <b>, <i> and the
brief's code block, every dynamic text escaped, the same "DATA STALE" line
under the title of /levels and /changes, ASCII only, at most ``TELEGRAM_MESSAGE_LIMIT``
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
    DEFAULT_STALE_SNAPSHOT_LIMIT,
    LEVEL_ORDER,
    NOT_AVAILABLE,
    TELEGRAM_MESSAGE_LIMIT,
    bullet_lines,
    code_block,
    comparison_block,
    escape,
    format_duration,
    format_gex,
    format_signed_percent,
    format_signed_usd,
    format_strike,
    format_utc,
    notable_events,
    regime_sentence,
    regime_versus_net_gex_note,
    stale_data_line,
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


def _title(currency: str, text: str, record: Optional[MenthorqLevelRecord]) -> str:
    title = f"<b>{escape(currency)} {escape(text)}</b>"
    if record is not None:
        mode_text = "live" if record.mode == MODE_LIVE else "daily close"
        title += f" | {escape(mode_text)} {escape(format_utc(record.as_of_utc))}"
    return title


def _no_record(currency: str, text: str) -> str:
    return f"{_title(currency, text, None)}\nNo live record is stored yet."


def _title_lines(
    currency: str, text: str, record: MenthorqLevelRecord, stale_limit: timedelta,
    now: Optional[datetime],
) -> List[str]:
    """The title, then the "DATA STALE" line when the record's data is stale."""
    lines = [_title(currency, text, record)]
    stale_line = stale_data_line(record, stale_limit, now)
    if stale_line is not None:
        lines.append(stale_line)
    return lines


def format_levels_reply(
    currency: str,
    record: Optional[MenthorqLevelRecord],
    stale_limit: timedelta = DEFAULT_STALE_SNAPSHOT_LIMIT,
    now: Optional[datetime] = None,
) -> str:
    """
    /levels: Call Resistance, HVL and Put Support of the latest stored live
    record with their signed distance to spot (level - spot, USD and % of
    spot), spot, the regime sentence and total net GEX. Starts with the
    "DATA STALE" line when the snapshot is more than ``stale_limit`` older
    than the record's data time or than ``now`` (the wall clock of the
    reply; see ``stale_data_warning``).
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
        *_title_lines(currency, "key levels", record, stale_limit, now),
        code_block("\n".join(rows)),
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
    stale_limit: timedelta = DEFAULT_STALE_SNAPSHOT_LIMIT,
    now: Optional[datetime] = None,
) -> str:
    """
    /changes: the latest stored live record vs the previous run, 24 h and
    7 d ago (one block each, settled expiries listed in the block), then
    the events, which label a level move that coincides with an expiry
    roll-off. Starts with the "DATA STALE" line when the record is stale
    (see ``format_levels_reply``).
    """
    if record is None:
        return _no_record(currency, "changes")
    events = notable_events(record, comparisons)

    def render(maximum_events: Optional[int]) -> str:
        return "\n".join([
            *_title_lines(currency, "changes", record, stale_limit, now),
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


def monitor_health_problems(
    status: MonitorStatus, stale_limit: timedelta = DEFAULT_STALE_SNAPSHOT_LIMIT, *,
    newest_snapshot_captured_at_utc: Optional[datetime],
    newest_snapshot_not_readable: bool = False,
) -> List[str]:
    """
    Plain-text problems of the monitor job; empty when it looks healthy.

    Args:
        status: What /status shows.
        stale_limit: ``MenthorqMonitorSettings.stale_snapshot_limit``, the
            limit the brief and the alerts use, so they never disagree; a
            snapshot more than this older than the latest live run, or a
            newest database snapshot more than this older than now, is a
            problem (exactly the limit is not).
        newest_snapshot_captured_at_utc: Capture time of the newest option
            chain snapshot in the database at or before now (required, so
            None only ever means none in the database, which is a problem).
        newest_snapshot_not_readable: True when the lookup of the newest
            snapshot failed (e.g. a database error); a problem of its own,
            and ``newest_snapshot_captured_at_utc`` is then ignored.
    """
    live = status.latest_live_run
    problems = []
    if live is None:
        problems.append("no live run stored")
    else:
        since_run = status.now - live.computed_at_utc
        if since_run > HOURLY_JOB_EXPECTED_WITHIN:
            problems.append(f"hourly job has not run for {format_duration(since_run)}")
        if not live.has_levels:
            reason = live.not_computed_reasons[0] if live.not_computed_reasons else "reason not stored"
            problems.append(f"latest live run not computed: {reason}")
        elif live.snapshot_captured_at_utc is not None and \
                live.as_of_utc - live.snapshot_captured_at_utc > stale_limit:
            problems.append(f"snapshot {format_duration(live.as_of_utc - live.snapshot_captured_at_utc)} "
                            "older than the latest run")
    if newest_snapshot_not_readable:
        problems.append("newest snapshot not readable")
    elif newest_snapshot_captured_at_utc is None:
        problems.append("no snapshot in the database")
    elif status.now - newest_snapshot_captured_at_utc > stale_limit:
        problems.append(f"newest snapshot is {format_duration(status.now - newest_snapshot_captured_at_utc)} "
                        f"old (limit {format_duration(stale_limit)})")
    return problems


def _run_line(label: str, run: Optional[MenthorqLevelRecord], now: datetime) -> str:
    if run is None:
        return f"{label}: none stored"
    computed = "computed" if run.has_levels else "NOT computed"
    return (f"{label}: {format_utc(run.as_of_utc)} ({format_duration(now - run.as_of_utc)} ago), "
            f"{computed}")


def _record_used_line(live: Optional[MenthorqLevelRecord]) -> str:
    """The inputs of the latest live record and how old its snapshot was at the run's data time."""
    if live is None:
        return "Last record used: not computed (no live run stored)"
    snapshot = live.snapshot_captured_at_utc
    snapshot_text = "not computed" if snapshot is None else (
        f"{format_utc(snapshot)} ({format_duration(live.as_of_utc - snapshot)} before the run)")
    open_interest = live.open_interest_captured_at_utc
    open_interest_text = "not computed" if open_interest is None else format_utc(open_interest)
    return f"Last record used: snapshot {snapshot_text}, open interest {open_interest_text}"


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
                 f"({format_duration(muted_until - status.now)} left)"]
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


def format_status_reply(
    status: MonitorStatus, stale_limit: timedelta = DEFAULT_STALE_SNAPSHOT_LIMIT, *,
    newest_snapshot_captured_at_utc: Optional[datetime],
    newest_snapshot_not_readable: bool = False,
) -> str:
    """
    /status: health of the monitor job (``stale_limit``,
    ``newest_snapshot_captured_at_utc`` and ``newest_snapshot_not_readable``:
    see ``monitor_health_problems``),
    last run per mode, the newest snapshot in the database and its age, the
    snapshot and open-interest instants the latest live record used,
    whether the hourly job ran in the last 2 h, the alert state (muted or
    not) and the last delivered alert.
    """
    now = status.now
    live = status.latest_live_run
    problems = monitor_health_problems(
        status, stale_limit, newest_snapshot_captured_at_utc=newest_snapshot_captured_at_utc,
        newest_snapshot_not_readable=newest_snapshot_not_readable)
    lines = [
        f"<b>{escape(status.currency)} MenthorQ monitor status</b> | {escape(format_utc(now))}",
        "Health: OK" if not problems else f"Health: PROBLEM: {escape('; '.join(problems))}",
        escape(_run_line("Live run", live, now)),
        escape(_run_line("Daily close", status.latest_daily_close_run, now)),
    ]
    if newest_snapshot_not_readable:
        lines.append("Newest snapshot: not readable (see the collector log)")
    elif newest_snapshot_captured_at_utc is not None:
        lines.append(escape(
            f"Newest snapshot: {format_utc(newest_snapshot_captured_at_utc)} "
            f"({format_duration(now - newest_snapshot_captured_at_utc)} old)"))
    else:
        lines.append("Newest snapshot: not computed (none in the database)")
    lines.append(escape(_record_used_line(live)))
    ran_recently = live is not None and now - live.computed_at_utc <= HOURLY_JOB_EXPECTED_WITHIN
    lines.append(f"Hourly job ran in the last 2h: {'yes' if ran_recently else 'NO'}")
    lines.extend(escape(line) for line in _alert_state_lines(status))
    return "\n".join(lines)


def format_mute_set_reply(currency: str, muted_until: datetime, duration_text: str) -> str:
    """/mute <duration> saved."""
    return (f"<b>{escape(currency)} URGENT alerts muted</b> until "
            f"{escape(format_utc(muted_until))} ({escape(duration_text)}).\n"
            "Command replies and the weekly brief are still sent. /mute off ends it early.\n"
            "A sign flip or HVL crossing during the mute is sent after it if the new state "
            "still holds (for the first alert of its kind: if it still differs from the state "
            "24h ago); /brief and /levels show the current state.")


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
