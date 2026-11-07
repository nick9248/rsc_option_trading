"""
Orchestrates a point-in-time MenthorQ replication: loads the option-chain
snapshot and index price at or before ``as_of`` from the database (read-only
queries), runs ``replicate_menthorq`` for every candidate configuration,
compares each with an optional reference, renders the charts for the primary
configuration and writes a JSON of all numbers.
"""

import json
import logging
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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
    IV_SOURCE_MISSING,
    IV_SOURCE_REBUILT_FROM_MARK_PRICE,
    compare_with_reference,
    compute_expiry_iv_times_open_interest,
    fill_missing_implied_volatility,
    replicate_menthorq,
    start_of_utc_day,
)
from coding.core.database.repository import DatabaseRepository

logger = logging.getLogger(__name__)

# MenthorQ labels its BTC charts "15:59 EST", but its printed spot matches the
# Deribit index at about 23:59 UTC (checked on 2026-08-24, 2026-10-12 and
# 2026-11-06); its open interest is about one day older (00:00 UTC is a
# consistent choice, not a proven instant -- see ``start_of_utc_day``).
MENTHORQ_DATA_TIME_UTC = time(23, 59)

RUN_MODE_AS_OF = "as_of"
RUN_MODE_MENTHORQ_DAILY_CLOSE = "menthorq_daily_close"
RUN_MODE_LIVE = "live"


def menthorq_daily_close_instants(menthorq_date: date) -> Tuple[datetime, datetime]:
    """
    (as_of, open_interest_as_of) that replay a MenthorQ chart labelled with
    ``menthorq_date``: 23:59 UTC of that date, open interest from 00:00 UTC
    of the same date.
    """
    as_of = datetime.combine(menthorq_date, MENTHORQ_DATA_TIME_UTC, tzinfo=timezone.utc)
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
        run_mode: str = RUN_MODE_AS_OF,
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
                path = output_directory / f"{stem}_{chart_name}{suffix}.png"
                render(replication, path, timestamp_label, currency=currency)
                charts[f"{chart_name}{suffix}"] = path.name

        per_expiry = self._render_per_expiry_iv_times_open_interest(
            inputs["chain_rows"],
            configurations[f"{EXPIRY_UNIVERSE_ALL}__gamma_at_{gamma_underlying}"]["replication"],
            output_directory, stem, timestamp_label, currency,
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
            "configurations": configurations,
        }
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
    ) -> Dict[str, Any]:
        """
        Replay the MenthorQ chart labelled ``menthorq_date``
        (``menthorq_daily_close_instants``: 23:59 UTC, open interest from
        00:00 UTC of that date). Same artefacts as ``run``.

        Args:
            now: Wall-clock instant, timezone-aware (default: the current
                UTC time); only used to reject a day that has not ended.

        Raises:
            ValueError: when 23:59 UTC of ``menthorq_date`` is after ``now``
                (the day is not complete, so there is no daily close yet).
        """
        now = now or datetime.now(timezone.utc)
        as_of, open_interest_as_of = menthorq_daily_close_instants(menthorq_date)
        if as_of > now:
            raise ValueError(
                f"{menthorq_date.isoformat()} has not ended at {now.isoformat()}: "
                f"no daily close yet (newest complete UTC day: "
                f"{last_complete_utc_day(now).isoformat()})"
            )
        return self.run(
            currency=currency, as_of=as_of, output_directory=output_directory,
            expiry_universe=expiry_universe, gamma_underlying=gamma_underlying,
            reference=reference, open_interest_as_of=open_interest_as_of,
            run_mode=RUN_MODE_MENTHORQ_DAILY_CLOSE,
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
            render_expiry_iv_times_open_interest_chart(expiry_result, spot, path, timestamp_label,
                                                       currency=currency)
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
