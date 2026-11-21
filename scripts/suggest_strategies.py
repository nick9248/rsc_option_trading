"""
Ask the model for a strategy per expiry from the facts the code prepared (research only: places no orders).

Usage (from repo root; needs the key as claude_api_key in .env; every call is paid, see the cost lines in the log):
    .venv\\Scripts\\python -m scripts.suggest_strategies --currency BTC --expiry 27NOV26 --dry-run
    .venv\\Scripts\\python -m scripts.suggest_strategies --currency BTC --expiry 27NOV26 --expiry 25DEC26 --yes

--dry-run builds the facts and shows them with their token count, and calls nothing that costs. The answers are stored in
strategy_suggestions (migration 038) for a later evaluation.
"""

import argparse
import logging
import os
from pathlib import Path
from typing import Optional, Sequence

import anthropic
from dotenv import load_dotenv

from coding.core.analytics.strategy_suggestion.expiry_facts import build_expiry_facts
from coding.core.database.repository import DatabaseRepository
from coding.core.logging.logging_setup import init_logging
from coding.service.deribit.deribit_api_service import DeribitApiService
from coding.service.on_chain.chain_source import CHAIN_SOURCE_STORED
from coding.service.on_chain.on_chain_analysis_service import OnChainAnalysisService
from coding.service.strategy.strategy_suggestion_service import (
    DEFAULT_MODEL, SYSTEM_PROMPT, BudgetExceeded, StrategySuggestionService)

logger = logging.getLogger(__name__)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the analysis, then ask the model about the chosen expiries."""
    parser = argparse.ArgumentParser(description="Strategy suggestions of the model per expiry (research only)")
    parser.add_argument("--currency", default="BTC", choices=["BTC", "ETH"])
    parser.add_argument("--expiry", action="append", required=True, help="expiry label such as 27NOV26 (repeat for several)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--dry-run", action="store_true", help="show the facts and their token count; no paid call")
    parser.add_argument("--yes", action="store_true", help="needed for more than one expiry in one run")
    arguments = parser.parse_args(argv)
    init_logging(task_name="suggest_strategies", log_to_file=False)
    load_dotenv(dotenv_path=Path(__file__).parents[1] / ".env")
    api_key = os.environ.get("claude_api_key")
    if not api_key:
        logger.error("claude_api_key is not set in .env")
        return 1
    if len(arguments.expiry) > 1 and not arguments.yes and not arguments.dry_run:
        logger.error("%d paid calls: add --yes to run them", len(arguments.expiry))
        return 1

    client = anthropic.Anthropic(api_key=api_key)
    repository = DatabaseRepository()
    with DeribitApiService(timeout=90) as api:
        service = OnChainAnalysisService(api, repository=repository, chain_source_mode=CHAIN_SOURCE_STORED)
        _, result = service.fetch_and_analyze(arguments.currency, return_result=True)

    if arguments.dry_run:
        for expiry in arguments.expiry:
            facts = build_expiry_facts(result, expiry)
            tokens = client.messages.count_tokens(
                model=arguments.model, system=SYSTEM_PROMPT, messages=[{"role": "user", "content": facts.text}]).input_tokens
            logger.info("%s: %d input tokens\n%s", expiry, tokens, facts.text)
        return 0

    suggestions = StrategySuggestionService(repository, client, model=arguments.model)
    for expiry in arguments.expiry:
        try:
            outcome = suggestions.suggest(result, expiry)
        except BudgetExceeded as error:
            logger.error("Budget reached: %s", error)
            return 1
        logger.info("%s: %s, cost %.4f USD\n%s", expiry, outcome.status, outcome.cost_usd, outcome.response_text)
        if outcome.pricing is not None:
            logger.info("Net premium at the quotes: %s", "not priceable: " + "; ".join(outcome.pricing.problems)
                        if outcome.pricing.net_premium_usd is None else f"{outcome.pricing.net_premium_usd:,.0f} USD (positive = paid)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
