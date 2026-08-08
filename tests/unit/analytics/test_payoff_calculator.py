"""
Tests for coding.core.analytics.payoff_calculator (Wave G task G2-F fix 2).

iron_condor_payoff/butterfly_payoff moved here from
coding.service.scanner.defined_risk_candidate_builder (a Core-imports-from-
Service dependency-inversion violation: chart_generator.py, in Core, was
importing these pure math functions from Service). These tests prove the
functions work correctly from their new Core location -- same hand-verified
numbers as the pre-move tests in tests/unit/test_defined_risk_candidate_builder.py,
re-derived independently here rather than copy-pasted, plus a regression
guard that chart_generator.py's payoff-chart imports no longer reach into
coding.service at all.
"""
import ast
from pathlib import Path

from coding.core.analytics.payoff_calculator import butterfly_payoff, iron_condor_payoff

CHART_GENERATOR_PATH = (
    Path(__file__).parents[3] / "coding" / "core" / "analytics" / "chart_generator.py"
)


class TestIronCondorPayoff:
    def test_full_win_between_short_strikes(self):
        # Settlement between the short strikes -> both spreads worthless -> keep full credit.
        candidate = {
            "short_call": 70000, "long_call": 72000,
            "short_put": 58000, "long_put": 56000,
            "cost_or_credit": 500.0,
        }
        assert iron_condor_payoff(candidate, settlement=65000.0) == 500.0

    def test_full_loss_beyond_long_call(self):
        # Settlement beyond the long call -> call spread owed = full wing width (2000).
        candidate = {
            "short_call": 70000, "long_call": 72000,
            "short_put": 58000, "long_put": 56000,
            "cost_or_credit": 500.0,
        }
        pnl = iron_condor_payoff(candidate, settlement=80000.0)
        assert pnl == 500.0 - 2000.0  # = -1500.0 == -(wing_width - credit) == -max_loss

    def test_full_loss_beyond_long_put(self):
        # Symmetric case on the downside: settlement beyond the long put.
        candidate = {
            "short_call": 70000, "long_call": 72000,
            "short_put": 58000, "long_put": 56000,
            "cost_or_credit": 500.0,
        }
        pnl = iron_condor_payoff(candidate, settlement=50000.0)
        assert pnl == 500.0 - 2000.0  # = -1500.0, same max_loss as the call-side breach


class TestButterflyPayoff:
    def test_max_profit_at_mid_strike(self):
        candidate = {"k1": 63000, "k2": 65000, "k3": 67000, "cost_or_credit": 400.0}
        pnl = butterfly_payoff(candidate, settlement=65000.0)
        assert pnl == (65000 - 63000) - 400.0  # = 1600.0

    def test_full_loss_below_k1(self):
        candidate = {"k1": 63000, "k2": 65000, "k3": 67000, "cost_or_credit": 400.0}
        pnl = butterfly_payoff(candidate, settlement=60000.0)
        assert pnl == -400.0  # all legs worthless -> lose the full debit

    def test_full_loss_above_k3(self):
        candidate = {"k1": 63000, "k2": 65000, "k3": 67000, "cost_or_credit": 400.0}
        pnl = butterfly_payoff(candidate, settlement=70000.0)
        assert pnl == -400.0  # symmetric: all legs cancel above k3, lose the full debit


class TestChartGeneratorDoesNotImportService:
    def test_chart_generator_has_no_service_layer_import(self):
        """
        Regression guard for the fix: chart_generator.py (Core) must not
        import anything from coding.service (Core -> Service is a
        dependency-inversion violation per this repo's CLAUDE.md layering
        rule). Parses the actual AST rather than grepping so both
        `import coding.service...` and `from coding.service... import ...`
        (module-level or function-level, anywhere in the file) are caught.
        """
        source = CHART_GENERATOR_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(CHART_GENERATOR_PATH))

        service_imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("coding.service"):
                        service_imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("coding.service"):
                    service_imports.append(node.module)

        assert service_imports == [], (
            f"chart_generator.py (Core) imports from Service: {service_imports}"
        )
