"""
Dry run of the MenthorQ-style Telegram brief from the local database.

Usage (from repo root):
    .venv\\Scripts\\python -m scripts.menthorq_brief --dry-run
    .venv\\Scripts\\python -m scripts.menthorq_brief --dry-run --as-of 2026-11-07T09:59Z

Computes the live record at --as-of (default: now), replays the records of
one hour, 24 hours and 7 days earlier and the daily-close record of the
previous UTC date from the stored snapshots (point in time, no lookahead),
then writes the brief (Telegram HTML) and the urgent alerts that would fire
(before any cooldown) to standard output.

Sends nothing and writes nothing: it does not need the monitor tables
(migration 031) and only reads snapshots and historical_trades.
"""

import argparse
import logging
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from coding.core.analytics.menthorq_monitor.alert_rules import evaluate_urgent_alerts
from coding.core.analytics.menthorq_monitor.level_trend_calculator import (
    COMPARISON_ONE_DAY,
    COMPARISON_PREVIOUS,
)
from coding.core.analytics.menthorq_replication_calculator import EXPIRY_UNIVERSES
from coding.core.logging.logging_setup import init_logging
from coding.service.on_chain.menthorq_monitor.brief_formatter import (
    format_brief,
    format_urgent_alert,
    telegram_length,
)
from coding.service.on_chain.menthorq_monitor.menthorq_monitor_service import MenthorqMonitorService
from coding.service.on_chain.menthorq_monitor.monitor_settings import MenthorqMonitorSettings

logger = logging.getLogger(__name__)


def parse_utc_timestamp(text: str) -> datetime:
    """ISO timestamp -> UTC-aware datetime; a timestamp without offset is taken as UTC."""
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def build_parser() -> argparse.ArgumentParser:
    """Command-line arguments of the dry run."""
    parser = argparse.ArgumentParser(
        description="Write the MenthorQ-style brief from the local database (no send, no write).")
    parser.add_argument("--dry-run", action="store_true", required=True,
                        help="Required: this script never sends or stores anything")
    parser.add_argument("--as-of", type=parse_utc_timestamp, default=None,
                        help="Data instant, ISO 8601 (UTC if no offset); default now")
    parser.add_argument("--expiry-universe", choices=EXPIRY_UNIVERSES, default=None,
                        help="Override MENTHORQ_EXPIRY_UNIVERSE")
    return parser


def build_dry_run_output(service: MenthorqMonitorService, as_of: datetime) -> str:
    """The brief and the urgent-alert preview as one text."""
    latest = service.compute_live_record(as_of)
    comparisons = service.build_replayed_comparisons(latest)
    daily_close = service.compute_daily_close_record((as_of - timedelta(days=1)).date())
    brief = format_brief(latest, comparisons,
                         daily_close if daily_close.spot is not None else None,
                         title="Dry run (references replayed, not stored)")
    by_label = {comparison.label: comparison for comparison in comparisons}
    alerts = evaluate_urgent_alerts(latest, by_label[COMPARISON_PREVIOUS], by_label[COMPARISON_ONE_DAY],
                                    service.settings.alert_thresholds)
    parts = [brief, f"\n[brief length {telegram_length(brief)} of 4096]\n"]
    if alerts:
        alert_text = format_urgent_alert(latest, alerts)
        parts += ["\n--- urgent alert that would be sent (before cooldown) ---\n", alert_text,
                  f"\n[alert length {telegram_length(alert_text)} of 4096]\n"]
    else:
        parts.append("\n--- no urgent alert would fire ---\n")
    return "".join(parts)


def main(argv: Optional[Sequence[str]] = None) -> None:
    """Parse arguments and write the dry-run brief to standard output."""
    arguments = build_parser().parse_args(argv)
    init_logging(level="WARNING", task_name="menthorq_brief")
    settings = MenthorqMonitorSettings.from_environment()
    if arguments.expiry_universe is not None:
        settings = replace(settings, expiry_universe=arguments.expiry_universe)
    as_of = arguments.as_of or datetime.now(timezone.utc)
    sys.stdout.write(build_dry_run_output(MenthorqMonitorService(settings=settings), as_of))


if __name__ == "__main__":
    main()
