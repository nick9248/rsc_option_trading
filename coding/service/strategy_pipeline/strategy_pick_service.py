"""
The strategy pipeline: facts and candidates built by code, one model call that chooses among them, the position sized from a loss budget, and
everything stored; later the choice is settled and compared with the baselines.

The same code runs live (as_of = now, the expiry not yet settled) and as a replay of a past instant (mode "replay": the chain, the facts and
the volatilities are those of that instant, and only the settlement price comes from the future, and only to evaluate). Every call is paid:
the total cost of all stored model calls is checked against a budget before each call. No order is ever placed.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

from coding.core.analytics.backtest.engine import simulate_trade
from coding.core.analytics.backtest.structures import Leg
from coding.core.analytics.candidates.distribution import LognormalDistribution
from coding.core.analytics.candidates.generator import CandidateLimits, generate_candidates
from coding.core.analytics.candidates.metrics import compute_metrics, metrics_at_venue_size
from coding.core.analytics.candidates.scoring import IMPLIED, REALISED
from coding.core.analytics.menthorq_replication_calculator import parse_expiry_settlement
from coding.core.analytics.risk.budget import RiskBudget, size_position
from coding.core.analytics.risk.venue import Venue
from coding.core.analytics.strategy_pipeline.facts import render_facts
from coding.core.analytics.strategy_pipeline.shortlist import (
    MAX_COST_FRACTION_OF_RISK, NO_TRADE, PICK_SCHEMA, SYSTEM_PROMPT, ShortlistItem, Pick, baseline_id, build_shortlist, candidate_record, parse_pick, render_shortlist)
from coding.core.database.repository import DatabaseRepository
from coding.service.backtest.backtest_service import MIN_SETTLEMENT_TRADES, BacktestService
from coding.service.strategy.strategy_suggestion_service import (
    DEFAULT_BUDGET_USD, DEFAULT_MODEL, EFFORT, MAX_TOKENS, PRICES_USD_PER_MILLION_TOKENS, WORST_CASE_CALL_USD, BudgetExceeded, call_cost_usd)
from coding.service.strategy_pipeline.facts_service import FactsAsOfService

logger = logging.getLogger(__name__)

METHOD_VERSION = "pipeline-3"
"""pipeline-1: the shortlist showed a flat-volatility "implied view" that ignored skew (the model read it as fair value and leaned to put structures);
pipeline-2: the market view is exact (minus costs);
pipeline-3: live picks are built for a venue (Derive: minimum order size, fees with the base fee per order, narrow spreads tried, size shown in the prompt)."""
VENUE_CANDIDATE_LIMITS = CandidateLimits(short_delta_range=(0.02, 0.60), long_delta_range=(0.02, 0.70),
                                         width_fractions=(0.004, 0.006, 0.008, 0.01, 0.015, 0.02, 0.03, 0.04, 0.06, 0.10))
"""With a venue the loss budget of a small account is a few USD: spreads from 0.4% of spot wide are tried too (the research default starts at 2%)."""
MODE_LIVE = "live"
MODE_REPLAY = "replay"
DEFAULT_RESEARCH_CAPITAL_USD = 10_000.0
"""The notional account of research runs (any fraction of a coin is allowed: results are in units of the loss, so they scale)."""


@dataclass(frozen=True)
class PickResult:
    """The outcome of one pick call."""

    row_id: Optional[int]
    status: str
    choice: Optional[str]
    pick: Optional[Pick]
    shortlist: List[ShortlistItem]
    quantity: Optional[float]
    cost_usd: float
    stored_before: bool
    """True when the instant had been picked already: nothing was paid and the stored row was returned."""
    expiration: Optional[str] = None


class StrategyPickService:
    """Builds the shortlist for one expiry at one instant, asks the model, stores the answer, and later evaluates it."""

    def __init__(self, repository: DatabaseRepository, client: Any, risk_budget: RiskBudget = RiskBudget(DEFAULT_RESEARCH_CAPITAL_USD),
                 model: str = DEFAULT_MODEL, budget_usd: float = DEFAULT_BUDGET_USD, venues: Optional[Mapping[str, Venue]] = None):
        """
        Args:
            repository: Local database (picks and the running cost are stored there).
            client: An ``anthropic.Anthropic`` client (a fake in tests).
            risk_budget: The account the position is sized for; any fraction of a coin is allowed (Derive trades fractions).
            model: Model id; must have a price in ``PRICES_USD_PER_MILLION_TOKENS``.
            budget_usd: Stop when the stored model calls have cost this much.
            venues: Per currency, where positions are placed. Used for LIVE picks only: candidates the venue cannot place within the loss budget
                are dropped, fees are the venue's, and the size is the venue's allowed one. None (and every replay): fractions of a coin, Deribit fees.

        Raises:
            ValueError: for a model without a known price.
        """
        if model not in PRICES_USD_PER_MILLION_TOKENS:
            raise ValueError(f"no price known for model {model!r}")
        self.repository = repository
        self.client = client
        self.risk_budget = risk_budget
        self.model = model
        self.budget_usd = budget_usd
        self.venues = dict(venues or {})
        self.backtest = BacktestService(repository)
        self.facts_service = FactsAsOfService(repository)

    def _entry_for(self, currency: str, as_of: datetime, mode: str, target_days: float, minimum_days: float, expiration: Optional[str],
                   now: datetime):
        """(entry, "") or (None, reason): the chain of the expiry at the instant (the cheap part: no facts, no candidates yet)."""
        return self.backtest.load_entry(currency, as_of, target_days, minimum_days, now, settled=(mode == MODE_REPLAY), expiration=expiration)

    def _shortlist_for(self, currency: str, as_of: datetime, entry, now: datetime, mode: str = MODE_REPLAY):
        """(facts, shortlist), or (reason, None) when the volatility needed for the view is missing."""
        facts = self.facts_service.build(currency, entry.expiration, as_of, entry.spot, entry.forward)
        days = (entry.settlement_at - entry.captured_at).total_seconds() / 86400.0
        distributions: Dict[str, LognormalDistribution] = {}
        if facts.realised_vol_30d:
            distributions[REALISED] = LognormalDistribution(entry.forward, facts.realised_vol_30d, days)
        if entry.atm_implied_volatility:
            distributions[IMPLIED] = LognormalDistribution(entry.forward, entry.atm_implied_volatility, days)
        if REALISED not in distributions:
            return "no realised volatility", None
        venue = self.venues.get(currency) if mode == MODE_LIVE else None
        if venue is None:
            scored = [(candidate, compute_metrics(candidate, entry.spot, distributions)) for candidate in generate_candidates(entry.quotes, entry.spot)]
        else:
            scored = []
            for candidate in generate_candidates(entry.quotes, entry.spot, VENUE_CANDIDATE_LIMITS):
                metrics = metrics_at_venue_size(candidate, entry.spot, distributions, venue, self.risk_budget.risk_per_trade_usd)
                if metrics is not None and metrics.by_distribution[REALISED].expected_pnl_usd > 0:     # nothing worth asking the model about otherwise
                    scored.append((candidate, metrics))
        return facts, build_shortlist(scored)

    def pick(self, currency: str, as_of: datetime, mode: str = MODE_REPLAY, target_days_to_expiry: float = 7.0, minimum_days_to_expiry: float = 2.0,
             expiration: Optional[str] = None, now_utc: Optional[datetime] = None) -> Optional[PickResult]:
        """
        Ask the model for one expiry at one instant (a stored answer for that instant is returned without a call).

        Args:
            currency: "BTC" or "ETH".
            as_of: The instant (timezone-aware UTC): the newest stored chain at or before it, fresh, is used; nothing newer.
            mode: ``MODE_REPLAY`` (a past instant whose expiry has settled) or ``MODE_LIVE``.
            target_days_to_expiry: The expiry nearest this many days away is used unless ``expiration`` is given.
            minimum_days_to_expiry: Expiries closer than this are not used.
            expiration: A given expiry label.
            now_utc: The current instant.

        Returns:
            The result, or None when the instant cannot be used (no fresh snapshot, no expiry, no volatility ...; logged with the reason).

        Raises:
            BudgetExceeded: before the call, when the stored cost plus the reserved worst case would pass the budget.
            ValueError: for a naive ``as_of`` or an unknown mode.
        """
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware (UTC)")
        if mode not in (MODE_LIVE, MODE_REPLAY):
            raise ValueError(f"unknown mode {mode!r}")
        now = now_utc or datetime.now(timezone.utc)
        entry, reason = self._entry_for(currency, as_of, mode, target_days_to_expiry, minimum_days_to_expiry, expiration, now)
        if entry is None:
            logger.info("%s %s: not usable (%s)", currency, as_of.isoformat(), reason)
            return None
        # Never pay twice: a replay instant is stored once; a live pick is not repeated within an hour for the same expiry and chain.
        stored = (self.repository.get_strategy_picks(currency, mode=mode, expiration=entry.expiration, as_of=as_of, method_version=METHOD_VERSION)
                  if mode == MODE_REPLAY else
                  self.repository.get_strategy_picks(currency, mode=mode, expiration=entry.expiration, as_of_since=as_of - timedelta(hours=1),
                                                     method_version=METHOD_VERSION))
        if stored:
            row = stored[-1]
            return PickResult(row["id"], row["status"], row["choice"], None, [], row["quantity"], 0.0, True, entry.expiration)
        facts, shortlist = self._shortlist_for(currency, as_of, entry, now, mode)
        if shortlist is None:
            logger.info("%s %s: not usable (%s)", currency, as_of.isoformat(), facts)
            return None
        facts_text = render_facts(facts)
        record = [candidate_record(item) for item in shortlist]
        if not shortlist:
            row_id = self._save(currency, entry.expiration, mode, as_of, now, entry.spot, "no_candidates", facts_text, record, None, None, "",
                                None, None, 0, 0, 0, 0, 0.0, None, None)
            return PickResult(row_id, "no_candidates", None, None, [], None, 0.0, False, entry.expiration)

        spent = self.repository.get_total_model_spend()
        if spent + WORST_CASE_CALL_USD > self.budget_usd:
            raise BudgetExceeded(f"stored model calls cost {spent:.2f} USD; budget {self.budget_usd:.2f} USD, "
                                 f"{WORST_CASE_CALL_USD:.2f} USD reserved per call")
        logger.info("Asking %s for %s %s at %s (spent so far %.2f of %.2f USD)", self.model, currency, entry.expiration, as_of.isoformat(), spent,
                    self.budget_usd)
        response = self.client.messages.create(
            model=self.model, max_tokens=MAX_TOKENS,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": facts_text + "\n\n" + render_shortlist(shortlist) + "\n\nChoose one candidate or no_trade."}],
            output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": PICK_SCHEMA}})
        usage = response.usage                                   # the call is paid from here on: a row with its cost is always stored
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cost = call_cost_usd(self.model, usage.input_tokens, usage.output_tokens, cache_read, cache_write)
        text = ""
        parsed: Optional[Pick] = None
        quantity = max_loss = None
        try:
            text = "".join(getattr(block, "text", "") for block in response.content if block.type == "text")
            if response.stop_reason == "refusal":
                status = "refused"
            elif response.stop_reason == "max_tokens":
                status = "invalid_response"
            else:
                parsed = parse_pick(text, [item.candidate_id for item in shortlist])
                status = "no_trade" if parsed.choice == NO_TRADE else "ok"
                if parsed.choice != NO_TRADE:
                    chosen = next(item for item in shortlist if item.candidate_id == parsed.choice)
                    if chosen.metrics.quantity is not None:          # sized for the venue already (its minimum, step and fees)
                        quantity, max_loss = chosen.metrics.quantity, chosen.metrics.quantity * chosen.metrics.max_loss_usd
                    else:
                        sized = size_position(chosen.metrics.max_loss_usd, self.risk_budget)
                        quantity, max_loss = sized.quantity, sized.max_loss_usd
        except ValueError as error:
            status = "invalid_response"
            logger.warning("The answer of the model is not usable: %s", error)
        except Exception as error:                               # an unexpected shape of the response: keep the paid answer and the cost
            status = "invalid_response"
            text = text or repr(response)
            logger.exception("Unexpected response: %s", error)
        try:
            row_id = self._save(currency, entry.expiration, mode, as_of, now, entry.spot, status, facts_text, record,
                                None if parsed is None else parsed.choice, None if parsed is None else list(parsed.ranking), text, quantity,
                                max_loss, usage.input_tokens, usage.output_tokens, cache_read, cache_write, cost,
                                getattr(response, "_request_id", None), baseline_id(shortlist))
        except Exception:
            logger.error("The paid answer could not be stored (cost %.4f USD, request %s): %s", cost, getattr(response, "_request_id", None), text)
            raise
        logger.info("Pick %s stored: %s %s, cost %.4f USD", row_id, status, None if parsed is None else parsed.choice, cost)
        return PickResult(row_id, status, None if parsed is None else parsed.choice, parsed, shortlist, quantity, cost, False, entry.expiration)

    def _save(self, currency, expiration, mode, as_of, now, spot, status, facts_text, record, choice, ranking, text, quantity, max_loss,
              input_tokens, output_tokens, cache_read, cache_write, cost, request_id, baseline) -> Optional[int]:
        return self.repository.save_strategy_pick({
            "currency": currency, "expiration": expiration, "mode": mode, "method_version": METHOD_VERSION, "as_of": as_of, "computed_at": now, "spot": spot, "model": self.model,
            "status": status, "facts": facts_text, "candidates": record, "choice": choice, "ranking": ranking, "response": text,
            "quantity": quantity, "max_loss_usd": max_loss, "risk_per_trade_usd": self.risk_budget.risk_per_trade_usd,
            "input_tokens": input_tokens, "output_tokens": output_tokens, "cache_read_tokens": cache_read, "cache_write_tokens": cache_write,
            "cost_usd": cost, "request_id": request_id, "baseline_id": baseline})

    def evaluate(self, row: Dict[str, Any], now_utc: Optional[datetime] = None) -> bool:
        """
        Settle a stored pick and store what its candidates really earned.

        What is compared: the chosen candidate, the baseline's top candidate, the mean of the shortlist and the mean of ALL candidates of that
        instant (regenerated from the stored snapshot with the same code), each in units of its maximum loss.

        Returns:
            True when the evaluation was stored; False when the expiry has not settled yet, its delivery price is not known, or the pick
            has no choice to settle.
        """
        now = now_utc or datetime.now(timezone.utc)
        if row["evaluated_at"] is not None or row["status"] not in ("ok", "no_trade"):
            return False
        settlement_at = parse_expiry_settlement(row["expiration"])
        if settlement_at is None or settlement_at > now:
            return False
        settlement = self.repository.get_settlement_index_price(row["currency"], settlement_at)
        if settlement is None or settlement[1] < MIN_SETTLEMENT_TRADES:
            return False
        outcomes: Dict[str, Any] = {}
        for item in row["candidates"]:
            legs = [Leg(leg["action"], leg["option_type"], leg["strike"], leg["instrument"], leg["entry_price_usd"]) for leg in item["legs"]]
            trade = simulate_trade(item["family"], row["expiration"], row["as_of"], settlement_at, row["spot"], legs, settlement[0])
            outcomes[item["id"]] = {"pnl_usd": trade.pnl_usd, "r": trade.pnl_usd / item["max_loss_usd"]}
        outcomes["_shortlist_mean_r"] = sum(value["r"] for value in outcomes.values()) / len(row["candidates"])
        outcomes["_all_mean_r"] = self._all_candidates_mean_r(row, settlement_at, settlement[0])
        if outcomes["_all_mean_r"] is None:
            return False
        return self.repository.save_strategy_pick_evaluation(row["id"], now, settlement[0], row["baseline_id"], outcomes)

    def _all_candidates_mean_r(self, row: Dict[str, Any], settlement_at: datetime, settlement_price: float) -> Optional[float]:
        """
        Mean R of every candidate the chain of that instant offered that passes the same cost filter as the shortlist (regenerated from the
        stored snapshot at the stored instant, priced at the stored spot).
        """
        entry, reason = self.backtest.load_entry(row["currency"], row["as_of"], 7.0, 2.0, datetime.now(timezone.utc), settled=False,
                                                 expiration=row["expiration"])
        if entry is None:
            logger.warning("Cannot regenerate the candidates of pick %s: %s", row["id"], reason)
            return None
        values: List[float] = []
        for candidate in generate_candidates(entry.quotes, entry.spot):
            metrics = compute_metrics(candidate, row["spot"], {})
            if metrics.max_loss_usd <= 0 or (metrics.spread_cost_usd + metrics.trading_fees_usd) > MAX_COST_FRACTION_OF_RISK * metrics.max_loss_usd:
                continue
            trade = simulate_trade(candidate.family, row["expiration"], row["as_of"], settlement_at, row["spot"], candidate.legs, settlement_price)
            values.append(trade.pnl_usd / metrics.max_loss_usd)
        return sum(values) / len(values) if values else None
