"""
Point-in-time replay of MenthorQ's BTC (Deribit) IV*OI chart and Net GEX
panels from the local database.

Usage (from repo root):
    .venv\\Scripts\\python -m scripts.replicate_menthorq --menthorq-date 2026-10-12 ^
        --expiry-universe all ^
        --reference outputs/menthorq_replicate/menthorq_reference_20261012T2359Z.json

--menthorq-date YYYY-MM-DD replays a MenthorQ chart labelled with that date:
as_of = 23:59 UTC of the date, open interest as of 00:00 UTC of the same date.
MenthorQ labels its charts "15:59 EST", but its printed spot matches the
Deribit index at about 23:59 UTC (checked on 2026-08-24, 2026-10-12 and
2026-11-06). Its open interest is about one day older; the data only
constrain that instant to roughly 21:00 UTC of the previous day to about
00:20 UTC, so 00:00 UTC is a consistent choice, not a proven one.

The expiry set is not pinned down: no single universe fits all dates.
All-expiry levels: on 2026-11-06 "monthly" or "quarterly_plus_front_month"
fits ("all" gives Put Support 74000 and HVL 77500, MenthorQ 75000 / 79000);
on 2026-10-12 only "all" fits (the other two give HVL 79000, MenthorQ
83000); on 2026-08-24 every universe gives the legend levels, but only with
IV rebuilt from mark_price (snapshots.mark_iv is NULL until 2026-08-25
15:11 UTC; the service does this as a labelled, logged fallback).
Panel choice on 2026-10-12 needs weeklies but no dailies ("all" picks the
13OCT26/14OCT26 dailies; MenthorQ shows 16OCT26/23OCT26), a set this
script does not offer.

Reads only (newest snapshot and index price at or before as_of; no
lookahead). Writes PNGs and a JSON of every number to --output-directory:
the all-expiry IV*OI, NET GEX panels and Net DEX charts for the chosen
universe (plus an "_all_listed_expiries" variant when it is not "all"), and
one IV*OI chart per listed, unsettled expiry with that expiry's own levels.
"""

import argparse
import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Sequence, Tuple

from coding.core.analytics.menthorq_replication_calculator import (
    EXPIRY_UNIVERSE_MONTHLY,
    EXPIRY_UNIVERSES,
    GAMMA_UNDERLYING_INDEX,
    GAMMA_UNDERLYINGS,
    parse_utc_timestamp,
    start_of_utc_day,
)
from coding.core.logging.logging_setup import init_logging
from coding.service.on_chain.menthorq_replication_service import (
    MenthorqReplicationService,
    menthorq_daily_close_instants,
)

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Command-line arguments of the replay."""
    parser = argparse.ArgumentParser(
        description="Point-in-time replay of MenthorQ's BTC (Deribit) charts from the local database."
    )
    instant_group = parser.add_mutually_exclusive_group(required=True)
    instant_group.add_argument("--as-of", type=parse_utc_timestamp,
                               help="Replay instant, ISO 8601 (UTC if no offset)")
    instant_group.add_argument("--menthorq-date", type=date.fromisoformat,
                               help="Date a MenthorQ chart is labelled with (YYYY-MM-DD): "
                                    "as_of = 23:59 UTC and open interest as of 00:00 UTC of "
                                    "that date (a consistent choice, not a proven instant)")
    parser.add_argument("--currency", default="BTC")
    parser.add_argument("--output-directory", type=Path,
                        default=Path("outputs") / "menthorq_replicate")
    parser.add_argument("--expiry-universe", choices=EXPIRY_UNIVERSES,
                        default=EXPIRY_UNIVERSE_MONTHLY,
                        help="Expiry set used for the rendered charts (not pinned down: "
                             "no single set fits every date)")
    parser.add_argument("--gamma-underlying", choices=GAMMA_UNDERLYINGS,
                        default=GAMMA_UNDERLYING_INDEX,
                        help="Price the gamma is evaluated at for the rendered charts "
                             "(default: index)")
    parser.add_argument("--reference", type=Path, default=None,
                        help="JSON of MenthorQ numbers to compare against")
    open_interest_group = parser.add_mutually_exclusive_group()
    open_interest_group.add_argument("--open-interest-as-of", type=parse_utc_timestamp, default=None,
                                     help="Optional earlier instant to take open interest from "
                                          "(tests whether the reference uses older open interest)")
    open_interest_group.add_argument("--open-interest-at-start-of-utc-day", action="store_true",
                                     help="Take open interest from 00:00 UTC of the --as-of day "
                                          "(consistent with MenthorQ on three dates; the true "
                                          "instant is only known to within about 3 h)")
    return parser


def resolve_instants(
    parser: argparse.ArgumentParser, arguments: argparse.Namespace
) -> Tuple[datetime, Optional[datetime]]:
    """(as_of, open_interest_as_of) from the parsed arguments."""
    if arguments.menthorq_date is not None:
        if arguments.open_interest_as_of is not None or arguments.open_interest_at_start_of_utc_day:
            parser.error("--menthorq-date already sets the open-interest instant")
        return menthorq_daily_close_instants(arguments.menthorq_date)
    if arguments.open_interest_at_start_of_utc_day:
        return arguments.as_of, start_of_utc_day(arguments.as_of)
    return arguments.as_of, arguments.open_interest_as_of


def main(argv: Optional[Sequence[str]] = None) -> None:
    """Parse arguments and run the replication."""
    parser = build_parser()
    arguments = parser.parse_args(argv)
    as_of, open_interest_as_of = resolve_instants(parser, arguments)

    init_logging(level="INFO", task_name="replicate_menthorq")
    reference = (json.loads(arguments.reference.read_text(encoding="utf-8"))
                 if arguments.reference else None)
    MenthorqReplicationService().run(
        currency=arguments.currency,
        as_of=as_of,
        output_directory=arguments.output_directory,
        expiry_universe=arguments.expiry_universe,
        gamma_underlying=arguments.gamma_underlying,
        reference=reference,
        open_interest_as_of=open_interest_as_of,
    )


if __name__ == "__main__":
    main()
