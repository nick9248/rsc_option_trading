"""
Orchestrates a point-in-time MenthorQ replication: loads the option-chain
snapshot and index price at or before ``as_of`` from the database (read-only
queries), runs ``replicate_menthorq`` for every candidate configuration,
compares each with an optional reference, renders the charts for the primary
configuration and writes a JSON of all numbers.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from coding.core.analytics.menthorq_chart_renderer import (
    DEFAULT_LEVEL_CANDIDATES,
    render_gex_panels_chart,
    render_iv_times_open_interest_chart,
)
from coding.core.analytics.menthorq_replication_calculator import (
    EXPIRY_UNIVERSE_MONTHLY,
    EXPIRY_UNIVERSES,
    GAMMA_UNDERLYING_INDEX,
    GAMMA_UNDERLYINGS,
    IV_SOURCE_MISSING,
    IV_SOURCE_REBUILT_FROM_MARK_PRICE,
    compare_with_reference,
    fill_missing_implied_volatility,
    replicate_menthorq,
)
from coding.core.database.repository import DatabaseRepository

logger = logging.getLogger(__name__)


class MenthorqReplicationService:
    """Load point-in-time inputs, replicate MenthorQ's charts, write PNG + JSON."""

    def __init__(self, repository: Optional[DatabaseRepository] = None):
        """
        Args:
            repository: Database access; a default ``DatabaseRepository`` when
                omitted. Only its read methods are used.
        """
        self.repository = repository or DatabaseRepository()

    def load_inputs(
        self, currency: str, as_of: datetime, open_interest_as_of: Optional[datetime] = None
    ) -> Dict[str, Any]:
        """
        Newest chain snapshot and index price at or before ``as_of``.

        ``open_interest_as_of`` (optional, must be <= ``as_of``) replaces each
        instrument's open interest with its value in the newest snapshot at
        or before that earlier instant, keeping mark IV and forwards from
        ``as_of``. It exists to test whether a reference (MenthorQ) uses
        older open interest; an instrument absent from the older snapshot
        gets open interest None, not 0.

        A row without ``mark_iv`` gets an IV rebuilt from its ``mark_price``
        and forward (``fill_missing_implied_volatility``, priced at the
        snapshot's capture time). This fallback is logged as a warning, each
        row carries ``iv_source`` and the counts are returned as
        ``iv_source_counts``.

        Raises:
            LookupError: when an input has no row at or before its instant
                (the replay never falls back to a later row).
            ValueError: when ``open_interest_as_of`` is after ``as_of``.
        """
        snapshot = self.repository.get_option_chain_snapshot_at_or_before(currency, as_of)
        if snapshot is None:
            raise LookupError(f"No {currency} option-chain snapshot at or before {as_of.isoformat()}")
        index = self.repository.get_index_price_at_or_before(currency, as_of)
        if index is None:
            raise LookupError(f"No {currency} index price at or before {as_of.isoformat()}")
        captured_at, chain_rows = snapshot
        index_price, index_time = index
        open_interest_captured_at = captured_at
        if open_interest_as_of is not None:
            if open_interest_as_of > as_of:
                raise ValueError("open_interest_as_of must not be after as_of (lookahead)")
            older = self.repository.get_option_chain_snapshot_at_or_before(
                currency, open_interest_as_of
            )
            if older is None:
                raise LookupError(
                    f"No {currency} snapshot at or before {open_interest_as_of.isoformat()}"
                )
            open_interest_captured_at, older_rows = older
            older_open_interest = {
                row["instrument_name"]: row["open_interest"] for row in older_rows
            }
            chain_rows = [
                dict(row, open_interest=older_open_interest.get(row["instrument_name"]))
                for row in chain_rows
            ]
        chain_rows, iv_source_counts = fill_missing_implied_volatility(chain_rows, captured_at)
        if iv_source_counts[IV_SOURCE_REBUILT_FROM_MARK_PRICE]:
            logger.warning(
                "mark_iv missing on %d rows of the %s snapshot: IV rebuilt from mark_price "
                "and the forward (Black-76, r = 0), labelled iv_source=%r",
                iv_source_counts[IV_SOURCE_REBUILT_FROM_MARK_PRICE], captured_at.isoformat(),
                IV_SOURCE_REBUILT_FROM_MARK_PRICE,
            )
        if iv_source_counts[IV_SOURCE_MISSING]:
            logger.warning("%d rows have no IV (no mark_iv and none rebuilt); their gamma is not computed",
                           iv_source_counts[IV_SOURCE_MISSING])
        logger.info(
            "Inputs for %s: snapshot captured %s (%d rows), index %.2f from trade at %s",
            as_of.isoformat(), captured_at.isoformat(), len(chain_rows), index_price,
            index_time.isoformat(),
        )
        return {
            "snapshot_captured_at_utc": captured_at,
            "open_interest_captured_at_utc": open_interest_captured_at,
            "chain_rows": chain_rows,
            "iv_source_counts": iv_source_counts,
            "index_price": index_price,
            "index_price_time_utc": index_time,
        }

    def run(
        self,
        currency: str,
        as_of: datetime,
        output_directory: Path,
        expiry_universe: str = EXPIRY_UNIVERSE_MONTHLY,
        gamma_underlying: str = GAMMA_UNDERLYING_INDEX,
        reference: Optional[Dict[str, Any]] = None,
        open_interest_as_of: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Run the replication and write the artefacts.

        Args:
            currency: "BTC" (MenthorQ's Deribit charts are BTC).
            as_of: Timezone-aware UTC replay instant.
            output_directory: Folder for the PNGs and JSON (created if missing).
            expiry_universe: Universe used for the rendered charts
                (default "monthly", as in ``replicate_menthorq``).
            gamma_underlying: "index" (default, as in ``replicate_menthorq``)
                or "forward" for the rendered charts.
            reference: Optional numbers read off MenthorQ, compared with
                every candidate configuration.
            open_interest_as_of: Optional earlier instant to take open
                interest from (see ``load_inputs``).

        Returns:
            The dict written to JSON.
        """
        inputs = self.load_inputs(currency, as_of, open_interest_as_of)
        output_directory.mkdir(parents=True, exist_ok=True)

        configurations: Dict[str, Dict[str, Any]] = {}
        for universe in EXPIRY_UNIVERSES:
            for underlying in GAMMA_UNDERLYINGS:
                replication = replicate_menthorq(
                    inputs["chain_rows"], inputs["index_price"], as_of, universe, underlying
                )
                not_computed = [expiration for expiration, exposure
                                in replication["gex_by_expiration"].items()
                                if exposure["total_net_gex"] is None]
                if not_computed:
                    logger.warning(
                        "%s / gamma at %s: GEX not computed (no leg has a gamma) for %d of %d "
                        "expiries %s; their levels are None and they are skipped for panels",
                        universe, underlying, len(not_computed),
                        len(replication["gex_by_expiration"]), not_computed,
                    )
                configurations[f"{universe}__gamma_at_{underlying}"] = {
                    "replication": replication,
                    "comparison": (
                        compare_with_reference(replication, reference)
                        if reference is not None else None
                    ),
                }

        primary_name = f"{expiry_universe}__gamma_at_{gamma_underlying}"
        primary = configurations[primary_name]["replication"]
        timestamp_label = f"{as_of.strftime('%Y-%m-%d %H:%M')} UTC"
        stem = f"{currency.lower()}_{as_of.strftime('%Y%m%dT%H%M')}Z"
        if open_interest_as_of is not None:
            stem += f"_open_interest_{open_interest_as_of.strftime('%Y%m%dT%H%M')}Z"
        iv_path = output_directory / f"{stem}_iv_times_open_interest.png"
        gex_path = output_directory / f"{stem}_net_gex_panels.png"
        render_iv_times_open_interest_chart(primary, iv_path, timestamp_label)
        render_gex_panels_chart(primary, gex_path, timestamp_label)

        output = {
            "as_of_utc": as_of.isoformat(),
            "currency": currency,
            "inputs": {
                "snapshot_captured_at_utc": inputs["snapshot_captured_at_utc"].isoformat(),
                "open_interest_captured_at_utc": inputs["open_interest_captured_at_utc"].isoformat(),
                "chain_row_count": len(inputs["chain_rows"]),
                "iv_source_counts": inputs["iv_source_counts"],
                "index_price": inputs["index_price"],
                "index_price_source": "historical_trades.index_price of the newest trade at or before as_of",
                "index_price_time_utc": inputs["index_price_time_utc"].isoformat(),
            },
            "primary_configuration": primary_name,
            "drawn_level_candidates": DEFAULT_LEVEL_CANDIDATES,
            "charts": {"iv_times_open_interest": iv_path.name, "net_gex_panels": gex_path.name},
            "configurations": configurations,
        }
        json_path = output_directory / f"{stem}_replication.json"
        json_path.write_text(json.dumps(output, indent=1, default=str), encoding="utf-8")
        logger.info("Wrote %s", json_path)
        return output
