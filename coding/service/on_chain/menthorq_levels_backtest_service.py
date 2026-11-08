"""
Daily historical backfill and backtest of the MenthorQ-style levels.

For every UTC date it replays the verified MenthorQ convention without
rendering charts: as_of = 23:59 UTC, spot = Deribit index from the newest
option trade at or before as_of, open interest from the snapshot at or
before 00:00 UTC of the same date, IV from the snapshot at or before as_of
(rebuilt from mark_price, labelled, where mark_iv is NULL). Levels come from
``replicate_menthorq``; the analysis from ``menthorq_levels_backtest_analysis``.

Reads only. A day whose inputs are missing or stale gets status
"not_computed" with a reason and None values, never a fabricated number.
"""

import csv
import json
import logging
import time as clock
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from coding.core.analytics.menthorq_levels_backtest_analysis import (
    alert_threshold_table,
    predictive_tests,
    trend_summary,
)
from coding.core.analytics.menthorq_levels_backtest_calculator import (
    LEVEL_NAMES,
    attribute_level_change,
    expiry_list_changes,
    forward_price_outcomes,
    gamma_regime,
    settlement_share_of_change,
    summarize_replication,
)
from coding.core.analytics.menthorq_replication_calculator import (
    DAILY_CLOSE_DATA_TIME_UTC,
    DAILY_CLOSE_MAXIMUM_INPUT_AGE,
    EXPIRY_UNIVERSE_ALL,
    EXPIRY_UNIVERSE_MONTHLY,
    GAMMA_UNDERLYING_INDEX,
    PANEL_NAMES,
    IV_SOURCE_MARK_IV,
    IV_SOURCE_MISSING,
    IV_SOURCE_REBUILT_FROM_MARK_PRICE,
    replicate_menthorq,
    start_of_utc_day,
)
from coding.core.database.repository import DatabaseRepository
from coding.service.on_chain.menthorq_replication_service import MenthorqReplicationService

logger = logging.getLogger(__name__)

BACKTEST_UNIVERSES = (EXPIRY_UNIVERSE_MONTHLY, EXPIRY_UNIVERSE_ALL)
MAXIMUM_PRICE_SAMPLE_AGE = timedelta(minutes=120)
OUTCOME_HORIZONS_HOURS = {"1d": 24, "3d": 72}
STATUS_COMPUTED = "computed"
STATUS_NOT_COMPUTED = "not_computed"


class MenthorqLevelsBacktestService:
    """Build the daily level rows, their forward outcomes and the analysis."""

    def __init__(self, repository: Optional[DatabaseRepository] = None):
        """
        Args:
            repository: Database access (read methods only); a default
                ``DatabaseRepository`` when omitted.
        """
        self.repository = repository or DatabaseRepository()
        self.replication_service = MenthorqReplicationService(self.repository)

    # ------------------------------------------------------------------
    # Daily rows
    # ------------------------------------------------------------------

    def _load_day_inputs(self, currency: str, as_of: datetime) -> Dict[str, Any]:
        """Inputs of one day, or {"reason": ...} when they are missing or stale."""
        open_interest_as_of = start_of_utc_day(as_of)
        try:
            inputs = self.replication_service.load_inputs(currency, as_of, open_interest_as_of)
        except LookupError as error:
            return {"reason": str(error)}
        reasons = []
        snapshot_age = as_of - inputs["snapshot_captured_at_utc"]
        if snapshot_age > DAILY_CLOSE_MAXIMUM_INPUT_AGE:
            reasons.append(f"newest snapshot is {snapshot_age} before as_of (day incomplete in the DB)")
        open_interest_age = open_interest_as_of - inputs["open_interest_captured_at_utc"]
        if open_interest_age > DAILY_CLOSE_MAXIMUM_INPUT_AGE:
            reasons.append(f"open-interest snapshot is {open_interest_age} before 00:00 UTC")
        index_age = as_of - inputs["index_price_time_utc"]
        if index_age > DAILY_CLOSE_MAXIMUM_INPUT_AGE:
            reasons.append(f"index price is from a trade {index_age} before as_of")
        if reasons:
            return {"reason": "; ".join(reasons)}
        return inputs

    def build_daily_rows(
        self, currency: str, first_date: date, last_date: date,
        universes: Sequence[str] = BACKTEST_UNIVERSES,
    ) -> List[Dict[str, Any]]:
        """
        One flat row per calendar date from ``first_date`` to ``last_date``
        (inclusive, every date present, not-computed ones included), with the
        levels of each universe and the expiry-settlement attribution of the
        day-over-day change (previous day's inputs recomputed without the
        expiries that settled in between).
        """
        rows: List[Dict[str, Any]] = []
        previous: Optional[Dict[str, Any]] = None
        day = first_date
        while day <= last_date:
            as_of = datetime.combine(day, DAILY_CLOSE_DATA_TIME_UTC, tzinfo=timezone.utc)
            inputs = self._load_day_inputs(currency, as_of)
            row: Dict[str, Any] = {"date": day.isoformat(), "as_of_utc": as_of.isoformat()}
            if "reason" in inputs:
                row.update(status=STATUS_NOT_COMPUTED, reason=inputs["reason"], index_price=None)
                logger.warning("%s not computed: %s", day, inputs["reason"])
                rows.append(row)
                previous = None
                day += timedelta(days=1)
                continue
            counts = inputs["iv_source_counts"]
            row.update(
                status=STATUS_COMPUTED, reason=None,
                snapshot_captured_at_utc=inputs["snapshot_captured_at_utc"].isoformat(),
                open_interest_captured_at_utc=inputs["open_interest_captured_at_utc"].isoformat(),
                index_price=inputs["index_price"],
                index_price_time_utc=inputs["index_price_time_utc"].isoformat(),
                chain_row_count=len(inputs["chain_rows"]),
                iv_source_mark_iv=counts[IV_SOURCE_MARK_IV],
                iv_source_rebuilt_from_mark_price=counts[IV_SOURCE_REBUILT_FROM_MARK_PRICE],
                iv_source_missing=counts[IV_SOURCE_MISSING],
            )
            current = {"as_of": as_of, "inputs": inputs, "summaries": {}}
            for universe in universes:
                summary = summarize_replication(replicate_menthorq(
                    inputs["chain_rows"], inputs["index_price"], as_of, universe, GAMMA_UNDERLYING_INDEX))
                current["summaries"][universe] = summary
                row.update(self._universe_columns(universe, summary, inputs["index_price"]))
                row.update(self._attribution_columns(universe, summary, previous, as_of))
            rows.append(row)
            previous = current
            day += timedelta(days=1)
        return rows

    @staticmethod
    def _universe_columns(universe: str, summary: Dict[str, Any], spot: float) -> Dict[str, Any]:
        levels = summary["levels"]
        columns: Dict[str, Any] = {f"{universe}_{level}": levels[level] for level in LEVEL_NAMES}
        columns.update({
            f"{universe}_total_net_gex_gamma_open_interest_spot":
                summary["total_net_gex_gamma_open_interest_spot"],
            f"{universe}_total_net_gex_usd_per_one_percent": summary["total_net_gex_usd_per_one_percent"],
            f"{universe}_gamma_regime": gamma_regime(spot, levels["hvl"]),
            f"{universe}_expirations": ";".join(summary["expirations"]),
            f"{universe}_expirations_not_computed": ";".join(summary["expirations_not_computed"]),
            f"{universe}_instruments_missing_gamma": summary["instruments_missing_gamma"],
            f"{universe}_levels_not_computed": ";".join(
                level for level in LEVEL_NAMES if levels[level] is None),
        })
        for panel_name in PANEL_NAMES:
            for key, value in summary["panels"][panel_name].items():
                columns[f"{universe}_panel_{panel_name}_{key}"] = value
        return columns

    @staticmethod
    def _attribution_columns(
        universe: str, summary: Dict[str, Any], previous: Optional[Dict[str, Any]], as_of: datetime
    ) -> Dict[str, Any]:
        """Settled/added expiries vs the previous day and the counterfactual levels."""
        names = [f"{universe}_{level}_without_settled" for level in LEVEL_NAMES]
        empty: Dict[str, Any] = dict.fromkeys(names)
        empty.update({f"{universe}_settled_expirations": None, f"{universe}_added_expirations": None,
                      f"{universe}_removed_unsettled_expirations": None,
                      f"{universe}_total_net_gex_without_settled": None,
                      f"{universe}_total_net_gex_settlement_share": None})
        empty.update({f"{universe}_{level}_change_attribution": None for level in LEVEL_NAMES})
        if previous is None:
            return empty
        previous_summary = previous["summaries"][universe]
        changes = expiry_list_changes(previous_summary["expirations"], summary["expirations"], as_of)
        settled = changes["settled"]
        if settled:
            previous_inputs = previous["inputs"]
            kept_rows = [row for row in previous_inputs["chain_rows"] if row["expiration"] not in settled]
            without = summarize_replication(replicate_menthorq(
                kept_rows, previous_inputs["index_price"], previous["as_of"], universe,
                GAMMA_UNDERLYING_INDEX))
        else:
            without = previous_summary
        gex_key = "total_net_gex_gamma_open_interest_spot"
        columns = {
            f"{universe}_settled_expirations": ";".join(settled),
            f"{universe}_added_expirations": ";".join(changes["added"]),
            f"{universe}_removed_unsettled_expirations": ";".join(changes["removed_unsettled"]),
            f"{universe}_total_net_gex_without_settled": without[gex_key],
            f"{universe}_total_net_gex_settlement_share": (
                settlement_share_of_change(previous_summary[gex_key], without[gex_key], summary[gex_key])
                if settled else None),
        }
        for level in LEVEL_NAMES:
            columns[f"{universe}_{level}_without_settled"] = without["levels"][level]
            columns[f"{universe}_{level}_change_attribution"] = attribute_level_change(
                previous_summary["levels"][level], without["levels"][level], summary["levels"][level])
        return columns

    # ------------------------------------------------------------------
    # Forward outcomes (strictly after as_of)
    # ------------------------------------------------------------------

    def _price_at(self, currency: str, instant: datetime, data_end: datetime) -> Optional[float]:
        """Index of the newest trade at or before ``instant``; None when stale or beyond the data."""
        if instant > data_end:
            return None
        found = self.repository.get_index_price_at_or_before(currency, instant)
        if found is None or instant - found[1] > MAXIMUM_PRICE_SAMPLE_AGE:
            return None
        return found[0]

    def add_price_outcomes(self, currency: str, rows: List[Dict[str, Any]]) -> None:
        """
        Add next-1d / next-3d outcomes (hourly index samples after as_of)
        and the trailing 1d realized volatility (the 24 h up to as_of).
        A window with any missing or stale sample gives None.

        The hourly forward samples are read once per day (the longest
        horizon) and sliced for each horizon. A day without an index price
        gets None everywhere without any query (every window starts or ends
        at that price, so none could be computed).
        """
        latest = self.repository.get_index_price_at_or_before(currency, datetime.now(timezone.utc))
        data_end = latest[1] if latest else datetime.min.replace(tzinfo=timezone.utc)
        longest_horizon_hours = max(OUTCOME_HORIZONS_HOURS.values())
        for row in rows:
            as_of = datetime.fromisoformat(row["as_of_utc"])
            if row["index_price"] is None:
                forward_samples: List[Optional[float]] = [None] * longest_horizon_hours
                trailing: List[Optional[float]] = [None]
            else:
                forward_samples = [self._price_at(currency, as_of + timedelta(hours=step), data_end)
                                   for step in range(1, longest_horizon_hours + 1)]
                trailing = [self._price_at(currency, as_of - timedelta(hours=step), data_end)
                            for step in range(24, 0, -1)] + [row["index_price"]]
            for horizon, hours in OUTCOME_HORIZONS_HOURS.items():
                prices = [row["index_price"]] + forward_samples[:hours]
                outcome = forward_price_outcomes(prices, step_hours=1)
                row[f"next_{horizon}_realized_volatility_annualized_percent"] = \
                    outcome["realized_volatility_annualized_percent"]
                row[f"next_{horizon}_absolute_log_return_percent"] = outcome["absolute_log_return_percent"]
                row[f"next_{horizon}_maximum_price"] = outcome["maximum_price"]
                row[f"next_{horizon}_minimum_price"] = outcome["minimum_price"]
                row[f"next_{horizon}_final_price"] = (
                    prices[-1] if outcome["maximum_price"] is not None else None)
            row["trailing_1d_realized_volatility_annualized_percent"] = forward_price_outcomes(
                trailing, step_hours=1)["realized_volatility_annualized_percent"]

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(
        self, currency: str, first_date: date, last_date: date, output_directory: Path,
        primary_universe: str = EXPIRY_UNIVERSE_MONTHLY,
    ) -> Dict[str, Any]:
        """
        Build rows, outcomes and analysis; write ``{currency}_daily_levels.csv``,
        ``{currency}_daily_levels.json`` and ``{currency}_backtest_analysis.json``.

        Every universe in ``BACKTEST_UNIVERSES`` is computed and analysed
        (trend, predictive tests, alert table per universe), whatever
        ``primary_universe`` says.

        Args:
            primary_universe: A LABEL only: written to the JSON as
                ``primary_universe_label`` (which universe the reader should
                treat as the headline); it selects or changes no number.

        Returns:
            The analysis dict (also written to JSON).
        """
        started = clock.perf_counter()
        rows = self.build_daily_rows(currency, first_date, last_date)
        levels_seconds = clock.perf_counter() - started
        self.add_price_outcomes(currency, rows)
        outcomes_seconds = clock.perf_counter() - started - levels_seconds

        analysis: Dict[str, Any] = {
            "currency": currency,
            "first_date": first_date.isoformat(),
            "last_date": last_date.isoformat(),
            "primary_universe_label": primary_universe,
            "universes_analysed": list(BACKTEST_UNIVERSES),
            "primary_universe_label_note": ("label only: trend, predictive_tests and alerts are computed "
                                            "for every universe in universes_analysed"),
            "convention": ("as_of 23:59 UTC; spot = historical_trades.index_price of the newest trade at "
                           "or before as_of; open interest from the snapshot at or before 00:00 UTC of the "
                           "same date; IV from the snapshot at or before as_of (rebuilt from mark_price "
                           "where mark_iv is NULL); gamma at the index; levels = net-GEX argmax / argmin "
                           "and cumulative net-GEX argmin"),
            "coverage": self._coverage(rows),
            "trend": {universe: trend_summary(rows, universe) for universe in BACKTEST_UNIVERSES},
            "predictive_tests": {universe: predictive_tests(rows, universe) for universe in BACKTEST_UNIVERSES},
            "alerts": {universe: alert_threshold_table(rows, universe) for universe in BACKTEST_UNIVERSES},
        }
        analysis["test_count"] = sum(len(tests) for tests in analysis["predictive_tests"].values())
        analysis["runtime_seconds"] = {
            "levels": levels_seconds, "outcomes": outcomes_seconds,
            "total": clock.perf_counter() - started,
        }

        output_directory.mkdir(parents=True, exist_ok=True)
        stem = currency.lower()
        self._write_csv(rows, output_directory / f"{stem}_daily_levels.csv")
        (output_directory / f"{stem}_daily_levels.json").write_text(
            json.dumps(rows, indent=1, default=str), encoding="utf-8")
        (output_directory / f"{stem}_backtest_analysis.json").write_text(
            json.dumps(analysis, indent=1, default=str), encoding="utf-8")
        logger.info("Wrote %d daily rows and the analysis to %s (%.1f s)",
                    len(rows), output_directory, analysis["runtime_seconds"]["total"])
        return analysis

    @staticmethod
    def _coverage(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        computed = [row for row in rows if row["status"] == STATUS_COMPUTED]
        return {
            "dates": len(rows),
            "computed": len(computed),
            "not_computed": [{"date": row["date"], "reason": row["reason"]}
                             for row in rows if row["status"] != STATUS_COMPUTED],
            "days_with_rebuilt_iv": sum(1 for row in computed if row["iv_source_rebuilt_from_mark_price"]),
            "rows_with_missing_iv_total": sum(row["iv_source_missing"] for row in computed),
            "days_with_next_3d_outcome": sum(
                1 for row in rows if row.get("next_3d_realized_volatility_annualized_percent") is not None),
        }

    @staticmethod
    def _write_csv(rows: Sequence[Dict[str, Any]], path: Path) -> None:
        field_names: List[str] = []
        for row in rows:
            for key in row:
                if key not in field_names:
                    field_names.append(key)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=field_names)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: ("" if value is None else value) for key, value in row.items()})
