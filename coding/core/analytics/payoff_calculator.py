"""
Payoff-at-expiry calculations for defined-risk options structures (iron
condor, long call butterfly).

Wave G task G2-F fix 2: these two functions used to live in
coding/service/scanner/defined_risk_candidate_builder.py (Service layer).
coding/core/analytics/chart_generator.py (Core layer) imported them via a
function-level `from coding.service.scanner.defined_risk_candidate_builder
import ...` to plot the payoff curve -- a Core-imports-from-Service
dependency-inversion violation (Core is supposed to be the foundation
everything else depends on, not the reverse; see this repo's CLAUDE.md
Code Quality Checklist). Both functions are pure math (dict + float in,
float out, no API/DB access), so they belong in Core. Moved here;
defined_risk_candidate_builder.py now imports them from this module instead
of defining them, so its own existing callers (defined_risk_forward_test_harness.py,
tests/unit/test_defined_risk_candidate_builder.py, etc.) keep working
unchanged via that module's namespace.
"""
from typing import Dict


def iron_condor_payoff(candidate: Dict, settlement: float) -> float:
    """pnl in USD; caller divides by candidate['max_loss'] for return %, not cost_or_credit."""
    k1, k2, k3, k4 = candidate["short_call"], candidate["long_call"], candidate["short_put"], candidate["long_put"]
    call_spread_owed = max(settlement - k1, 0) - max(settlement - k2, 0)
    put_spread_owed = max(k3 - settlement, 0) - max(k4 - settlement, 0)
    return candidate["cost_or_credit"] - call_spread_owed - put_spread_owed


def butterfly_payoff(candidate: Dict, settlement: float) -> float:
    """pnl in USD; caller divides by candidate['cost_or_credit'] for return %."""
    k1, k2, k3 = candidate["k1"], candidate["k2"], candidate["k3"]
    payout = max(settlement - k1, 0) - 2 * max(settlement - k2, 0) + max(settlement - k3, 0)
    return payout - candidate["cost_or_credit"]
