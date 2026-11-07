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
from typing import Any, Dict, List, Optional

from coding.core.analytics.menthorq_chart_renderer import (
    render_expiry_iv_times_open_interest_chart,
    render_gex_panels_chart,
    render_iv_times_open_interest_chart,
    render_net_dex_chart,
)
from coding.core.analytics.menthorq_replication_calculator import (
    DEFAULT_LEVEL_CANDIDATES,
    EXPIRY_UNIVERSE_ALL,
    EXPIRY_UNIVERSE_MONTHLY,
    EXPIRY_UNIVERSES,
    GAMMA_UNDERLYING_INDEX,
    GAMMA_UNDERLYINGS,
    compare_with_reference,
    compute_expiry_iv_times_open_interest,
    replicate_menthorq,
)
from coding.core.database.repository import DatabaseRepository
from coding.service.on_chain.menthorq_input_loader import MenthorqInputLoader

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
        Newest chain snapshot and index price at or before ``as_of``; see
        ``MenthorqInputLoader.load_inputs`` (same arguments, result and
        errors).
        """
        return MenthorqInputLoader(self.repository).load_inputs(currency, as_of, open_interest_as_of)

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
                        "%s / gamma at %s: GEX not computed (no leg has both gamma and open "
                        "interest) for %d of %d "
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
        charts: Dict[str, str] = {}
        chart_variants = {"": primary}
        if expiry_universe != EXPIRY_UNIVERSE_ALL:
            # Variant with every listed expiry (dailies and weeklies included).
            chart_variants["_all_listed_expiries"] = configurations[
                f"{EXPIRY_UNIVERSE_ALL}__gamma_at_{gamma_underlying}"]["replication"]
        for suffix, replication in chart_variants.items():
            for chart_name, render in (("iv_times_open_interest", render_iv_times_open_interest_chart),
                                       ("net_gex_panels", render_gex_panels_chart),
                                       ("net_dex", render_net_dex_chart)):
                path = output_directory / f"{stem}_{chart_name}{suffix}.png"
                render(replication, path, timestamp_label)
                charts[f"{chart_name}{suffix}"] = path.name

        per_expiry = self._render_per_expiry_iv_times_open_interest(
            inputs["chain_rows"],
            configurations[f"{EXPIRY_UNIVERSE_ALL}__gamma_at_{gamma_underlying}"]["replication"],
            output_directory, stem, timestamp_label,
        )

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
            "charts": charts,
            "per_expiry_iv_times_open_interest": per_expiry,
            "configurations": configurations,
        }
        json_path = output_directory / f"{stem}_replication.json"
        json_path.write_text(json.dumps(output, indent=1, default=str), encoding="utf-8")
        logger.info("Wrote %s", json_path)
        return output

    @staticmethod
    def _render_per_expiry_iv_times_open_interest(
        chain_rows: List[Dict[str, Any]], all_expiries_replication: Dict[str, Any],
        output_directory: Path, stem: str, timestamp_label: str,
    ) -> List[Dict[str, Any]]:
        """
        One IV*OI chart per expiry listed in the snapshot and not settled at
        as_of (the "all" universe), each with its own net-GEX levels.

        Returns:
            Per expiry: expiration, levels, level_rule, chart file name,
            chain rows without open interest (None in the open-interest
            snapshot) and the per-strike IV*OI.
        """
        spot = all_expiries_replication["settings"]["index_price"]
        results = []
        for expiration in all_expiries_replication["settings"]["expirations"]:
            expiry_result = compute_expiry_iv_times_open_interest(
                chain_rows, expiration, all_expiries_replication["gex_by_expiration"][expiration]
            )
            path = output_directory / f"{stem}_iv_times_open_interest_{expiration}.png"
            render_expiry_iv_times_open_interest_chart(expiry_result, spot, path, timestamp_label)
            rows_without_open_interest = sum(
                1 for row in chain_rows
                if row.get("expiration") == expiration and row.get("open_interest") is None
            )
            if rows_without_open_interest:
                logger.warning("%s: %d rows have no open interest; left out of IV*OI and GEX",
                               expiration, rows_without_open_interest)
            results.append({
                "expiration": expiration,
                "levels": expiry_result["levels"],
                "level_rule": expiry_result["level_rule"],
                "chart": path.name,
                "rows_without_open_interest": rows_without_open_interest,
                "by_strike": expiry_result["by_strike"],
            })
        return results
