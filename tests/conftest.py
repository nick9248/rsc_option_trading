"""
Pytest configuration and fixtures.
"""

import importlib
import sys
from datetime import datetime as _real_datetime
from pathlib import Path
from typing import Callable

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def pytest_addoption(parser):
    """
    ``--update-golden`` rewrites tests/golden/** from the current pipeline output
    instead of asserting against it. Used by the on-chain characterization suite
    (tests/characterization/) — see refactor_design_spec.md section 7.4.

    CI/pre-commit must reject a commit that touches tests/golden/** without a
    diff summary in the message (enforced by process/review, not by this flag).
    """
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="Rewrite characterization-test golden files instead of asserting against them.",
    )


@pytest.fixture
def update_golden(request) -> bool:
    """True when the suite was invoked with --update-golden."""
    return request.config.getoption("--update-golden")


# Modules where the on-chain pipeline reads "now" (see refactor_design_spec.md
# section 7.3 "Determinism", items 1-4, plus synthesis.py's report-header
# timestamp and DTE calc — a nondeterminism source the spec's list omitted,
# patched here per Task A2's brief: "if any nondeterminism source is NOT
# covered by the spec, patch it via injectable clock/parameters in the test
# fakes ... do NOT modify production behavior").
_FROZEN_CLOCK_MODULES = (
    "coding.core.analytics.on_chain_analyzer",
    "coding.core.analytics.buy_sell_flow_analyzer",
    "coding.core.analytics.market_wide_calculator",
    "coding.service.on_chain.on_chain_analysis_service",
    "coding.core.analytics.synthesis",
    # vrp_calculator.calculate_realized_volatility defaults reference_time to
    # datetime.now() when the caller doesn't pass one
    # (MarketWideCalculator.calculate_realized_volatility_multi_window doesn't) —
    # an undocumented nondeterminism source discovered during Task A4: the
    # golden master silently drifted (RV window boundaries shift) as real wall
    # clock time moved away from the fixture's recorded_at_epoch. Same remedy
    # this list already documents for the other 5 modules.
    "coding.core.analytics.vrp_calculator",
)


def apply_frozen_clock(monkeypatch_obj, epoch: float) -> _real_datetime:
    """
    Freeze ``datetime.now()`` (in the modules listed in _FROZEN_CLOCK_MODULES)
    and the global ``time.time()`` to a fixed epoch, using any monkeypatch-like
    object with a ``setattr(target, name, value)`` method — plain function
    (not a fixture) so both the function-scoped ``frozen_clock`` fixture below
    and characterization tests that need a module-scoped freeze (one pipeline
    run reused across several assertions) can share the exact same logic.

    Pass the exact epoch ``scripts/record_onchain_fixture.py`` anchored the
    fixture to, so every relative time window the pipeline recomputes at test
    time (24h flow lookback, 35d/180d/365d chart windows) is byte-identical
    to what was recorded — required for the fakes' exact-kwargs dict lookup
    to match.

    Returns the frozen ``datetime`` instance for convenience.
    """
    frozen_dt = _real_datetime.fromtimestamp(epoch)

    class _FrozenDateTime(_real_datetime):
        @classmethod
        def now(cls, tz=None):
            # bugfix_spec.md Item 5 (F5.3.1) has production code call
            # datetime.now(timezone.utc) explicitly. Honor a requested tz by
            # returning the correct aware instant for the same epoch, rather
            # than silently handing back the naive local frozen_dt (which
            # would crash any tz-aware-minus-naive subtraction downstream).
            if tz is not None:
                return _real_datetime.fromtimestamp(epoch, tz=tz)
            return frozen_dt

    for module_name in _FROZEN_CLOCK_MODULES:
        module = importlib.import_module(module_name)
        monkeypatch_obj.setattr(module, "datetime", _FrozenDateTime)

    # on_chain_analysis_service does `import time; time.time()` — patching
    # the real `time` module's `time` attribute affects every consumer
    # globally for the patch's duration (monkeypatch reverts automatically).
    time_module = importlib.import_module("time")
    monkeypatch_obj.setattr(time_module, "time", lambda: epoch)

    return frozen_dt


@pytest.fixture
def frozen_clock(monkeypatch) -> Callable[[float], _real_datetime]:
    """Function-scoped ``frozen_clock(epoch)`` fixture wrapping ``apply_frozen_clock``."""

    def _freeze(epoch: float) -> _real_datetime:
        return apply_frozen_clock(monkeypatch, epoch)

    return _freeze
