"""
Orchestrates a point-in-time MenthorQ replication: loads the option-chain
snapshot and index price at or before ``as_of`` from the database (read-only
queries), runs ``replicate_menthorq`` for every candidate configuration,
compares each with an optional reference, renders the charts for the primary
configuration and writes a JSON of all numbers.
"""

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from coding.core.analytics.menthorq_chart_renderer import (
    render_expiry_iv_times_open_interest_chart,
    render_gex_panels_chart,
    render_iv_times_open_interest_chart,
    render_net_dex_chart,
)
from coding.core.analytics.menthorq_replication_calculator import (
    DAILY_CLOSE_DATA_TIME_UTC,
    DEFAULT_LEVEL_CANDIDATES,
    EXPIRY_UNIVERSE_ALL,
    EXPIRY_UNIVERSE_MONTHLY,
    EXPIRY_UNIVERSES,
    GAMMA_UNDERLYING_INDEX,
    GAMMA_UNDERLYINGS,
    compare_with_reference,
    compute_expiry_iv_times_open_interest,
    expiries_without_any_open_interest,
    expiry_not_in_open_interest_snapshot_text,
    iv_source_text,
    replicate_menthorq,
    start_of_utc_day,
)
from coding.core.database.repository import DatabaseRepository
from coding.service.on_chain.menthorq_input_loader import MenthorqInputLoader

logger = logging.getLogger(__name__)

# MenthorQ labels its BTC charts "15:59 EST", but its printed spot matches the
# Deribit index at about 23:59 UTC (checked on 2026-08-24, 2026-10-12 and
# 2026-11-06); its open interest is about one day older (00:00 UTC is a
# consistent choice, not a proven instant -- see ``start_of_utc_day``).

RUN_MODE_AS_OF = "as_of"
RUN_MODE_MENTHORQ_DAILY_CLOSE = "menthorq_daily_close"
RUN_MODE_LIVE = "live"

# "flat": every file directly in the output folder (the original layout).
# "nested": <output folder>/<YYYY-MM-DD of as_of>/ with one subfolder per
# chart kind, replication.json and README.txt at the date folder root.
CHART_LAYOUT_FLAT = "flat"
CHART_LAYOUT_NESTED = "nested"
CHART_LAYOUTS = (CHART_LAYOUT_FLAT, CHART_LAYOUT_NESTED)

FOLDER_IV_TIMES_OPEN_INTEREST = "01_iv_times_open_interest"
FOLDER_IV_TIMES_OPEN_INTEREST_PER_EXPIRY = "02_iv_times_open_interest_per_expiry"
FOLDER_NET_GEX_PANELS = "03_net_gex_panels"
FOLDER_NET_DEX = "04_net_dex"
NESTED_CHART_FOLDERS = {
    "iv_times_open_interest": FOLDER_IV_TIMES_OPEN_INTEREST,
    "net_gex_panels": FOLDER_NET_GEX_PANELS,
    "net_dex": FOLDER_NET_DEX,
}
NESTED_JSON_FILE_NAME = "replication.json"
NESTED_README_FILE_NAME = "README.txt"


def menthorq_daily_close_instants(menthorq_date: date) -> Tuple[datetime, datetime]:
    """
    (as_of, open_interest_as_of) that replay a MenthorQ chart labelled with
    ``menthorq_date``: 23:59 UTC of that date, open interest from 00:00 UTC
    of the same date.
    """
    as_of = datetime.combine(menthorq_date, DAILY_CLOSE_DATA_TIME_UTC, tzinfo=timezone.utc)
    return as_of, start_of_utc_day(as_of)


def last_complete_utc_day(now: datetime) -> date:
    """
    The newest UTC calendar day that has fully ended at ``now`` (yesterday
    in UTC), the default date for a MenthorQ daily-close replay.

    Raises:
        ValueError: when ``now`` is naive.
    """
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(timezone.utc).date() - timedelta(days=1)


def nested_date_folder(output_directory: Path, chart_date: date) -> Path:
    """The folder a "nested" run for the UTC date ``chart_date`` writes into."""
    return output_directory / chart_date.isoformat()


def _format_utc(instant: datetime) -> str:
    """'2026-11-06 23:59:00 UTC'."""
    return instant.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def nested_readme_text(
    output: Dict[str, Any], open_interest_as_of: Optional[datetime]
) -> str:
    """
    Plain-ASCII README of a "nested" run folder: what each subfolder holds
    and the run settings, all taken from the run's ``output``.

    Args:
        output: The dict the run writes to replication.json.
        open_interest_as_of: The open-interest instant asked for (None: the
            open interest comes from the same snapshot as the IV).
    """
    inputs = output["inputs"]
    as_of = datetime.fromisoformat(output["as_of_utc"])
    primary_settings = output["configurations"][output["primary_configuration"]][
        "replication"]["settings"]
    universes = primary_settings["expiry_universe"]
    if any(name.endswith("_all_listed_expiries") for name in output["charts"]):
        universes += " (file names without suffix) and all listed (suffix _all_listed_expiries)"

    def chart_count(folder: str) -> int:
        names = list(output["charts"].values()) + [
            entry["chart"] for entry in output["per_expiry_iv_times_open_interest"]]
        return sum(1 for name in names if name.startswith(folder + "/"))

    if open_interest_as_of is None:
        open_interest_text = "same snapshot as the IV"
    else:
        open_interest_text = (f"snapshot at or before {_format_utc(open_interest_as_of)}, "
                              f"captured {_format_utc(datetime.fromisoformat(inputs['open_interest_captured_at_utc']))}")
    folders = (
        (FOLDER_IV_TIMES_OPEN_INTEREST, "IV*OI over all expiries of the universe"),
        (FOLDER_IV_TIMES_OPEN_INTEREST_PER_EXPIRY, "IV*OI of one listed expiry per chart"),
        (FOLDER_NET_GEX_PANELS, "Net GEX, four expiry panels"),
        (FOLDER_NET_DEX, "Net DEX over all expiries of the universe"),
    )
    lines = [
        f"MenthorQ-style charts, {output['currency']}, {output['run_mode']}, "
        f"{as_of.date().isoformat()}",
        "Generated by MenthorqReplicationService. Every number is in replication.json.",
        "",
        "Run settings",
        f"  As of (spot and IV):  {_format_utc(as_of)}",
        f"  Snapshot captured:    "
        f"{_format_utc(datetime.fromisoformat(inputs['snapshot_captured_at_utc']))}",
        f"  Open interest:        {open_interest_text}",
        f"  Index price:          {inputs['index_price']:,.2f} (trade at "
        f"{_format_utc(datetime.fromisoformat(inputs['index_price_time_utc']))})",
        f"  IV source:            {iv_source_text(inputs['iv_source_counts'])}",
        f"  Expiry universes:     {universes}",
        f"  Gamma at:             {primary_settings['gamma_underlying']}",
        "",
        "Contents",
    ]
    lines += [f"  {folder + '/':<38}{description} ({chart_count(folder)} files)"
              for folder, description in folders]
    lines += [f"  {NESTED_JSON_FILE_NAME:<38}every number of the run; chart paths are relative "
              "to this folder",
              f"  {NESTED_README_FILE_NAME:<38}this file", ""]
    return "\n".join(lines)


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
        run_mode: str = RUN_MODE_AS_OF,
        chart_layout: str = CHART_LAYOUT_FLAT,
    ) -> Dict[str, Any]:
        """
        Run the replication and write the artefacts.

        Args:
            currency: "BTC" or "ETH". The formulas were fitted on MenthorQ's
                BTC (Deribit) charts only; for ETH they are applied unchanged
                and are not validated.
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
            run_mode: Label stored in the output ("as_of",
                "menthorq_daily_close" or "live"); "live" also adds "_live"
                to the file names.
            chart_layout: "flat" (default): every file directly in
                ``output_directory``, JSON named ``<stem>_replication.json``.
                "nested": files in ``nested_date_folder(output_directory,
                as_of date)`` -- PNGs in the ``NESTED_CHART_FOLDERS`` /
                per-expiry subfolders, ``replication.json`` and
                ``README.txt`` at its root; chart names in the JSON are then
                paths relative to the date folder ("03_net_gex_panels/x.png").
                A re-run overwrites files of the same names, deletes nothing.

        Returns:
            The dict written to JSON.

        Raises:
            ValueError: for an unknown ``chart_layout``.
        """
        if chart_layout not in CHART_LAYOUTS:
            raise ValueError(f"chart_layout must be one of {CHART_LAYOUTS}, not {chart_layout!r}")
        inputs = self.load_inputs(currency, as_of, open_interest_as_of)
        nested = chart_layout == CHART_LAYOUT_NESTED
        if nested:
            output_directory = nested_date_folder(output_directory, as_of.astimezone(timezone.utc).date())
        output_directory.mkdir(parents=True, exist_ok=True)

        # Expiries listed after the open-interest snapshot (no row has open interest) are
        # expected: ONE info line per run each (below), not a warning per configuration.
        listed_after_open_interest = set(expiries_without_any_open_interest(
            inputs["chain_rows"], {row.get("expiration") for row in inputs["chain_rows"]}))
        configurations: Dict[str, Dict[str, Any]] = {}
        for universe in EXPIRY_UNIVERSES:
            for underlying in GAMMA_UNDERLYINGS:
                replication = replicate_menthorq(
                    inputs["chain_rows"], inputs["index_price"], as_of, universe, underlying,
                    currency=currency,
                )
                not_computed = [expiration for expiration, exposure
                                in replication["gex_by_expiration"].items()
                                if exposure["total_net_gex"] is None
                                and expiration not in listed_after_open_interest]
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

        # The "all" universe holds every unsettled expiry of every universe.
        all_expiries_replication = configurations[
            f"{EXPIRY_UNIVERSE_ALL}__gamma_at_{gamma_underlying}"]["replication"]
        not_in_open_interest_snapshot = [expiration for expiration
                                         in all_expiries_replication["settings"]["expirations"]
                                         if expiration in listed_after_open_interest]
        for expiration in not_in_open_interest_snapshot:
            logger.info(expiry_not_in_open_interest_snapshot_text(expiration))

        primary_name = f"{expiry_universe}__gamma_at_{gamma_underlying}"
        primary = configurations[primary_name]["replication"]
        timestamp_label = f"{as_of.strftime('%Y-%m-%d %H:%M')} UTC"
        stem = f"{currency.lower()}_{as_of.strftime('%Y%m%dT%H%M')}Z"
        if open_interest_as_of is not None:
            stem += f"_open_interest_{open_interest_as_of.strftime('%Y%m%dT%H%M')}Z"
        if run_mode == RUN_MODE_LIVE:
            stem += "_live"
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
                relative_path = Path(f"{stem}_{chart_name}{suffix}.png")
                if nested:
                    relative_path = Path(NESTED_CHART_FOLDERS[chart_name]) / relative_path
                path = output_directory / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                render(replication, path, timestamp_label, currency=currency)
                charts[f"{chart_name}{suffix}"] = relative_path.as_posix()

        per_expiry = self._render_per_expiry_iv_times_open_interest(
            inputs["chain_rows"], all_expiries_replication,
            output_directory, stem, timestamp_label, currency,
            FOLDER_IV_TIMES_OPEN_INTEREST_PER_EXPIRY if nested else None,
            expected_without_open_interest=set(not_in_open_interest_snapshot),
        )

        output = {
            "run_mode": run_mode,
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
            # Listed after the open-interest snapshot: expected, a note rather than a warning.
            "expiries_not_in_open_interest_snapshot": not_in_open_interest_snapshot,
            "configurations": configurations,
        }
        if nested:
            output["chart_layout"] = CHART_LAYOUT_NESTED
            json_path = output_directory / NESTED_JSON_FILE_NAME
            readme_path = output_directory / NESTED_README_FILE_NAME
            readme_path.write_text(nested_readme_text(output, open_interest_as_of), encoding="ascii")
            logger.info("Wrote %s", readme_path)
        else:
            json_path = output_directory / f"{stem}_replication.json"
        json_path.write_text(json.dumps(output, indent=1, default=str), encoding="utf-8")
        logger.info("Wrote %s", json_path)
        return output

    def run_menthorq_daily_close(
        self,
        currency: str,
        menthorq_date: date,
        output_directory: Path,
        expiry_universe: str = EXPIRY_UNIVERSE_MONTHLY,
        gamma_underlying: str = GAMMA_UNDERLYING_INDEX,
        reference: Optional[Dict[str, Any]] = None,
        now: Optional[datetime] = None,
        chart_layout: str = CHART_LAYOUT_FLAT,
    ) -> Dict[str, Any]:
        """
        Replay the MenthorQ chart labelled ``menthorq_date``
        (``menthorq_daily_close_instants``: 23:59 UTC, open interest from
        00:00 UTC of that date). Same artefacts as ``run``.

        Args:
            now: Wall-clock instant, timezone-aware (default: the current
                UTC time); only used to reject a day that has not ended.
            chart_layout: "flat" or "nested", see ``run``; "nested" writes
                into ``nested_date_folder(output_directory, menthorq_date)``.

        Raises:
            ValueError: when ``menthorq_date`` is after
                ``last_complete_utc_day(now)`` (the UTC day has not ended,
                so there is no daily close yet; 23:59:30 UTC of the date is
                still rejected, 00:00 UTC of the next day is allowed), or
                when ``now`` is naive.
        """
        now = now or datetime.now(timezone.utc)
        as_of, open_interest_as_of = menthorq_daily_close_instants(menthorq_date)
        if menthorq_date > last_complete_utc_day(now):
            raise ValueError(
                f"{menthorq_date.isoformat()} has not ended at {now.isoformat()}: "
                f"no daily close yet (newest complete UTC day: "
                f"{last_complete_utc_day(now).isoformat()})"
            )
        return self.run(
            currency=currency, as_of=as_of, output_directory=output_directory,
            expiry_universe=expiry_universe, gamma_underlying=gamma_underlying,
            reference=reference, open_interest_as_of=open_interest_as_of,
            run_mode=RUN_MODE_MENTHORQ_DAILY_CLOSE, chart_layout=chart_layout,
        )

    def run_live(
        self,
        currency: str,
        output_directory: Path,
        expiry_universe: str = EXPIRY_UNIVERSE_MONTHLY,
        gamma_underlying: str = GAMMA_UNDERLYING_INDEX,
        reference: Optional[Dict[str, Any]] = None,
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        "Live" charts from the newest option-chain snapshot in the database
        at or before ``now``: as_of is that snapshot's capture time, so the
        index price is the newest one at or before it and the open interest
        comes from the same snapshot. "Live" means the newest stored data,
        not a fresh Deribit request (locally that is the last VPS sync).

        Args:
            now: Timezone-aware instant (default: the current UTC time).

        Raises:
            LookupError: when no snapshot exists at or before ``now``.
        """
        now = now or datetime.now(timezone.utc)
        snapshot = self.repository.get_option_chain_snapshot_at_or_before(currency, now)
        if snapshot is None:
            raise LookupError(f"No {currency} option-chain snapshot at or before {now.isoformat()}")
        captured_at, _ = snapshot
        logger.info("Live %s charts from the newest snapshot, captured %s", currency,
                    captured_at.isoformat())
        return self.run(
            currency=currency, as_of=captured_at, output_directory=output_directory,
            expiry_universe=expiry_universe, gamma_underlying=gamma_underlying,
            reference=reference, run_mode=RUN_MODE_LIVE,
        )

    @staticmethod
    def _render_per_expiry_iv_times_open_interest(
        chain_rows: List[Dict[str, Any]], all_expiries_replication: Dict[str, Any],
        output_directory: Path, stem: str, timestamp_label: str, currency: str,
        subfolder: Optional[str] = None,
        expected_without_open_interest: Optional[Set[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        One IV*OI chart per expiry listed in the snapshot and not settled at
        as_of (the "all" universe), each with its own net-GEX levels.

        Args:
            subfolder: Folder under ``output_directory`` for the PNGs (None:
                directly in it).
            expected_without_open_interest: Expiries listed after the
                open-interest snapshot (already logged at INFO by ``run``):
                their missing open interest is not warned about again.

        Returns:
            Per expiry: expiration, levels, level_rule, chart path relative
            to ``output_directory`` (the file name when ``subfolder`` is
            None), chain rows without open interest (None in the
            open-interest snapshot) and the per-strike IV*OI.
        """
        spot = all_expiries_replication["settings"]["index_price"]
        results = []
        for expiration in all_expiries_replication["settings"]["expirations"]:
            expiry_result = compute_expiry_iv_times_open_interest(
                chain_rows, expiration, all_expiries_replication["gex_by_expiration"][expiration]
            )
            relative_path = Path(f"{stem}_iv_times_open_interest_{expiration}.png")
            if subfolder is not None:
                relative_path = Path(subfolder) / relative_path
            path = output_directory / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            render_expiry_iv_times_open_interest_chart(expiry_result, spot, path, timestamp_label,
                                                       currency=currency)
            rows_without_open_interest = sum(
                1 for row in chain_rows
                if row.get("expiration") == expiration and row.get("open_interest") is None
            )
            if rows_without_open_interest and expiration not in (expected_without_open_interest or set()):
                logger.warning("%s: %d rows have no open interest; left out of IV*OI and GEX",
                               expiration, rows_without_open_interest)
            results.append({
                "expiration": expiration,
                "levels": expiry_result["levels"],
                "level_rule": expiry_result["level_rule"],
                "chart": relative_path.as_posix(),
                "rows_without_open_interest": rows_without_open_interest,
                "by_strike": expiry_result["by_strike"],
            })
        return results
