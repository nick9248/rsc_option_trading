"""
Point-in-time replay of MenthorQ's BTC (Deribit) IV*OI chart and Net GEX
panels from the local database.

Usage (from repo root):
    .venv\\Scripts\\python -m scripts.replicate_menthorq --as-of 2026-11-06T20:59:00Z ^
        --reference outputs/menthorq_replicate/menthorq_reference_20261106T2059Z.json

Reads only (newest snapshot and index price at or before --as-of; no
lookahead). Writes PNGs and a JSON of every number to --output-directory.
"""

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from coding.core.analytics.menthorq_replication_calculator import (
    EXPIRY_UNIVERSE_MONTHLY,
    EXPIRY_UNIVERSES,
    GAMMA_UNDERLYING_FORWARD,
    GAMMA_UNDERLYINGS,
)
from coding.core.logging.logging_setup import init_logging
from coding.service.on_chain.menthorq_replication_service import MenthorqReplicationService

logger = logging.getLogger(__name__)


def parse_utc_timestamp(text: str) -> datetime:
    """ISO timestamp -> UTC-aware datetime; a timestamp without offset is taken as UTC."""
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def main() -> None:
    """Parse arguments and run the replication."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--as-of", required=True, type=parse_utc_timestamp,
                        help="Replay instant, ISO 8601 (UTC if no offset)")
    parser.add_argument("--currency", default="BTC")
    parser.add_argument("--output-directory", type=Path,
                        default=Path("outputs") / "menthorq_replicate")
    parser.add_argument("--expiry-universe", choices=EXPIRY_UNIVERSES,
                        default=EXPIRY_UNIVERSE_MONTHLY,
                        help="Expiry set used for the rendered charts")
    parser.add_argument("--gamma-underlying", choices=GAMMA_UNDERLYINGS,
                        default=GAMMA_UNDERLYING_FORWARD,
                        help="Price the gamma is evaluated at for the rendered charts")
    parser.add_argument("--reference", type=Path, default=None,
                        help="JSON of MenthorQ numbers to compare against")
    parser.add_argument("--open-interest-as-of", type=parse_utc_timestamp, default=None,
                        help="Optional earlier instant to take open interest from "
                             "(tests whether the reference uses older open interest)")
    arguments = parser.parse_args()

    init_logging(level="INFO", task_name="replicate_menthorq")
    reference = (json.loads(arguments.reference.read_text(encoding="utf-8"))
                 if arguments.reference else None)
    MenthorqReplicationService().run(
        currency=arguments.currency,
        as_of=arguments.as_of,
        output_directory=arguments.output_directory,
        expiry_universe=arguments.expiry_universe,
        gamma_underlying=arguments.gamma_underlying,
        reference=reference,
        open_interest_as_of=arguments.open_interest_as_of,
    )


if __name__ == "__main__":
    main()
