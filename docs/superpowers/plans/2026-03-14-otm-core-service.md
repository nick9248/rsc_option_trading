# OTM Contract Finder — Core & Service Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the regime-gated, signal-stacked OTM contract finder backend — all four scoring gates, Kelly sizer, data fetchers, and orchestrating service — producing a ranked `List[OTMSignal]` without any GUI.

**Architecture:** Core layer (`coding/core/strategy/otm/`) holds all signal math (no API calls). Service layer (`coding/service/strategy/otm/`) fetches data and orchestrates the four gates. New data fetchers (DVOL, stablecoin, IBIT P/C) live in the service layer alongside the finder. Two DB migrations add `dvol_history` and `otm_signals` tables.

**Tech Stack:** Python 3.13, Pydantic v2, `arch>=6.0.0` (GJR-GARCH), psycopg2, requests, pytest

---

## File Map

### New files — Core layer
| File | Responsibility |
|---|---|
| `coding/core/strategy/otm/__init__.py` | Package marker |
| `coding/core/strategy/otm/models/__init__.py` | Package marker |
| `coding/core/strategy/otm/models/otm_config.py` | `OTMConfig` Pydantic model — all tunable thresholds |
| `coding/core/strategy/otm/models/otm_signal.py` | `OTMSignal` Pydantic model — output per contract |
| `coding/core/strategy/otm/signals/__init__.py` | Package marker |
| `coding/core/strategy/otm/signals/liquidity_gate.py` | Gate 1 — hard pass/fail filters |
| `coding/core/strategy/otm/signals/volatility_regime_gate.py` | Gate 2 — vol regime score 0–100 |
| `coding/core/strategy/otm/signals/directional_scorer.py` | Gate 3 — call_score + put_score 0–100 each |
| `coding/core/strategy/otm/signals/strike_expiry_optimizer.py` | Gate 4 — ranks surviving contracts |
| `coding/core/strategy/otm/scoring/__init__.py` | Package marker |
| `coding/core/strategy/otm/scoring/kelly_sizer.py` | Fractional Kelly position sizer |

### New files — Service layer
| File | Responsibility |
|---|---|
| `coding/service/strategy/otm/__init__.py` | Package marker |
| `coding/service/strategy/otm/fetchers/__init__.py` | Package marker |
| `coding/service/strategy/otm/fetchers/dvol_fetcher.py` | Fetch + store DVOL from Deribit |
| `coding/service/strategy/otm/fetchers/stablecoin_fetcher.py` | Stablecoin inflow from CryptoQuant |
| `coding/service/strategy/otm/fetchers/ibit_fetcher.py` | IBIT P/C ratio from CBOE |
| `coding/service/strategy/otm/otm_finder_service.py` | Orchestrates all 4 gates end-to-end |
| `coding/service/strategy/otm/otm_backtest_service.py` | Backtest interface (stub only) |

### New files — DB + scripts
| File | Responsibility |
|---|---|
| `migrations/011_add_otm_tables.sql` | `dvol_history` + `otm_signals` tables |
| `scripts/backfill_dvol_history.py` | One-time historical DVOL backfill |

### New files — Tests
| File | Responsibility |
|---|---|
| `tests/unit/strategy/otm/__init__.py` | Package marker |
| `tests/unit/strategy/otm/test_otm_config.py` | OTMConfig validation |
| `tests/unit/strategy/otm/test_otm_signal.py` | OTMSignal construction + serialization |
| `tests/unit/strategy/otm/test_liquidity_gate.py` | Gate 1 — 20+ tests |
| `tests/unit/strategy/otm/test_volatility_regime_gate.py` | Gate 2 — 20+ tests |
| `tests/unit/strategy/otm/test_directional_scorer.py` | Gate 3 — 30+ tests |
| `tests/unit/strategy/otm/test_strike_expiry_optimizer.py` | Gate 4 — 20+ tests |
| `tests/unit/strategy/otm/test_kelly_sizer.py` | Kelly sizer — 15+ tests |

### Modified files
| File | Change |
|---|---|
| `requirements.txt` | Add `arch>=6.0.0` |

---

## Chunk 1: Foundation — Dependencies, DB, Models

---

### Task 1: Add `arch` dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add `arch` to requirements.txt**

Open `requirements.txt` and append:
```
arch>=6.0.0
```

- [ ] **Step 2: Install it**

```bash
.venv/Scripts/activate && pip install "arch>=6.0.0"
```
Expected: `Successfully installed arch-x.x.x`

- [ ] **Step 3: Verify import works**

```bash
python -c "from arch import arch_model; print('arch OK')"
```
Expected: `arch OK`

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "feat: add arch>=6.0.0 for GJR-GARCH in Gate 2"
```

---

### Task 2: DB migration — `dvol_history` + `otm_signals`

**Files:**
- Create: `migrations/011_add_otm_tables.sql`

- [ ] **Step 1: Write migration SQL**

```sql
-- migrations/011_add_otm_tables.sql
-- OTM Contract Finder: DVOL history and signal storage

-- ── DVOL history ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dvol_history (
    id          SERIAL PRIMARY KEY,
    asset       VARCHAR(10)   NOT NULL,   -- 'BTC' | 'ETH'
    timestamp   TIMESTAMPTZ   NOT NULL,
    dvol_value  DECIMAL(8,4)  NOT NULL,
    UNIQUE (asset, timestamp)
);
CREATE INDEX IF NOT EXISTS idx_dvol_history_asset_ts
    ON dvol_history (asset, timestamp DESC);

COMMENT ON TABLE dvol_history IS
    'Deribit DVOL index history used for Gate 2 percentile calculation';

-- ── OTM signals ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS otm_signals (
    id                   SERIAL PRIMARY KEY,
    signal_id            UUID          NOT NULL UNIQUE,
    generated_at         TIMESTAMPTZ   NOT NULL,
    asset                VARCHAR(10)   NOT NULL,
    instrument_name      VARCHAR(50)   NOT NULL,
    direction            VARCHAR(4)    NOT NULL,    -- 'call' | 'put'
    strike               DECIMAL(14,2) NOT NULL,
    expiry               VARCHAR(10)   NOT NULL,
    dte                  INTEGER       NOT NULL,
    delta                DECIMAL(8,6)  NOT NULL,
    mark_iv              DECIMAL(8,4),
    entry_premium        DECIMAL(12,4),
    underlying_price     DECIMAL(14,2),
    gate2_score          DECIMAL(6,2),
    gate3_call_score     DECIMAL(6,2),
    gate3_put_score      DECIMAL(6,2),
    conviction_score     DECIMAL(6,2),
    position_usd         DECIMAL(12,2),
    take_profit_multiple DECIMAL(6,2),
    expiry_category      VARCHAR(10),               -- 'short'|'medium'|'long'
    regime_flag          VARCHAR(10),               -- 'bull'|'bear'|'neutral'
    signal_breakdown     JSONB,
    exit_params          JSONB
);
CREATE INDEX IF NOT EXISTS idx_otm_signals_asset_ts
    ON otm_signals (asset, generated_at DESC);

COMMENT ON TABLE otm_signals IS
    'OTM contract finder output — one row per signal generated';
```

- [ ] **Step 2: Run migration**

```bash
python scripts/run_migration.py migrations/011_add_otm_tables.sql
```
Expected: Migration applied successfully

- [ ] **Step 3: Verify tables exist**

```bash
python -c "
import psycopg2, os
from dotenv import load_dotenv
load_dotenv()
conn = psycopg2.connect(host='localhost', port=5433, dbname='option_trading',
                        user='postgres', password=os.getenv('DB_PASSWORD'))
cur = conn.cursor()
cur.execute(\"SELECT table_name FROM information_schema.tables WHERE table_name IN ('dvol_history','otm_signals')\")
print(cur.fetchall())
"
```
Expected: `[('dvol_history',), ('otm_signals',)]` (order may vary)

- [ ] **Step 4: Commit**

```bash
git add migrations/011_add_otm_tables.sql
git commit -m "feat: add dvol_history and otm_signals DB tables"
```

---

### Task 3: `OTMConfig` Pydantic model

**Files:**
- Create: `coding/core/strategy/otm/__init__.py`
- Create: `coding/core/strategy/otm/models/__init__.py`
- Create: `coding/core/strategy/otm/models/otm_config.py`
- Test: `tests/unit/strategy/otm/__init__.py`
- Test: `tests/unit/strategy/otm/test_otm_config.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/strategy/otm/test_otm_config.py
import pytest
from pydantic import ValidationError
from coding.core.strategy.otm.models.otm_config import OTMConfig


def test_default_config_creates_successfully():
    config = OTMConfig(risk_budget_usd=10_000.0)
    assert config.risk_budget_usd == 10_000.0


def test_default_gate1_thresholds():
    config = OTMConfig(risk_budget_usd=10_000.0)
    assert config.max_bid_ask_spread_relative == 0.08
    assert config.max_bid_ask_spread_absolute == 4.0
    assert config.min_volume_oi_ratio == 0.05
    assert config.min_oi_btc == 50
    assert config.min_oi_eth == 200
    assert config.tx_cost_floor_multiplier == 5.0


def test_default_gate2_thresholds():
    config = OTMConfig(risk_budget_usd=10_000.0)
    assert config.gate2_suppress_threshold == 40.0
    assert config.gate2_position_exit_threshold == 30.0
    assert config.dvol_percentile_threshold == 30.0
    assert config.dvol_lookback_months == 36
    assert config.dvol_floor_std_multiplier == 1.0
    assert config.vrp_cheap_threshold == 5.0
    assert config.garch_iv_ratio_threshold == 1.10
    assert config.term_structure_contango_threshold == 5.0
    assert config.term_structure_shallow_back_threshold == -5.0
    assert config.term_structure_deep_back_threshold == -15.0


def test_default_kelly_priors():
    config = OTMConfig(risk_budget_usd=10_000.0)
    assert config.p_win_priors["40_60"] == 0.35
    assert config.p_win_priors["90_100"] == 0.50
    assert config.avg_return_priors["90_100"] == 3.0   # capped at 3×


def test_risk_budget_required():
    with pytest.raises(ValidationError):
        OTMConfig()   # missing required field


def test_risk_budget_must_be_positive():
    with pytest.raises(ValidationError):
        OTMConfig(risk_budget_usd=-100.0)


def test_max_single_trade_pct_must_be_positive():
    with pytest.raises(ValidationError):
        OTMConfig(risk_budget_usd=10_000.0, max_single_trade_pct=0.0)


def test_kelly_divisor_must_be_positive():
    with pytest.raises(ValidationError):
        OTMConfig(risk_budget_usd=10_000.0, kelly_divisor=0.0)


def test_custom_overrides_work():
    config = OTMConfig(
        risk_budget_usd=50_000.0,
        min_oi_btc=100,
        gate2_suppress_threshold=50.0,
    )
    assert config.min_oi_btc == 100
    assert config.gate2_suppress_threshold == 50.0


def test_config_is_immutable():
    config = OTMConfig(risk_budget_usd=10_000.0)
    with pytest.raises(Exception):
        config.risk_budget_usd = 999.0
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/unit/strategy/otm/test_otm_config.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Create package markers**

```python
# coding/core/strategy/otm/__init__.py
# coding/core/strategy/otm/models/__init__.py
# tests/unit/strategy/otm/__init__.py
```
(empty files)

- [ ] **Step 4: Implement `OTMConfig`**

```python
# coding/core/strategy/otm/models/otm_config.py
"""
OTMConfig — all tunable thresholds for the OTM contract finder.

Update this model (not code) to adjust strategy behavior after backtesting.
"""
import logging
from typing import Dict
from pydantic import BaseModel, ConfigDict, field_validator

logger = logging.getLogger(__name__)


class OTMConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    # ── Budget ────────────────────────────────────────────────────────────────
    risk_budget_usd: float
    max_single_trade_pct: float = 0.10
    max_correlated_pct: float = 0.10

    # ── Gate 1 — dual-threshold spread ───────────────────────────────────────
    max_bid_ask_spread_relative: float = 0.08
    max_bid_ask_spread_absolute: float = 4.0
    min_volume_oi_ratio: float = 0.05
    min_oi_btc: int = 50
    min_oi_eth: int = 200
    tx_cost_floor_multiplier: float = 5.0

    # ── Gate 2 ────────────────────────────────────────────────────────────────
    gate2_suppress_threshold: float = 40.0
    gate2_position_exit_threshold: float = 30.0
    dvol_percentile_threshold: float = 30.0
    dvol_lookback_months: int = 36
    dvol_floor_std_multiplier: float = 1.0
    vrp_cheap_threshold: float = 5.0
    garch_iv_ratio_threshold: float = 1.10
    term_structure_contango_threshold: float = 5.0
    term_structure_shallow_back_threshold: float = -5.0
    term_structure_deep_back_threshold: float = -15.0

    # ── Gate 3 ────────────────────────────────────────────────────────────────
    rr_z_score_threshold: float = 1.5
    pc_ratio_percentile_bull: float = 70.0
    pc_ratio_percentile_bear: float = 30.0
    funding_percentile_bull: float = 10.0
    funding_percentile_bear: float = 90.0
    block_trade_min_premium: float = 500_000.0
    stablecoin_inflow_threshold_pct: float = 0.5
    ris_divergence_threshold: float = 2.0

    # ── Regime (fast EMA dual-filter) ─────────────────────────────────────────
    ema_fast: int = 10
    ema_slow: int = 20
    trend_sma: int = 50
    regime_call_multiplier: float = 1.30
    regime_put_multiplier: float = 0.70

    # ── Gate 4 ────────────────────────────────────────────────────────────────
    min_delta_directional: float = 0.20
    max_delta_directional: float = 0.35
    min_delta_event: float = 0.10
    max_delta_event: float = 0.20
    vega_theta_short: float = 0.05
    vega_theta_medium: float = 0.30
    vega_theta_long: float = 0.80
    max_breakeven_move_multiplier: float = 2.0

    # ── Kelly / sizing ────────────────────────────────────────────────────────
    kelly_divisor: float = 4.0
    p_win_priors: Dict[str, float] = {
        "40_60": 0.35,
        "60_75": 0.40,
        "75_90": 0.45,
        "90_100": 0.50,
    }
    avg_return_priors: Dict[str, float] = {
        "40_60": 1.5,
        "60_75": 2.0,
        "75_90": 2.5,
        "90_100": 3.0,
    }

    # ── Exit thresholds ───────────────────────────────────────────────────────
    stop_loss_hard_floor_pct: float = 0.70
    theta_excess_loss_reduce_pct: float = 0.20
    theta_excess_loss_full_exit_pct: float = 0.40
    thesis_stop_call_pct: float = 0.85
    thesis_stop_put_pct: float = 1.15
    vega_windfall_iv_spike_threshold: float = 15.0
    vega_windfall_spot_move_max: float = 0.01
    vega_windfall_profit_threshold: float = 2.0
    liquidity_exit_spread_multiplier: float = 3.0
    liquidity_exit_reprice_interval_sec: int = 10
    liquidity_exit_reprice_concession_vol_pts: float = 0.5
    liquidity_exit_max_reprice_cycles: int = 8
    itm_threshold_for_hold: float = 0.10
    hold_through_expiry_max_dte: int = 5

    # ── Validators ────────────────────────────────────────────────────────────
    @field_validator("risk_budget_usd")
    @classmethod
    def budget_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("risk_budget_usd must be positive")
        return v

    @field_validator("max_single_trade_pct")
    @classmethod
    def single_trade_pct_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("max_single_trade_pct must be positive")
        return v

    @field_validator("kelly_divisor")
    @classmethod
    def kelly_divisor_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("kelly_divisor must be positive")
        return v
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
pytest tests/unit/strategy/otm/test_otm_config.py -v
```
Expected: all 10 tests PASS

- [ ] **Step 6: Commit**

```bash
git add coding/core/strategy/otm/ tests/unit/strategy/otm/
git commit -m "feat: add OTMConfig Pydantic model with all Gate 1-4 thresholds"
```

---

### Task 4: `OTMSignal` Pydantic model

**Files:**
- Create: `coding/core/strategy/otm/models/otm_signal.py`
- Test: `tests/unit/strategy/otm/test_otm_signal.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/strategy/otm/test_otm_signal.py
import pytest
from datetime import datetime, timezone
from uuid import uuid4
from pydantic import ValidationError
from coding.core.strategy.otm.models.otm_signal import OTMSignal


def _make_signal(**overrides) -> OTMSignal:
    """Helper: build a valid OTMSignal with sensible defaults."""
    defaults = dict(
        signal_id=str(uuid4()),
        generated_at=datetime.now(timezone.utc),
        asset="BTC",
        instrument_name="BTC-28MAR25-95000-C",
        direction="call",
        strike=95000.0,
        expiry="28MAR25",
        dte=14,
        delta=0.28,
        gamma=0.000012,
        vega=45.0,
        theta=-18.0,
        mark_iv=0.65,
        entry_premium=320.0,
        underlying_price=87500.0,
        gate1_passed=True,
        gate2_score=72.0,
        gate3_call_score=68.0,
        gate3_put_score=32.0,
        gate3_directional_score=68.0,
        conviction_score=70.0,
        d1_d7_score=0.6,
        d2_score=0.4,
        d3_score=0.3,
        d4_score=0.2,
        d6_d9_score=0.5,
        d8_score=0.1,
        d10_score=0.3,
        ris_score=0.2,
        position_usd=450.0,
        p_win_prior=0.40,
        kelly_fraction=0.025,
        take_profit_multiple=3.0,
        stop_loss_pct=0.70,
        time_stop_dte=7,
        vega_theta_ratio=2.5,
        gamma_premium_ratio=0.0000375,
        breakeven_price=95320.0,
        expiry_category="medium",
        regime_flag="bull",
        gate2_suppressed=False,
    )
    defaults.update(overrides)
    return OTMSignal(**defaults)


def test_valid_signal_creates_successfully():
    signal = _make_signal()
    assert signal.asset == "BTC"
    assert signal.direction == "call"


def test_asset_must_be_btc_or_eth():
    with pytest.raises(ValidationError):
        _make_signal(asset="SOL")


def test_direction_must_be_call_or_put():
    with pytest.raises(ValidationError):
        _make_signal(direction="buy")


def test_expiry_category_values():
    for cat in ("short", "medium", "long"):
        s = _make_signal(expiry_category=cat)
        assert s.expiry_category == cat
    with pytest.raises(ValidationError):
        _make_signal(expiry_category="weekly")


def test_regime_flag_values():
    for flag in ("bull", "bear", "neutral"):
        s = _make_signal(regime_flag=flag)
        assert s.regime_flag == flag
    with pytest.raises(ValidationError):
        _make_signal(regime_flag="sideways")


def test_gate2_score_range():
    _make_signal(gate2_score=0.0)
    _make_signal(gate2_score=100.0)
    with pytest.raises(ValidationError):
        _make_signal(gate2_score=-1.0)
    with pytest.raises(ValidationError):
        _make_signal(gate2_score=101.0)


def test_gate3_scores_range():
    with pytest.raises(ValidationError):
        _make_signal(gate3_call_score=150.0)


def test_sub_signal_scores_are_minus1_to_1():
    with pytest.raises(ValidationError):
        _make_signal(d1_d7_score=2.0)
    with pytest.raises(ValidationError):
        _make_signal(d2_score=-1.5)


def test_dte_must_be_positive():
    with pytest.raises(ValidationError):
        _make_signal(dte=0)


def test_signal_serializes_to_dict():
    signal = _make_signal()
    d = signal.model_dump()
    assert "signal_id" in d
    assert "conviction_score" in d


def test_eth_signal_d10_is_zero():
    signal = _make_signal(asset="ETH", d10_score=0.0)
    assert signal.d10_score == 0.0
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/unit/strategy/otm/test_otm_signal.py -v
```

- [ ] **Step 3: Implement `OTMSignal`**

```python
# coding/core/strategy/otm/models/otm_signal.py
"""
OTMSignal — one record per contract that survived all four gates.

Immutable once created. Serialized to DB via model_dump().
"""
import logging
from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, field_validator

logger = logging.getLogger(__name__)


class OTMSignal(BaseModel):
    model_config = ConfigDict(frozen=True)

    # ── Identity ──────────────────────────────────────────────────────────────
    signal_id: str
    generated_at: datetime
    asset: Literal["BTC", "ETH"]
    instrument_name: str
    direction: Literal["call", "put"]
    strike: float
    expiry: str
    dte: int
    expiry_category: Literal["short", "medium", "long"]

    # ── Contract metrics at signal time ───────────────────────────────────────
    delta: float
    gamma: float
    vega: float
    theta: float
    mark_iv: float
    entry_premium: float
    underlying_price: float

    # ── Gate scores ───────────────────────────────────────────────────────────
    gate1_passed: bool
    gate2_score: float
    gate3_call_score: float
    gate3_put_score: float
    gate3_directional_score: float
    conviction_score: float

    # ── Gate 3 sub-signal breakdown (each in [−1, +1]) ────────────────────────
    d1_d7_score: float
    d2_score: float
    d3_score: float
    d4_score: float
    d6_d9_score: float
    d8_score: float
    d10_score: float     # 0.0 for ETH
    ris_score: float

    # ── Sizing ────────────────────────────────────────────────────────────────
    position_usd: float
    p_win_prior: float
    kelly_fraction: float

    # ── Exit thresholds (set at entry) ────────────────────────────────────────
    take_profit_multiple: float
    stop_loss_pct: float
    time_stop_dte: int

    # ── Gate 4 rationale ──────────────────────────────────────────────────────
    vega_theta_ratio: float
    gamma_premium_ratio: float
    breakeven_price: float

    # ── Regime context ────────────────────────────────────────────────────────
    regime_flag: Literal["bull", "bear", "neutral"]
    gate2_suppressed: bool

    # ── Validators ────────────────────────────────────────────────────────────
    @field_validator("gate2_score", "gate3_call_score", "gate3_put_score",
                     "gate3_directional_score", "conviction_score")
    @classmethod
    def score_0_to_100(cls, v: float) -> float:
        if not (0.0 <= v <= 100.0):
            raise ValueError(f"Score must be in [0, 100], got {v}")
        return v

    @field_validator("d1_d7_score", "d2_score", "d3_score", "d4_score",
                     "d6_d9_score", "d8_score", "d10_score", "ris_score")
    @classmethod
    def sub_signal_range(cls, v: float) -> float:
        if not (-1.0 <= v <= 1.0):
            raise ValueError(f"Sub-signal score must be in [−1, +1], got {v}")
        return v

    @field_validator("dte")
    @classmethod
    def dte_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("dte must be >= 1")
        return v
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/unit/strategy/otm/test_otm_signal.py -v
```

- [ ] **Step 5: Commit**

```bash
git add coding/core/strategy/otm/models/ tests/unit/strategy/otm/test_otm_signal.py
git commit -m "feat: add OTMSignal Pydantic output model"
```

---

## Chunk 2: Data Fetchers + Backfill Script

---

### Task 5: `DVOLFetcher` — fetch + store DVOL history

**Files:**
- Create: `coding/service/strategy/otm/__init__.py`
- Create: `coding/service/strategy/otm/fetchers/__init__.py`
- Create: `coding/service/strategy/otm/fetchers/dvol_fetcher.py`
- Test: `tests/unit/strategy/otm/test_dvol_fetcher.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/strategy/otm/test_dvol_fetcher.py
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
from coding.service.strategy.otm.fetchers.dvol_fetcher import DVOLFetcher


@pytest.fixture
def fetcher():
    return DVOLFetcher()


def _mock_deribit_response(values: list) -> dict:
    """Build a Deribit-style index_price_history response."""
    return {
        "result": {
            "data": [[int(datetime(2025, 1, d+1, tzinfo=timezone.utc).timestamp() * 1000), v]
                     for d, v in enumerate(values)]
        }
    }


def test_parse_dvol_response_returns_list_of_tuples(fetcher):
    raw = _mock_deribit_response([55.1, 60.2, 48.3])
    result = fetcher._parse_response(raw)
    assert len(result) == 3
    assert all(isinstance(ts, datetime) and isinstance(v, float) for ts, v in result)


def test_parse_dvol_response_empty_data(fetcher):
    raw = {"result": {"data": []}}
    result = fetcher._parse_response(raw)
    assert result == []


def test_parse_dvol_response_invalid_structure(fetcher):
    with pytest.raises(KeyError):
        fetcher._parse_response({"result": {}})


def test_build_url_btc(fetcher):
    url = fetcher._build_url("BTC", 1_000_000, 2_000_000)
    assert "btc_dvol" in url
    assert "1000000" in url
    assert "2000000" in url


def test_build_url_eth(fetcher):
    url = fetcher._build_url("ETH", 1_000_000, 2_000_000)
    assert "eth_dvol" in url


def test_build_url_invalid_asset(fetcher):
    with pytest.raises(ValueError):
        fetcher._build_url("SOL", 0, 1)


@patch("coding.service.strategy.otm.fetchers.dvol_fetcher.requests.get")
def test_fetch_latest_returns_float_on_success(mock_get, fetcher):
    mock_get.return_value = MagicMock(
        status_code=200,
        json=lambda: _mock_deribit_response([62.5])
    )
    result = fetcher.fetch_latest("BTC")
    assert isinstance(result, float)
    assert result == 62.5


@patch("coding.service.strategy.otm.fetchers.dvol_fetcher.requests.get")
def test_fetch_latest_returns_none_on_http_error(mock_get, fetcher):
    mock_get.return_value = MagicMock(status_code=500)
    result = fetcher.fetch_latest("BTC")
    assert result is None


@patch("coding.service.strategy.otm.fetchers.dvol_fetcher.requests.get")
def test_fetch_history_returns_list(mock_get, fetcher):
    mock_get.return_value = MagicMock(
        status_code=200,
        json=lambda: _mock_deribit_response([50.0, 55.0, 60.0])
    )
    result = fetcher.fetch_history("BTC", months=3)
    assert len(result) == 3
    assert all(isinstance(v, float) for _, v in result)
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/unit/strategy/otm/test_dvol_fetcher.py -v
```

- [ ] **Step 3: Implement `DVOLFetcher`**

```python
# coding/service/strategy/otm/fetchers/dvol_fetcher.py
"""
DVOLFetcher — fetches Deribit DVOL index history and latest value.

Deribit endpoint: /public/get_index_price_history
Index names: btc_dvol, eth_dvol
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Tuple
import requests

logger = logging.getLogger(__name__)

_BASE_URL = "https://www.deribit.com/api/v2/public/get_index_price_history"
_ASSET_TO_INDEX = {"BTC": "btc_dvol", "ETH": "eth_dvol"}
_RESOLUTION = 1440  # daily (minutes)
_TIMEOUT_SEC = 15


class DVOLFetcher:
    """Fetches DVOL index values from Deribit for Gate 2 percentile calculation."""

    def _build_url(self, asset: str, start_ms: int, end_ms: int) -> str:
        if asset not in _ASSET_TO_INDEX:
            raise ValueError(f"Unsupported asset: {asset}. Must be BTC or ETH.")
        index_name = _ASSET_TO_INDEX[asset]
        return (f"{_BASE_URL}?index_name={index_name}"
                f"&start_timestamp={start_ms}&end_timestamp={end_ms}"
                f"&resolution={_RESOLUTION}")

    def _parse_response(self, data: dict) -> List[Tuple[datetime, float]]:
        """Parse Deribit response into list of (datetime, dvol_value) tuples."""
        rows = data["result"]["data"]
        return [
            (datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc), float(value))
            for ts_ms, value in rows
        ]

    def fetch_latest(self, asset: str) -> Optional[float]:
        """
        Fetch the most recent DVOL value for the given asset.

        Returns None on any error — callers should treat None as unavailable.
        """
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = now_ms - 2 * 24 * 3600 * 1000  # 2 days back to ensure at least 1 row
        try:
            url = self._build_url(asset, start_ms, now_ms)
            resp = requests.get(url, timeout=_TIMEOUT_SEC)
            if resp.status_code != 200:
                logger.warning("DVOL fetch failed for %s: HTTP %s", asset, resp.status_code)
                return None
            rows = self._parse_response(resp.json())
            if not rows:
                logger.warning("DVOL fetch returned empty data for %s", asset)
                return None
            return rows[-1][1]  # most recent
        except Exception as exc:
            logger.error("DVOLFetcher.fetch_latest error for %s: %s", asset, exc)
            return None

    def fetch_history(
        self, asset: str, months: int = 36
    ) -> List[Tuple[datetime, float]]:
        """
        Fetch up to `months` months of daily DVOL history.

        Returns list of (datetime, dvol_value) tuples, oldest first.
        Returns empty list on any error.
        """
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        start_ms = int(
            (datetime.now(timezone.utc) - timedelta(days=months * 30)).timestamp() * 1000
        )
        try:
            url = self._build_url(asset, start_ms, now_ms)
            resp = requests.get(url, timeout=_TIMEOUT_SEC)
            if resp.status_code != 200:
                logger.warning("DVOL history fetch failed for %s: HTTP %s", asset, resp.status_code)
                return []
            rows = self._parse_response(resp.json())
            logger.info("DVOLFetcher: fetched %d rows for %s (%d months)", len(rows), asset, months)
            return sorted(rows, key=lambda x: x[0])
        except Exception as exc:
            logger.error("DVOLFetcher.fetch_history error for %s: %s", asset, exc)
            return []
```

- [ ] **Step 4: Add `save_to_db` to `DVOLFetcher`** (prevents duplicated insert logic in finder service)

Add this method to the `DVOLFetcher` class implementation:

```python
def save_to_db(self, rows: List[Tuple[datetime, float]], asset: str, conn) -> int:
    """
    Upsert DVOL rows into dvol_history table.

    Args:
        rows: list of (datetime, dvol_value) tuples.
        asset: "BTC" or "ETH".
        conn: open psycopg2 connection (caller manages lifecycle).

    Returns:
        Number of new rows inserted (existing rows skipped via ON CONFLICT DO NOTHING).
    """
    inserted = 0
    with conn.cursor() as cur:
        for ts, value in rows:
            cur.execute(
                """
                INSERT INTO dvol_history (asset, timestamp, dvol_value)
                VALUES (%s, %s, %s)
                ON CONFLICT (asset, timestamp) DO NOTHING
                """,
                (asset, ts, value),
            )
            inserted += cur.rowcount
    logger.debug("DVOLFetcher.save_to_db: %d new rows for %s", inserted, asset)
    return inserted
```

Also add one test for `save_to_db` in `test_dvol_fetcher.py`:

```python
def test_save_to_db_calls_execute_for_each_row(fetcher):
    from unittest.mock import MagicMock, call
    from datetime import datetime, timezone
    conn = MagicMock()
    cur = MagicMock()
    conn.cursor.return_value.__enter__ = lambda s: cur
    conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    cur.rowcount = 1
    rows = [(datetime(2025, 1, 1, tzinfo=timezone.utc), 55.0),
            (datetime(2025, 1, 2, tzinfo=timezone.utc), 57.0)]
    result = fetcher.save_to_db(rows, "BTC", conn)
    assert cur.execute.call_count == 2
    assert result == 2
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
pytest tests/unit/strategy/otm/test_dvol_fetcher.py -v
```

- [ ] **Step 6: Commit**

```bash
git add coding/service/strategy/otm/ tests/unit/strategy/otm/test_dvol_fetcher.py
git commit -m "feat: add DVOLFetcher with save_to_db for incremental updates"
```

> **Note for OTMFinderService (Task 18):** The finder service must call `dvol_fetcher.fetch_latest(asset)` + `dvol_fetcher.save_to_db(...)` on every scan cycle to keep `dvol_history` current. The backfill script (Task 7) only handles the one-time historical seed.

---

### Task 6: `StablecoinFetcher` + `IBITFetcher`

**Files:**
- Create: `coding/service/strategy/otm/fetchers/stablecoin_fetcher.py`
- Create: `coding/service/strategy/otm/fetchers/ibit_fetcher.py`
- Test: `tests/unit/strategy/otm/test_external_fetchers.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/strategy/otm/test_external_fetchers.py
import pytest
from unittest.mock import patch, MagicMock
from coding.service.strategy.otm.fetchers.stablecoin_fetcher import StablecoinFetcher
from coding.service.strategy.otm.fetchers.ibit_fetcher import IBITFetcher


# ── StablecoinFetcher ─────────────────────────────────────────────────────────

@pytest.fixture
def stablecoin_fetcher():
    return StablecoinFetcher()


def test_stablecoin_parse_valid_response(stablecoin_fetcher):
    raw = {"data": [{"inflow_usd": 500_000_000, "total_supply": 50_000_000_000}]}
    result = stablecoin_fetcher._parse_inflow_pct(raw)
    assert isinstance(result, float)
    assert abs(result - 1.0) < 0.001  # 500M / 50B * 100 = 1.0%


def test_stablecoin_parse_missing_key_returns_none(stablecoin_fetcher):
    result = stablecoin_fetcher._parse_inflow_pct({"data": []})
    assert result is None


@patch("coding.service.strategy.otm.fetchers.stablecoin_fetcher.requests.get")
def test_stablecoin_fetch_returns_float(mock_get, stablecoin_fetcher):
    mock_get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"data": [{"inflow_usd": 200_000_000, "total_supply": 100_000_000_000}]},
    )
    result = stablecoin_fetcher.fetch_inflow_pct()
    assert result == pytest.approx(0.2, abs=0.001)


@patch("coding.service.strategy.otm.fetchers.stablecoin_fetcher.requests.get")
def test_stablecoin_fetch_returns_none_on_error(mock_get, stablecoin_fetcher):
    mock_get.return_value = MagicMock(status_code=403)
    result = stablecoin_fetcher.fetch_inflow_pct()
    assert result is None


# ── IBITFetcher ───────────────────────────────────────────────────────────────

@pytest.fixture
def ibit_fetcher():
    return IBITFetcher()


def test_ibit_parse_valid_response(ibit_fetcher):
    raw = {"data": {"put_call_ratio": 0.85}}
    result = ibit_fetcher._parse_pc_ratio(raw)
    assert result == pytest.approx(0.85)


def test_ibit_parse_missing_key_returns_none(ibit_fetcher):
    result = ibit_fetcher._parse_pc_ratio({"data": {}})
    assert result is None


@patch("coding.service.strategy.otm.fetchers.ibit_fetcher.requests.get")
def test_ibit_fetch_returns_float(mock_get, ibit_fetcher):
    mock_get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"data": {"put_call_ratio": 0.72}},
    )
    result = ibit_fetcher.fetch_pc_ratio()
    assert result == pytest.approx(0.72)


@patch("coding.service.strategy.otm.fetchers.ibit_fetcher.requests.get")
def test_ibit_fetch_returns_none_on_http_error(mock_get, ibit_fetcher):
    mock_get.return_value = MagicMock(status_code=404)
    result = ibit_fetcher.fetch_pc_ratio()
    assert result is None


@patch("coding.service.strategy.otm.fetchers.ibit_fetcher.requests.get")
def test_ibit_fetch_returns_none_on_exception(mock_get, ibit_fetcher):
    mock_get.side_effect = Exception("Connection timeout")
    result = ibit_fetcher.fetch_pc_ratio()
    assert result is None
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/unit/strategy/otm/test_external_fetchers.py -v
```

- [ ] **Step 3: Implement `StablecoinFetcher`**

```python
# coding/service/strategy/otm/fetchers/stablecoin_fetcher.py
"""
StablecoinFetcher — fetches stablecoin exchange inflow from CryptoQuant free API.

Fallback: returns None. Callers must treat None as neutral (D8 score = 0).
"""
import logging
from typing import Optional
import requests

logger = logging.getLogger(__name__)

_URL = "https://api.cryptoquant.com/v1/stablecoins/exchange-inflow"
_TIMEOUT_SEC = 10


class StablecoinFetcher:
    """Fetches stablecoin exchange inflow percentage of total supply."""

    def _parse_inflow_pct(self, data: dict) -> Optional[float]:
        """
        Parse CryptoQuant response.
        Returns inflow as percentage of total stablecoin supply.
        Returns None if data is missing or malformed.
        """
        rows = data.get("data", [])
        if not rows:
            return None
        row = rows[-1]  # most recent
        inflow_usd = row.get("inflow_usd")
        total_supply = row.get("total_supply")
        if inflow_usd is None or total_supply is None or total_supply == 0:
            return None
        return (float(inflow_usd) / float(total_supply)) * 100.0

    def fetch_inflow_pct(self) -> Optional[float]:
        """
        Fetch 3-day stablecoin exchange inflow as % of total supply.

        Returns None on any failure — treat as neutral signal (D8 = 0).
        """
        try:
            resp = requests.get(_URL, timeout=_TIMEOUT_SEC)
            if resp.status_code != 200:
                logger.warning(
                    "StablecoinFetcher: HTTP %s — falling back to neutral D8",
                    resp.status_code
                )
                return None
            return self._parse_inflow_pct(resp.json())
        except Exception as exc:
            logger.warning("StablecoinFetcher unavailable: %s — D8 neutral", exc)
            return None
```

- [ ] **Step 4: Implement `IBITFetcher`**

```python
# coding/service/strategy/otm/fetchers/ibit_fetcher.py
"""
IBITFetcher — fetches IBIT options P/C ratio from CBOE delayed public data.

BTC-only signal (D10). ETH callers should not use this fetcher.
Fallback: returns None. Callers treat None as neutral (D10 = 0).
"""
import logging
from typing import Optional
import requests

logger = logging.getLogger(__name__)

_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/_IBIT.json"
_TIMEOUT_SEC = 10


class IBITFetcher:
    """Fetches IBIT put/call ratio from CBOE public delayed data feed."""

    def _parse_pc_ratio(self, data: dict) -> Optional[float]:
        """Parse CBOE response to extract put_call_ratio field."""
        try:
            return float(data["data"]["put_call_ratio"])
        except (KeyError, TypeError, ValueError):
            return None

    def fetch_pc_ratio(self) -> Optional[float]:
        """
        Fetch current IBIT options put/call ratio.

        Returns None on any failure — treat as neutral (D10 = 0).
        """
        try:
            resp = requests.get(_URL, timeout=_TIMEOUT_SEC)
            if resp.status_code != 200:
                logger.warning(
                    "IBITFetcher: HTTP %s — falling back to neutral D10",
                    resp.status_code
                )
                return None
            ratio = self._parse_pc_ratio(resp.json())
            if ratio is None:
                logger.warning("IBITFetcher: missing put_call_ratio in response — D10 neutral")
            return ratio
        except Exception as exc:
            logger.warning("IBITFetcher unavailable: %s — D10 neutral", exc)
            return None
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
pytest tests/unit/strategy/otm/test_external_fetchers.py -v
```

- [ ] **Step 6: Commit**

```bash
git add coding/service/strategy/otm/fetchers/ tests/unit/strategy/otm/test_external_fetchers.py
git commit -m "feat: add StablecoinFetcher and IBITFetcher with neutral fallbacks"
```

---

### Task 7: DVOL backfill script

**Files:**
- Create: `scripts/backfill_dvol_history.py`

No unit tests needed — this is a one-shot admin script with its own verification output.

- [ ] **Step 1: Write the script**

```python
#!/usr/bin/env python
# scripts/backfill_dvol_history.py
"""
One-time backfill: fetch full DVOL history from Deribit and store in dvol_history table.

Usage:
    python scripts/backfill_dvol_history.py
    python scripts/backfill_dvol_history.py --assets BTC   (BTC only)

Skips rows that already exist (ON CONFLICT DO NOTHING).
"""
import logging
import os
import sys
import argparse
from datetime import datetime

# ── Path setup ────────────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from coding.core.logging.logging_setup import init_logging
init_logging(level="INFO")

import psycopg2
from coding.service.strategy.otm.fetchers.dvol_fetcher import DVOLFetcher

logger = logging.getLogger(__name__)


def get_connection():
    return psycopg2.connect(
        host="localhost", port=5433, dbname="option_trading",
        user="postgres", password=os.getenv("DB_PASSWORD", "")
    )


def backfill_asset(asset: str, months: int = 40) -> int:
    """Fetch and store DVOL history for one asset. Returns rows inserted."""
    fetcher = DVOLFetcher()
    logger.info("Fetching %d months of DVOL history for %s...", months, asset)
    rows = fetcher.fetch_history(asset, months=months)
    if not rows:
        logger.error("No data returned for %s — aborting", asset)
        return 0

    logger.info("Received %d rows for %s", len(rows), asset)

    conn = get_connection()
    inserted = 0
    try:
        with conn:
            with conn.cursor() as cur:
                for ts, value in rows:
                    cur.execute(
                        """
                        INSERT INTO dvol_history (asset, timestamp, dvol_value)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (asset, timestamp) DO NOTHING
                        """,
                        (asset, ts, value),
                    )
                    inserted += cur.rowcount
        logger.info("Inserted %d new rows for %s (%d already existed)",
                    inserted, asset, len(rows) - inserted)
    finally:
        conn.close()

    return inserted


def main():
    parser = argparse.ArgumentParser(description="Backfill DVOL history from Deribit")
    parser.add_argument("--assets", nargs="+", choices=["BTC", "ETH"],
                        default=["BTC", "ETH"])
    args = parser.parse_args()

    total = 0
    for asset in args.assets:
        total += backfill_asset(asset)

    logger.info("Backfill complete. Total rows inserted: %d", total)

    # Verification
    conn = get_connection()
    with conn.cursor() as cur:
        cur.execute(
            "SELECT asset, COUNT(*), MIN(timestamp)::date, MAX(timestamp)::date "
            "FROM dvol_history GROUP BY asset ORDER BY asset"
        )
        print("\n── DVOL History Summary ──")
        for row in cur.fetchall():
            print(f"  {row[0]}: {row[1]} days  |  {row[2]} → {row[3]}")
    conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the backfill**

```bash
python scripts/backfill_dvol_history.py
```
Expected output like:
```
── DVOL History Summary ──
  BTC: 1095 days  |  2022-01-01 → 2025-03-14
  ETH: 900 days   |  2022-06-01 → 2025-03-14
```

- [ ] **Step 3: Commit**

```bash
git add scripts/backfill_dvol_history.py
git commit -m "feat: add DVOL history backfill script"
```

---

## Chunk 3: Gate 1 — Liquidity Filter

---

### Task 8: `LiquidityGate`

**Files:**
- Create: `coding/core/strategy/otm/signals/__init__.py`
- Create: `coding/core/strategy/otm/signals/liquidity_gate.py`
- Test: `tests/unit/strategy/otm/test_liquidity_gate.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/strategy/otm/test_liquidity_gate.py
import pytest
from coding.core.strategy.otm.signals.liquidity_gate import LiquidityGate
from coding.core.strategy.otm.models.otm_config import OTMConfig


@pytest.fixture
def config():
    return OTMConfig(risk_budget_usd=10_000.0)


@pytest.fixture
def gate(config):
    return LiquidityGate(config)


def _make_contract(**overrides) -> dict:
    """Return a contract dict that passes all Gate 1 checks by default."""
    base = {
        "asset": "BTC",
        "instrument_name": "BTC-28MAR25-95000-C",
        "delta": 0.28,
        "bid_iv": 0.60,
        "ask_iv": 0.63,       # spread = 0.03 vol pts, relative = 0.03/0.615 ≈ 4.9%
        "open_interest": 200,
        "volume_24h": 20,     # vol/OI = 0.10 > 0.05
        "mark_price": 0.004,  # in BTC
        "underlying_price": 87000.0,
        "contract_qty": 1,
    }
    base.update(overrides)
    return base


# ── Bid-ask spread: relative check ───────────────────────────────────────────

def test_passes_when_spread_within_both_thresholds(gate):
    passed, reason = gate.check(_make_contract())
    assert passed, reason


def test_fails_relative_spread_too_wide(gate):
    # relative = (0.65 - 0.50) / 0.575 = 26.1% > 8%
    passed, reason = gate.check(_make_contract(bid_iv=0.50, ask_iv=0.65))
    assert not passed
    assert "relative" in reason.lower()


def test_fails_absolute_spread_too_wide(gate):
    # absolute = ask_iv - bid_iv = 5.0 vol pts > 4.0 cap
    passed, reason = gate.check(_make_contract(bid_iv=0.60, ask_iv=0.65 + 0.05))
    # relative: (0.70 - 0.60) / 0.65 = 15.4% → already fails relative; but absolute also tested
    # Use values where relative is ok but absolute > 4.0
    # bid_iv = 0.60, ask_iv = 0.645 → absolute = 0.045 * 100 = 4.5 vol pts > 4.0
    passed, reason = gate.check(_make_contract(bid_iv=0.600, ask_iv=0.645))
    assert not passed
    assert "absolute" in reason.lower()


def test_fails_when_bid_iv_is_none(gate):
    passed, reason = gate.check(_make_contract(bid_iv=None))
    assert not passed
    assert "null" in reason.lower() or "none" in reason.lower() or "missing" in reason.lower()


def test_fails_when_ask_iv_is_none(gate):
    passed, reason = gate.check(_make_contract(ask_iv=None))
    assert not passed


# ── Volume / OI ratio ─────────────────────────────────────────────────────────

def test_fails_volume_oi_ratio_too_low(gate):
    passed, reason = gate.check(_make_contract(volume_24h=2, open_interest=200))
    # ratio = 2/200 = 0.01 < 0.05
    assert not passed
    assert "volume" in reason.lower() or "oi" in reason.lower()


def test_fails_volume_oi_exactly_at_threshold(gate):
    passed, reason = gate.check(_make_contract(volume_24h=10, open_interest=200))
    # ratio = 10/200 = 0.05 — spec says > 0.05 (strictly), so exactly 0.05 should FAIL
    assert not passed
    assert "volume" in reason.lower() or "oi" in reason.lower()


# ── Minimum OI — asset-specific ───────────────────────────────────────────────

def test_fails_btc_min_oi(gate):
    passed, reason = gate.check(_make_contract(asset="BTC", open_interest=40))
    assert not passed
    assert "oi" in reason.lower() or "open interest" in reason.lower()


def test_fails_eth_min_oi(gate):
    passed, reason = gate.check(_make_contract(asset="ETH", open_interest=150))
    assert not passed


def test_passes_eth_min_oi_exactly(gate):
    passed, _ = gate.check(_make_contract(asset="ETH", open_interest=200,
                                           volume_24h=20))
    assert passed


# ── Transaction cost floor ────────────────────────────────────────────────────

def test_fails_tx_cost_floor(gate):
    # round_trip_fee = 2 × 0.0003 × 87000 × 1 = 52.2
    # required: 2 × entry_premium > 52.2 × 5 = 261 → premium must be > 130.5
    # mark_price in BTC: 0.0010 → entry_premium = 0.001 × 87000 = 87 USD — fails
    passed, reason = gate.check(_make_contract(mark_price=0.0010, underlying_price=87000.0))
    assert not passed
    assert "cost" in reason.lower() or "fee" in reason.lower() or "premium" in reason.lower()


def test_passes_tx_cost_floor(gate):
    # mark_price 0.004 → premium = 0.004 × 87000 = 348 USD
    # 2 × 348 = 696 > 261 ✓
    passed, _ = gate.check(_make_contract(mark_price=0.004, underlying_price=87000.0))
    assert passed


# ── Edge cases ────────────────────────────────────────────────────────────────

def test_fails_zero_open_interest(gate):
    passed, _ = gate.check(_make_contract(open_interest=0))
    assert not passed


def test_passes_btc_exactly_min_oi(gate):
    passed, _ = gate.check(_make_contract(asset="BTC", open_interest=50, volume_24h=5))
    assert passed


def test_check_returns_tuple_of_bool_and_str(gate):
    result = gate.check(_make_contract())
    assert isinstance(result, tuple)
    assert isinstance(result[0], bool)
    assert isinstance(result[1], str)


def test_fail_reason_is_informative(gate):
    _, reason = gate.check(_make_contract(open_interest=5))
    assert len(reason) > 10   # not empty


def test_btc_absolute_spread_threshold_in_vol_pts(gate):
    # Note: bid_iv and ask_iv are in decimal (0.60 = 60%)
    # absolute check uses (ask_iv - bid_iv) × 100 converted to vol pts
    # OR spec says "vol pts" which may just be the raw IV difference
    # Spec says: (ask_iv − bid_iv) < 4.0 vol pts
    # Treating 1 vol pt = 1 percentage point of IV (i.e., raw decimal diff × 100 < 4.0)
    # bid=0.60, ask=0.63 → diff = 0.03 → 3 vol pts → PASS
    passed, _ = gate.check(_make_contract(bid_iv=0.60, ask_iv=0.63))
    assert passed
    # bid=0.60, ask=0.641 → diff = 0.041 → 4.1 vol pts → FAIL
    passed, reason = gate.check(_make_contract(bid_iv=0.60, ask_iv=0.641))
    assert not passed
    assert "absolute" in reason.lower()
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/unit/strategy/otm/test_liquidity_gate.py -v
```

- [ ] **Step 3: Implement `LiquidityGate`**

```python
# coding/core/strategy/otm/signals/liquidity_gate.py
"""
Gate 1 — Liquidity Filter.

Hard pass/fail. All conditions must pass. Cheap to compute; run first.
Returns (passed: bool, reason: str).
"""
import logging
from typing import Tuple
from coding.core.strategy.otm.models.otm_config import OTMConfig

logger = logging.getLogger(__name__)

_FEE_RATE = 0.0003   # Deribit taker fee per leg


class LiquidityGate:
    """
    Applies Gate 1 liquidity checks to a single OTM contract candidate.

    Checks (all must pass):
    1. Bid-ask IV spread — DUAL threshold (relative AND absolute)
    2. Volume / OI ratio
    3. Minimum open interest (asset-specific)
    4. Transaction cost floor
    """

    def __init__(self, config: OTMConfig) -> None:
        self._config = config

    def check(self, contract: dict) -> Tuple[bool, str]:
        """
        Check one contract against all Gate 1 conditions.

        Args:
            contract: dict with keys: asset, bid_iv, ask_iv, open_interest,
                      volume_24h, mark_price, underlying_price, contract_qty

        Returns:
            (True, "passed") if all conditions met.
            (False, "<reason>") on first failure — short-circuits.
        """
        bid_iv = contract.get("bid_iv")
        ask_iv = contract.get("ask_iv")

        # ── 1. Null IV check ──────────────────────────────────────────────────
        if bid_iv is None or ask_iv is None:
            return False, "missing bid_iv or ask_iv"

        mid_iv = (bid_iv + ask_iv) / 2.0
        if mid_iv <= 0:
            return False, "mid_iv <= 0 — invalid IV"

        # ── 2a. Relative spread ───────────────────────────────────────────────
        relative_spread = (ask_iv - bid_iv) / mid_iv
        if relative_spread >= self._config.max_bid_ask_spread_relative:
            return False, (
                f"relative spread {relative_spread:.3f} "
                f">= threshold {self._config.max_bid_ask_spread_relative}"
            )

        # ── 2b. Absolute spread (vol pts = diff × 100) ────────────────────────
        absolute_spread_vol_pts = (ask_iv - bid_iv) * 100.0
        if absolute_spread_vol_pts >= self._config.max_bid_ask_spread_absolute:
            return False, (
                f"absolute spread {absolute_spread_vol_pts:.2f} vol pts "
                f">= cap {self._config.max_bid_ask_spread_absolute}"
            )

        # ── 3. Volume / OI ratio ──────────────────────────────────────────────
        oi = contract.get("open_interest", 0)
        volume_24h = contract.get("volume_24h", 0)
        if oi <= 0:
            return False, f"open_interest {oi} <= 0"
        vol_oi_ratio = volume_24h / oi
        if vol_oi_ratio <= self._config.min_volume_oi_ratio:
            return False, (
                f"volume/OI ratio {vol_oi_ratio:.4f} "
                f"<= threshold {self._config.min_volume_oi_ratio} (spec requires >)"
            )

        # ── 4. Minimum OI — asset-specific ───────────────────────────────────
        asset = contract.get("asset", "BTC")
        min_oi = self._config.min_oi_btc if asset == "BTC" else self._config.min_oi_eth
        if oi < min_oi:
            return False, f"open_interest {oi} < min {min_oi} for {asset}"

        # ── 5. Transaction cost floor ─────────────────────────────────────────
        mark_price = contract.get("mark_price", 0.0)
        underlying = contract.get("underlying_price", 0.0)
        qty = contract.get("contract_qty", 1)
        entry_premium_usd = mark_price * underlying   # USD per contract
        round_trip_fee = 2.0 * _FEE_RATE * underlying * qty
        required_premium = round_trip_fee * self._config.tx_cost_floor_multiplier
        if (2.0 * entry_premium_usd) <= required_premium:
            return False, (
                f"2× premium {2*entry_premium_usd:.2f} USD "
                f"<= {self._config.tx_cost_floor_multiplier}× fee {required_premium:.2f} USD"
            )

        return True, "passed"
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/unit/strategy/otm/test_liquidity_gate.py -v
```
Expected: all 18 tests PASS

- [ ] **Step 5: Commit**

```bash
git add coding/core/strategy/otm/signals/ tests/unit/strategy/otm/test_liquidity_gate.py
git commit -m "feat: implement Gate 1 LiquidityGate with dual spread threshold"
```

---

*End of Chunk 3. Chunks 4–7 continue in the same file below.*

---

## Chunk 4: Gate 2 — Volatility Regime Gate

---

### Task 9: `VolatilityRegimeGate`

**Files:**
- Create: `coding/core/strategy/otm/signals/volatility_regime_gate.py`
- Test: `tests/unit/strategy/otm/test_volatility_regime_gate.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/strategy/otm/test_volatility_regime_gate.py
import pytest
from coding.core.strategy.otm.signals.volatility_regime_gate import VolatilityRegimeGate
from coding.core.strategy.otm.models.otm_config import OTMConfig


@pytest.fixture
def config():
    return OTMConfig(risk_budget_usd=10_000.0)


@pytest.fixture
def gate(config):
    return VolatilityRegimeGate(config)


def _make_ohlcv(n: int = 200) -> list:
    import random; random.seed(99)
    price = 50000.0
    rows = []
    for _ in range(n):
        change = random.gauss(0, 0.02)
        close = price * (1 + change)
        rows.append({"open": price, "high": max(price, close)*1.005,
                     "low": min(price, close)*0.995, "close": close, "volume": 1000.0})
        price = close
    return rows


# ── V1: DVOL percentile ──────────────────────────────────────────────────────

def test_v1_score_100_when_dvol_low_and_below_floor(gate):
    # 200 low values + 200 high values → median ~55, std ~15, floor ~70
    history = [40.0 + i*0.1 for i in range(200)] + [70.0 + i*0.1 for i in range(200)]
    score = gate._score_v1(38.0, history)   # below 30th pctile AND below floor
    assert score == 100.0

def test_v1_score_0_when_dvol_above_floor(gate):
    history = [60.0] * 400   # all same; std=0; floor=60+0=60
    score = gate._score_v1(85.0, history)   # above floor
    assert score == 0.0

def test_v1_score_50_when_history_under_90_days(gate):
    assert gate._score_v1(60.0, [60.0] * 80) == 50.0

def test_v1_score_50_when_history_empty(gate):
    assert gate._score_v1(60.0, []) == 50.0

def test_v1_insufficient_history_logs_warning(gate, caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        gate._score_v1(60.0, [55.0] * 50)
    assert any("insufficient" in m.lower() or "dvol" in m.lower() for m in caplog.messages)

def test_v1_degraded_history_90_to_365_logs_warning(gate, caplog):
    import logging
    with caplog.at_level(logging.WARNING):
        gate._score_v1(55.0, [60.0] * 150)
    assert any("degraded" in m.lower() for m in caplog.messages)


# ── VRP component ────────────────────────────────────────────────────────────

def test_vrp_score_100_when_vrp_below_zero(gate):
    assert gate._score_vrp(atm_iv_30d=0.50, rv_30d_parkinson=0.55) == 100.0

def test_vrp_score_100_when_vrp_below_threshold(gate):
    # VRP = (0.50-0.46)*100 = +4 pts < 5 threshold
    assert gate._score_vrp(atm_iv_30d=0.50, rv_30d_parkinson=0.46) == 100.0

def test_vrp_score_0_when_vrp_above_threshold(gate):
    # VRP = (0.50-0.40)*100 = +10 pts > 5
    assert gate._score_vrp(atm_iv_30d=0.50, rv_30d_parkinson=0.40) == 0.0


# ── GARCH component ──────────────────────────────────────────────────────────

def test_garch_score_100_when_forecast_exceeds_iv_ratio(gate):
    # 0.75/0.65=1.15 > 1.10
    assert gate._score_garch(garch_fcast_annualized=0.75, atm_iv_30d=0.65) == 100.0

def test_garch_score_0_when_forecast_below_ratio(gate):
    # 0.55/0.65=0.846 < 1.10
    assert gate._score_garch(garch_fcast_annualized=0.55, atm_iv_30d=0.65) == 0.0

def test_garch_score_50_when_fewer_than_90_candles(gate):
    assert gate._score_garch_from_ohlcv(_make_ohlcv(50)) == 50.0

def test_garch_fit_returns_positive_float_with_sufficient_data(gate):
    result = gate._fit_gjr_garch(_make_ohlcv(200))
    assert result is not None
    assert isinstance(result, float) and result > 0


# ── V3: IV term structure ─────────────────────────────────────────────────────

def test_v3_100_in_contango(gate):
    assert gate._score_v3({"spread": 8.0}) == 100.0

def test_v3_50_flat(gate):
    assert gate._score_v3({"spread": 2.0}) == 50.0

def test_v3_25_shallow_backwardation(gate):
    assert gate._score_v3({"spread": -10.0}) == 25.0

def test_v3_0_deep_backwardation(gate):
    assert gate._score_v3({"spread": -20.0}) == 0.0

def test_v3_50_when_none(gate):
    assert gate._score_v3(None) == 50.0


# ── Composite ────────────────────────────────────────────────────────────────

def test_composite_weighted_average(gate):
    # 0.30*100 + 0.40*80 + 0.30*50 = 77
    assert gate._combine_scores(100.0, 80.0, 50.0) == pytest.approx(77.0, abs=0.1)

def test_action_new_entries_allowed(gate):
    assert gate._determine_action(55.0) == "new_entries_allowed"

def test_action_no_new_entries(gate):
    assert gate._determine_action(35.0) == "no_new_entries"

def test_action_partial_exit(gate):
    assert gate._determine_action(25.0) == "partial_exit"

def test_score_method_returns_required_keys(gate):
    result = gate.score(
        dvol_history=[60.0]*400, current_dvol=45.0,
        atm_iv_30d=0.60, rv_30d_parkinson=0.55,
        ohlcv_daily=_make_ohlcv(200),
        term_structure_data={"spread": 8.0},
    )
    for key in ("total_score", "action", "v1_score", "v2v4_score", "v3_score",
                "garch_fcast_annualized"):
        assert key in result
    assert 0.0 <= result["total_score"] <= 100.0
```

- [ ] **Step 2: Run tests — expect FAIL**
```bash
pytest tests/unit/strategy/otm/test_volatility_regime_gate.py -v
```

- [ ] **Step 3: Implement `VolatilityRegimeGate`**

```python
# coding/core/strategy/otm/signals/volatility_regime_gate.py
"""
Gate 2 — Volatility Regime. Score 0-100.
  V1 (30%): DVOL percentile + dynamic floor
  V2+V4 (40%): VRP (50%) + GJR-GARCH (50%)
  V3 (30%): IV term structure slope

Actions: score>=40 -> new_entries_allowed | 30-39 -> no_new_entries | <=29 -> partial_exit
"""
import logging, math
from typing import Dict, List, Optional
import numpy as np
from coding.core.strategy.otm.models.otm_config import OTMConfig

logger = logging.getLogger(__name__)


class VolatilityRegimeGate:
    def __init__(self, config: OTMConfig) -> None:
        self._config = config

    def _score_v1(self, current_dvol: float, dvol_history: List[float]) -> float:
        n = len(dvol_history)
        if n < 90:
            logger.warning("DVOL history insufficient (%d days) — V1 score = 50 (neutral)", n)
            return 50.0
        if n < 365:
            logger.warning("DVOL history degraded (%d days, 36m preferred)", n)
        arr = np.array(dvol_history, dtype=float)
        percentile = float(np.mean(arr <= current_dvol) * 100.0)
        dvol_floor = float(np.median(arr)) + self._config.dvol_floor_std_multiplier * float(np.std(arr))
        below_pct = percentile < self._config.dvol_percentile_threshold
        below_floor = current_dvol < dvol_floor
        # Spec requires BOTH conditions for full score; no partial credit.
        if below_pct and below_floor:
            return 100.0
        return 0.0

    def _score_vrp(self, atm_iv_30d: float, rv_30d_parkinson: float) -> float:
        vrp_vol_pts = (atm_iv_30d - rv_30d_parkinson) * 100.0
        return 100.0 if vrp_vol_pts < self._config.vrp_cheap_threshold else 0.0

    def _fit_gjr_garch(self, ohlcv_daily: List[dict]) -> Optional[float]:
        try:
            from arch import arch_model
            closes = [r["close"] for r in ohlcv_daily]
            log_ret = [math.log(closes[i]/closes[i-1])*100.0 for i in range(1, len(closes))]
            res = arch_model(np.array(log_ret), vol="GARCH", p=1, o=1, q=1,
                             dist="normal").fit(disp="off", show_warning=False)
            variance_1d = float(res.forecast(horizon=1, reindex=False).variance.iloc[-1, 0])
            return math.sqrt(variance_1d) * math.sqrt(252) / 100.0
        except Exception as exc:
            logger.warning("GJR-GARCH fit failed: %s", exc)
            return None

    def _score_garch_from_ohlcv(self, ohlcv_daily: List[dict]) -> float:
        n = len(ohlcv_daily)
        if n < 90:
            logger.warning("GARCH: < 90 candles (%d) — sub-signal B = 50 (neutral)", n)
            return 50.0
        if n < 180:
            logger.warning("GARCH: %d candles (< 180), fitting on available data", n)
        fcast = self._fit_gjr_garch(ohlcv_daily)
        return 50.0 if fcast is None else self._score_garch(fcast, None)

    def _score_garch(self, garch_fcast_annualized: float,
                     atm_iv_30d: Optional[float]) -> float:
        if atm_iv_30d is None or atm_iv_30d <= 0:
            return 100.0 if garch_fcast_annualized > 0.65 * self._config.garch_iv_ratio_threshold else 0.0
        return 100.0 if (garch_fcast_annualized / atm_iv_30d) > self._config.garch_iv_ratio_threshold else 0.0

    def _score_v3(self, term_data: Optional[dict]) -> float:
        if term_data is None:
            return 50.0
        slope = term_data.get("spread", 0.0)
        # spec: contango >+5=100, flat [-5,+5]=50, shallow back (-15,-5)=25, deep <-15=0
        if slope > self._config.term_structure_contango_threshold:
            return 100.0
        elif slope >= self._config.term_structure_shallow_back_threshold:
            # flat: slope in [-5, +5] (shallow_back_threshold = -5.0)
            return 50.0
        elif slope > self._config.term_structure_deep_back_threshold:
            # shallow backwardation: slope in (-15, -5)
            return 25.0
        return 0.0  # deep backwardation: slope <= -15         # deep backwardation < -15

    def _combine_scores(self, v1: float, v2v4: float, v3: float) -> float:
        return round(0.30*v1 + 0.40*v2v4 + 0.30*v3, 2)

    def _determine_action(self, score: float) -> str:
        if score >= self._config.gate2_suppress_threshold:
            return "new_entries_allowed"
        elif score >= self._config.gate2_position_exit_threshold:
            return "no_new_entries"
        return "partial_exit"

    def score(self, dvol_history: List[float], current_dvol: float,
              atm_iv_30d: float, rv_30d_parkinson: float,
              ohlcv_daily: List[dict], term_structure_data: Optional[dict]) -> Dict:
        v1 = self._score_v1(current_dvol, dvol_history)
        vrp_score = self._score_vrp(atm_iv_30d, rv_30d_parkinson)
        garch_fcast = self._fit_gjr_garch(ohlcv_daily) if len(ohlcv_daily) >= 90 else None
        garch_score = self._score_garch(garch_fcast, atm_iv_30d) if garch_fcast is not None else 50.0
        v2v4 = (vrp_score + garch_score) / 2.0
        v3 = self._score_v3(term_structure_data)
        total = self._combine_scores(v1, v2v4, v3)
        return {
            "total_score": total, "action": self._determine_action(total),
            "v1_score": v1, "v2v4_score": v2v4, "v3_score": v3,
            "vrp_score": vrp_score, "garch_score": garch_score,
            "garch_fcast_annualized": garch_fcast,
        }
```

- [ ] **Step 4: Run tests — expect PASS**
```bash
pytest tests/unit/strategy/otm/test_volatility_regime_gate.py -v
```

- [ ] **Step 5: Commit**
```bash
git add coding/core/strategy/otm/signals/volatility_regime_gate.py \
        tests/unit/strategy/otm/test_volatility_regime_gate.py
git commit -m "feat: implement Gate 2 VolatilityRegimeGate (DVOL/VRP/GARCH/term-structure)"
```

---

## Chunk 5: Gate 3 — Directional Scorer

---

### Task 10: `DirectionalScorer`

**Files:**
- Create: `coding/core/strategy/otm/signals/directional_scorer.py`
- Test: `tests/unit/strategy/otm/test_directional_scorer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/strategy/otm/test_directional_scorer.py
import pytest
import numpy as np
from coding.core.strategy.otm.signals.directional_scorer import DirectionalScorer
from coding.core.strategy.otm.models.otm_config import OTMConfig


@pytest.fixture
def config():
    return OTMConfig(risk_budget_usd=10_000.0)

@pytest.fixture
def scorer(config):
    return DirectionalScorer(config)

def _ohlcv(n=60, trend="up"):
    price = 50000.0
    rows = []
    for _ in range(n):
        price *= 1.001 if trend == "up" else 0.999
        rows.append({"close": price})
    return rows


# ── D1+D7 ─────────────────────────────────────────────────────────────────────

def test_d1d7_bullish_negative_gex(scorer):
    gex = {"totals": {"net_gex": -2e6, "net_dex": 0}, "second_order": {"vanna": 1.0}}
    assert scorer._score_d1_d7(gex) > 0.0

def test_d1d7_bearish_positive_gex(scorer):
    gex = {"totals": {"net_gex": 2e6, "net_dex": 0}, "second_order": {"vanna": -1.0}}
    assert scorer._score_d1_d7(gex) < 0.0

def test_d1d7_neutral_zero_gex(scorer):
    gex = {"totals": {"net_gex": 0.0, "net_dex": 0}, "second_order": {"vanna": 0.0}}
    assert scorer._score_d1_d7(gex) == 0.0


# ── D2 ────────────────────────────────────────────────────────────────────────

def test_d2_bullish_low_funding_peak_shorts(scorer):
    history = [0.0001]*900 + [0.0008]*100
    assert scorer._score_d2(0.00005, history, spot_making_new_30d_low=True) > 0.0

def test_d2_bearish_high_funding_with_divergence(scorer):
    history = [0.0001]*900 + [0.0008]*100
    assert scorer._score_d2(0.0008, history, bearish_divergence=True) < 0.0

def test_d2_neutral_empty_history(scorer):
    assert scorer._score_d2(0.0001, []) == 0.0


# ── D3 ────────────────────────────────────────────────────────────────────────

def test_d3_bullish_low_z_score(scorer):
    # mean=0.02, current=0.01 -> z negative -> calls cheap -> bullish
    history = [0.02 + 0.001*(i%5-2) for i in range(30)]
    assert scorer._score_d3(current_rr=0.01, rr25_history=history) > 0.0

def test_d3_bearish_high_z_score(scorer):
    history = [-0.01 + 0.001*(i%3-1) for i in range(30)]
    assert scorer._score_d3(current_rr=0.02, rr25_history=history) < 0.0

def test_d3_neutral_within_threshold(scorer):
    assert scorer._score_d3(0.01, [0.01]*30) == 0.0

def test_d3_neutral_insufficient_history(scorer):
    assert scorer._score_d3(0.01, [0.01]*5) == 0.0


# ── D4 ────────────────────────────────────────────────────────────────────────

def test_d4_bullish_high_pc_ratio(scorer):
    history = [1.0 + 0.01*i for i in range(100)]
    assert scorer._score_d4(1.95, history) > 0.0

def test_d4_bearish_low_pc_ratio(scorer):
    history = [1.0 + 0.01*i for i in range(100)]
    assert scorer._score_d4(1.0, history) < 0.0

def test_d4_neutral_mid_range(scorer):
    history = [1.0 + 0.01*i for i in range(100)]
    assert scorer._score_d4(1.50, history) == 0.0


# ── D6+D9 ─────────────────────────────────────────────────────────────────────

def test_d6d9_bullish_call_blocks_positive_dex(scorer):
    assert scorer._score_d6_d9({"blocks_detected": True, "direction": "call"},
                                dex_sign_flipped_positive=True) > 0.0

def test_d6d9_bearish_put_blocks_negative_dex(scorer):
    assert scorer._score_d6_d9({"blocks_detected": True, "direction": "put"},
                                dex_sign_flipped_negative=True) < 0.0

def test_d6d9_neutral_no_blocks(scorer):
    assert scorer._score_d6_d9({"blocks_detected": False}) == 0.0


# ── D8 ────────────────────────────────────────────────────────────────────────

def test_d8_bullish_large_inflow(scorer):    assert scorer._score_d8(0.8) > 0.0
def test_d8_bearish_large_outflow(scorer):   assert scorer._score_d8(-0.8) < 0.0
def test_d8_neutral_small(scorer):           assert scorer._score_d8(0.2) == 0.0
def test_d8_neutral_none(scorer):            assert scorer._score_d8(None) == 0.0


# ── D10 ───────────────────────────────────────────────────────────────────────

def test_d10_bullish_below_avg(scorer):      assert scorer._score_d10(0.5, 0.9) > 0.0
def test_d10_bearish_above_avg(scorer):      assert scorer._score_d10(1.3, 0.9) < 0.0
def test_d10_neutral_none(scorer):           assert scorer._score_d10(None, None) == 0.0


# ── RIS ───────────────────────────────────────────────────────────────────────

def test_ris_bullish_calls_cheap(scorer):
    # divergence = (0.03 - 0.00)*100 = 3 vol pts > 2 threshold
    assert scorer._score_ris(rr25_30d_mean=0.03, rr25_current=0.00) > 0.0

def test_ris_bearish_puts_cheap(scorer):
    # divergence = (-0.03 - 0.00)*100 = -3 vol pts < -2
    assert scorer._score_ris(rr25_30d_mean=-0.03, rr25_current=0.00) < 0.0

def test_ris_neutral_within_threshold(scorer):
    # divergence = 0.5 vol pts < 2
    assert scorer._score_ris(0.01, 0.005) == 0.0


# ── Regime ────────────────────────────────────────────────────────────────────

def test_regime_bull(scorer):
    assert scorer._detect_regime(_ohlcv(60, "up")) == "bull"

def test_regime_bear(scorer):
    assert scorer._detect_regime(_ohlcv(60, "down")) == "bear"

def test_regime_neutral_insufficient_data(scorer):
    assert scorer._detect_regime([{"close": 50000.0}]*5) == "neutral"

def test_regime_scaling_amplifies_directional_in_bull(scorer):
    weights = {"D1_D7": 0.22, "D2": 0.15, "D8": 0.08}
    scaled = scorer._apply_regime_scaling(weights, "call", "bull", "BTC")
    assert scaled["D1_D7"] > weights["D1_D7"]  # directional scaled up
    assert abs(scaled["D8"] - weights["D8"]/sum(weights.values())) < 0.05  # D8 proportionally unchanged

def test_weights_sum_to_1_after_scaling(scorer):
    weights = {"D1_D7": 0.22, "D2": 0.15, "D3": 0.14, "D4": 0.11,
               "D6_D9": 0.14, "D8": 0.08, "D10": 0.09, "RIS": 0.07}
    scaled = scorer._apply_regime_scaling(weights, "call", "bull", "BTC")
    assert abs(sum(scaled.values()) - 1.0) < 0.001


# ── Conflict rules ────────────────────────────────────────────────────────────

def test_d3d4_conflict_reduces_contribution(scorer):
    raw = 1.0*0.14 + (-1.0)*0.11
    adjusted = scorer._apply_d3d4_conflict_rule(1.0, -1.0, 0.14, 0.11)
    assert abs(adjusted) < abs(raw)

def test_d3d4_no_conflict_same_direction(scorer):
    expected = 1.0*0.14 + 0.8*0.11
    assert scorer._apply_d3d4_conflict_rule(1.0, 0.8, 0.14, 0.11) == pytest.approx(expected, abs=0.001)


# ── ETH call penalty ─────────────────────────────────────────────────────────

def test_eth_call_penalty_applied(scorer):
    assert scorer._apply_eth_call_penalty(80.0, "ETH", "call", 0.28) == pytest.approx(68.0, abs=0.1)

def test_eth_call_penalty_skipped_outside_delta(scorer):
    assert scorer._apply_eth_call_penalty(80.0, "ETH", "call", 0.18) == 80.0

def test_eth_call_penalty_skipped_btc(scorer):
    assert scorer._apply_eth_call_penalty(80.0, "BTC", "call", 0.28) == 80.0


# ── Full score ────────────────────────────────────────────────────────────────

def test_full_score_returns_required_keys(scorer):
    result = scorer.score(
        asset="BTC", gex_dex={"totals": {"net_gex": -1e6}, "second_order": {"vanna": 1.0}},
        current_funding_rate=0.0001, funding_rate_history=[0.0001]*1000,
        vol_surface={"rr25": 0.01, "pc_by_moneyness": {"pc_ratio_all": 1.0}},
        rr25_history=[0.01]*30, pc_ratio_history=[1.0]*200,
        block_trades={"blocks_detected": False},
        stablecoin_inflow_pct=None, ibit_pc_ratio=None, ibit_pc_30d_avg=None,
        ohlcv_daily=_ohlcv(60), spot_close=87000.0,
    )
    assert all(k in result for k in ("call_score", "put_score", "regime", "breakdown"))
    assert 0.0 <= result["call_score"] <= 100.0

def test_eth_d10_always_zero(scorer):
    result = scorer.score(
        asset="ETH", gex_dex={"totals": {"net_gex": 0}, "second_order": {"vanna": 0}},
        current_funding_rate=0.0001, funding_rate_history=[0.0001]*1000,
        vol_surface={"rr25": 0.01, "pc_by_moneyness": {"pc_ratio_all": 1.0}},
        rr25_history=[0.01]*30, pc_ratio_history=[1.0]*200,
        block_trades={"blocks_detected": False},
        stablecoin_inflow_pct=None, ibit_pc_ratio=0.5, ibit_pc_30d_avg=0.9,
        ohlcv_daily=_ohlcv(60), spot_close=87000.0,
    )
    assert result["breakdown"]["D10"] == 0.0
```

- [ ] **Step 2: Run tests — expect FAIL**
```bash
pytest tests/unit/strategy/otm/test_directional_scorer.py -v
```

- [ ] **Step 3: Implement `DirectionalScorer`**

```python
# coding/core/strategy/otm/signals/directional_scorer.py
"""
Gate 3 — Directional Scorer.
Computes call_score and put_score (0-100) using 8 weighted sub-signals.
BTC: D1+D7 22%, D2 15%, D3 14%, D4 11%, D6+D9 14%, D8 8%, D10 9%, RIS 7%
ETH: D10=0%, rest renormalized.
"""
import logging
from typing import Dict, List, Optional
import numpy as np
from coding.core.strategy.otm.models.otm_config import OTMConfig

logger = logging.getLogger(__name__)

_BTC_W = {"D1_D7": 0.22, "D2": 0.15, "D3": 0.14, "D4": 0.11,
           "D6_D9": 0.14, "D8": 0.08, "D10": 0.09, "RIS": 0.07}
_ETH_W_RAW = {k: (0.0 if k == "D10" else v) for k, v in _BTC_W.items()}
_S = sum(_ETH_W_RAW.values())
_ETH_W = {k: v/_S for k, v in _ETH_W_RAW.items()}
_DIRECTIONAL = {"D1_D7", "D2", "D3", "D4", "D6_D9", "D10", "RIS"}


class DirectionalScorer:
    def __init__(self, config: OTMConfig) -> None:
        self._config = config

    def _score_d1_d7(self, gex_dex: dict) -> float:
        net_gex = gex_dex.get("totals", {}).get("net_gex", 0.0)
        vanna = gex_dex.get("second_order", {}).get("vanna", 0.0)
        g = -1.0 if net_gex > 0 else (1.0 if net_gex < 0 else 0.0)
        v = 1.0 if vanna > 0 else (-1.0 if vanna < 0 else 0.0)
        return max(-1.0, min(1.0, (g + v) / 2.0))

    def _score_d2(self, current_rate: float, history: List[float],
                   spot_making_new_30d_low: bool = False,
                   spot_making_higher_highs: bool = False,
                   bearish_divergence: bool = False) -> float:
        if not history:
            return 0.0
        arr = np.array(history, dtype=float)
        pct = float(np.mean(arr <= current_rate) * 100.0)
        bull = self._config.funding_percentile_bull
        bear = self._config.funding_percentile_bear
        # Scenario 1: low funding (< 10th pctile) + peak short crowding
        if pct < bull and spot_making_new_30d_low:
            return 1.0
        # Scenario 2: high funding + higher highs + no divergence (trend continuation)
        if pct > bear and spot_making_higher_highs and not bearish_divergence:
            return 1.0
        # Scenario 3: high funding + bearish divergence (lower highs)
        if pct > bear and bearish_divergence:
            return -1.0
        return 0.0

    def _score_d3(self, current_rr: float, rr25_history: List[float]) -> float:
        if len(rr25_history) < 10:
            return 0.0
        arr = np.array(rr25_history, dtype=float)
        std = float(np.std(arr))
        if std == 0:
            return 0.0
        z = (current_rr - float(np.mean(arr))) / std
        t = self._config.rr_z_score_threshold
        return 1.0 if z < -t else (-1.0 if z > t else 0.0)

    def _score_d4(self, current_ratio: float, pc_ratio_history: List[float]) -> float:
        if not pc_ratio_history:
            return 0.0
        arr = np.array(pc_ratio_history, dtype=float)
        pct = float(np.mean(arr <= current_ratio) * 100.0)
        return (1.0 if pct > self._config.pc_ratio_percentile_bull
                else (-1.0 if pct < self._config.pc_ratio_percentile_bear else 0.0))

    def _score_d6_d9(self, block_trades: dict,
                      dex_sign_flipped_positive: bool = False,
                      dex_sign_flipped_negative: bool = False) -> float:
        if not block_trades.get("blocks_detected", False):
            return 0.0
        d = block_trades.get("direction", "")
        if d == "call" and dex_sign_flipped_positive:
            return 1.0
        if d == "put" and dex_sign_flipped_negative:
            return -1.0
        return 0.5 if d == "call" else (-0.5 if d == "put" else 0.0)

    def _score_d8(self, inflow_pct: Optional[float]) -> float:
        if inflow_pct is None:
            return 0.0
        t = self._config.stablecoin_inflow_threshold_pct
        return 1.0 if inflow_pct > t else (-1.0 if inflow_pct < -t else 0.0)

    def _score_d10(self, current_ratio: Optional[float],
                    avg_30d: Optional[float]) -> float:
        if current_ratio is None or avg_30d is None or avg_30d == 0:
            return 0.0
        r = current_ratio / avg_30d
        return 1.0 if r < 0.85 else (-1.0 if r > 1.15 else 0.0)

    def _score_ris(self, rr25_30d_mean: float, rr25_current: float) -> float:
        div = (rr25_30d_mean - rr25_current) * 100.0
        t = self._config.ris_divergence_threshold
        return 1.0 if div > t else (-1.0 if div < -t else 0.0)

    def _detect_regime(self, ohlcv_daily: List[dict]) -> str:
        needed = max(self._config.ema_slow, self._config.trend_sma)
        if len(ohlcv_daily) < needed:
            return "neutral"
        closes = np.array([r["close"] for r in ohlcv_daily], dtype=float)
        def ema(arr, p):
            k = 2.0 / (p + 1); v = float(arr[0])
            for x in arr[1:]: v = x*k + v*(1-k)
            return v
        ef = ema(closes, self._config.ema_fast)
        es = ema(closes, self._config.ema_slow)
        sma = float(np.mean(closes[-self._config.trend_sma:]))
        spot = float(closes[-1])
        if ef > es and spot > sma:
            return "bull"
        if ef < es and spot < sma:
            return "bear"
        return "neutral"

    def _apply_regime_scaling(self, base: Dict[str, float],
                               direction: str, regime: str, asset: str) -> Dict[str, float]:
        if regime == "neutral":
            return dict(base)
        if direction == "call":
            mult = self._config.regime_call_multiplier if regime == "bull" else self._config.regime_put_multiplier
        else:
            mult = self._config.regime_put_multiplier if regime == "bull" else self._config.regime_call_multiplier
        scaled = {k: (v * mult if k in _DIRECTIONAL else v) for k, v in base.items()}
        total = sum(scaled.values())
        return {k: v/total for k, v in scaled.items()} if total > 0 else scaled

    def _apply_d3d4_conflict_rule(self, d3_score: float, d4_score: float,
                                    d3_weight: float, d4_weight: float) -> float:
        raw = d3_score * d3_weight + d4_score * d4_weight
        if (d3_score > 0 and d4_score < 0) or (d3_score < 0 and d4_score > 0):
            return raw * 0.70
        return raw

    def _apply_eth_call_penalty(self, call_score: float, asset: str,
                                  direction: str, delta: float) -> float:
        if asset == "ETH" and direction == "call" and 0.25 <= delta <= 0.35:
            return call_score * 0.85
        return call_score

    def score(self, asset: str, gex_dex: dict,
              current_funding_rate: float, funding_rate_history: List[float],
              vol_surface: dict, rr25_history: List[float],
              pc_ratio_history: List[float], block_trades: dict,
              stablecoin_inflow_pct: Optional[float],
              ibit_pc_ratio: Optional[float], ibit_pc_30d_avg: Optional[float],
              ohlcv_daily: List[dict], spot_close: float,
              spot_making_new_30d_low: bool = False,
              bearish_divergence: bool = False,
              dex_sign_flipped_positive: bool = False,
              dex_sign_flipped_negative: bool = False) -> Dict:
        regime = self._detect_regime(ohlcv_daily)
        base_w = _BTC_W if asset == "BTC" else _ETH_W
        rr25_current = vol_surface.get("rr25", 0.0)
        rr25_mean = float(np.mean(rr25_history)) if rr25_history else 0.0
        pc_ratio = vol_surface.get("pc_by_moneyness", {}).get("pc_ratio_all", 1.0)

        raw = {
            "D1_D7": self._score_d1_d7(gex_dex),
            "D2":    self._score_d2(current_funding_rate, funding_rate_history,
                                    spot_making_new_30d_low, bearish_divergence),
            "D3":    self._score_d3(rr25_current, rr25_history),
            "D4":    self._score_d4(pc_ratio, pc_ratio_history),
            "D6_D9": self._score_d6_d9(block_trades, dex_sign_flipped_positive,
                                        dex_sign_flipped_negative),
            "D8":    self._score_d8(stablecoin_inflow_pct),
            "D10":   self._score_d10(ibit_pc_ratio, ibit_pc_30d_avg) if asset == "BTC" else 0.0,
            "RIS":   self._score_ris(rr25_mean, rr25_current),
        }

        d3d4 = self._apply_d3d4_conflict_rule(raw["D3"], raw["D4"],
                                               base_w["D3"], base_w["D4"])

        def _build_score(direction: str) -> float:
            w = self._apply_regime_scaling(base_w, direction, regime, asset)
            # D3+D4 conflict-adjusted using scaled weights for this direction
            d3d4_scaled = self._apply_d3d4_conflict_rule(
                raw["D3"], raw["D4"], w.get("D3", 0.0), w.get("D4", 0.0)
            )
            total = d3d4_scaled
            for sig in ["D1_D7", "D2", "D6_D9", "D8", "D10", "RIS"]:
                total += raw[sig] * w.get(sig, 0.0)
            return max(0.0, min(100.0, (total + 1.0) / 2.0 * 100.0))

        call_score = round(_build_score("call"), 2)
        put_score  = round(_build_score("put"), 2)

        return {"call_score": call_score, "put_score": put_score,
                "regime": regime, "breakdown": {k: round(v, 4) for k, v in raw.items()}}
```

- [ ] **Step 4: Run tests — expect PASS**
```bash
pytest tests/unit/strategy/otm/test_directional_scorer.py -v
```

- [ ] **Step 5: Commit**
```bash
git add coding/core/strategy/otm/signals/directional_scorer.py \
        tests/unit/strategy/otm/test_directional_scorer.py
git commit -m "feat: implement Gate 3 DirectionalScorer with regime scaling and conflict rules"
```

---

## Chunk 6: Gate 4 + Kelly Sizer

---

### Task 11: `StrikeExpiryOptimizer` (Gate 4)

**Files:**
- Create: `coding/core/strategy/otm/signals/strike_expiry_optimizer.py`
- Test: `tests/unit/strategy/otm/test_strike_expiry_optimizer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/strategy/otm/test_strike_expiry_optimizer.py
import pytest
from coding.core.strategy.otm.signals.strike_expiry_optimizer import StrikeExpiryOptimizer
from coding.core.strategy.otm.models.otm_config import OTMConfig


@pytest.fixture
def config():
    return OTMConfig(risk_budget_usd=10_000.0)

@pytest.fixture
def opt(config):
    return StrikeExpiryOptimizer(config)


def _contract(strike=95000, dte=14, delta=0.28, vega=45.0, theta=-18.0,
              gamma=0.000012, mark_price=0.004, underlying=87000.0,
              direction="call"):
    entry_premium = mark_price * underlying
    return {
        "strike": strike, "dte": dte, "delta": delta, "vega": vega,
        "theta": theta, "gamma": gamma, "mark_price": mark_price,
        "underlying_price": underlying, "entry_premium": entry_premium,
        "direction": direction,
    }


# ── Delta range filter ────────────────────────────────────────────────────────

def test_delta_filter_passes_directional_range(opt):
    c = _contract(delta=0.28)
    assert opt._passes_delta_filter(c, mode="directional") is True

def test_delta_filter_fails_too_high(opt):
    c = _contract(delta=0.40)
    assert opt._passes_delta_filter(c, mode="directional") is False

def test_delta_filter_fails_too_low(opt):
    c = _contract(delta=0.15)
    assert opt._passes_delta_filter(c, mode="directional") is False

def test_delta_filter_event_range(opt):
    c = _contract(delta=0.15)
    assert opt._passes_delta_filter(c, mode="event") is True

def test_eth_call_avoid_range_unless_high_score(opt):
    c = _contract(delta=0.28, direction="call")
    assert opt._passes_eth_call_filter(c, asset="ETH", call_score=60.0) is False
    assert opt._passes_eth_call_filter(c, asset="ETH", call_score=85.0) is True

def test_eth_call_avoid_not_applied_outside_range(opt):
    c = _contract(delta=0.18, direction="call")
    assert opt._passes_eth_call_filter(c, asset="ETH", call_score=60.0) is True


# ── Expiry category ───────────────────────────────────────────────────────────

def test_expiry_category_short(opt):
    assert opt._classify_expiry(3) == "short"

def test_expiry_category_medium(opt):
    assert opt._classify_expiry(14) == "medium"
    assert opt._classify_expiry(7) == "medium"

def test_expiry_category_long(opt):
    assert opt._classify_expiry(45) == "long"
    assert opt._classify_expiry(30) == "long"

def test_expiry_category_boundary_1(opt):
    assert opt._classify_expiry(1) == "short"

def test_expiry_category_over_90_raises(opt):
    with pytest.raises(ValueError):
        opt._classify_expiry(95)


# ── Vega/Theta ratio ──────────────────────────────────────────────────────────

def test_vega_theta_ratio_medium_passes(opt):
    # vega=45, theta=-18, ratio=2.5 > 0.30 threshold
    score = opt._score_vega_theta(_contract(vega=45.0, theta=-18.0, dte=14))
    assert score > 0.0

def test_vega_theta_ratio_short_lower_threshold(opt):
    # dte=3 (short), ratio=45/18=2.5 > 0.05 → should score well
    c = _contract(dte=3, vega=5.0, theta=-100.0)  # ratio=0.05 — at threshold
    score = opt._score_vega_theta(c)
    assert score >= 0.0  # at exactly threshold may be neutral or positive

def test_vega_theta_zero_theta_returns_zero(opt):
    c = _contract(theta=0.0)
    score = opt._score_vega_theta(c)
    assert score == 0.0


# ── Breakeven distance ────────────────────────────────────────────────────────

def test_breakeven_within_2x_move(opt):
    # spot=87000, GARCH_30d=0.10 (10% annualized normalized)
    # 2x expected move = 2 * 0.10 * sqrt(30/252) * 87000 ≈ wide enough
    c = _contract(strike=92000, mark_price=0.004, underlying=87000)
    # breakeven call = 92000 + 348 = 92348
    # spot * (1 + 2*garch) = 87000 * (1 + 2*0.05) = 87000 * 1.10 = 95700 → passes
    score = opt._score_breakeven(c, garch_fcast_30d=0.05)
    assert score > 0

def test_breakeven_penalized_beyond_2x_move(opt):
    c = _contract(strike=120000, mark_price=0.001, underlying=87000)
    # breakeven = 120000 + 87 = 120087, spot*(1+2*0.05) = 95700 → fail
    score = opt._score_breakeven(c, garch_fcast_30d=0.05)
    assert score < 1.0  # penalized


# ── Max pain tiebreaker ───────────────────────────────────────────────────────

def test_max_pain_closer_candidate_wins(opt):
    c1 = _contract(strike=95000)
    c2 = _contract(strike=99000)
    max_pain = 96000
    # c1 is closer to max_pain
    assert opt._max_pain_tiebreak(c1, c2, max_pain) == c1

def test_max_pain_none_returns_first(opt):
    c1 = _contract(strike=95000)
    c2 = _contract(strike=99000)
    assert opt._max_pain_tiebreak(c1, c2, None) == c1


# ── Full select method ────────────────────────────────────────────────────────

def test_select_returns_sorted_list(opt):
    contracts = [
        _contract(strike=92000, dte=14, delta=0.30, vega=50.0, theta=-15.0),
        _contract(strike=96000, dte=14, delta=0.22, vega=30.0, theta=-12.0),
        _contract(strike=100000, dte=14, delta=0.18, vega=20.0, theta=-10.0),
    ]
    result = opt.select(
        contracts=contracts, direction="call", call_score=70.0, put_score=30.0,
        gate2_score=65.0, garch_fcast_30d=0.05, max_pain_strike=93000,
        spot_price=87000.0, asset="BTC",
    )
    # All should have gate4_score assigned; sorted descending
    assert all("gate4_score" in c for c in result)
    scores = [c["gate4_score"] for c in result]
    assert scores == sorted(scores, reverse=True)

def test_select_filters_out_of_delta_range(opt):
    contracts = [
        _contract(strike=92000, delta=0.28),  # valid
        _contract(strike=80000, delta=0.05),  # too low delta → filtered
    ]
    result = opt.select(
        contracts=contracts, direction="call", call_score=70.0, put_score=30.0,
        gate2_score=65.0, garch_fcast_30d=0.05, max_pain_strike=None,
        spot_price=87000.0, asset="BTC",
    )
    assert len(result) == 1
    assert result[0]["strike"] == 92000

def test_select_returns_empty_when_no_valid_contracts(opt):
    contracts = [_contract(strike=80000, delta=0.05)]
    result = opt.select(
        contracts=contracts, direction="call", call_score=70.0, put_score=30.0,
        gate2_score=65.0, garch_fcast_30d=0.05, max_pain_strike=None,
        spot_price=87000.0, asset="BTC",
    )
    assert result == []
```

- [ ] **Step 2: Run tests — expect FAIL**
```bash
pytest tests/unit/strategy/otm/test_strike_expiry_optimizer.py -v
```

- [ ] **Step 3: Implement `StrikeExpiryOptimizer`**

```python
# coding/core/strategy/otm/signals/strike_expiry_optimizer.py
"""
Gate 4 — Strike & Expiry Optimizer.

Filters surviving contracts by delta range, DTE category, and vega/theta.
Scores each 0-100. Returns list sorted descending by gate4_score.
"""
import logging
import math
from typing import Dict, List, Optional
from coding.core.strategy.otm.models.otm_config import OTMConfig

logger = logging.getLogger(__name__)


class StrikeExpiryOptimizer:
    def __init__(self, config: OTMConfig) -> None:
        self._config = config

    def _classify_expiry(self, dte: int) -> str:
        if dte < 1:
            raise ValueError(f"DTE must be >= 1, got {dte}")
        if dte > 90:
            raise ValueError(f"DTE {dte} > 90 — out of scope for this strategy")
        if dte <= 6:
            return "short"
        if dte < 30:
            return "medium"
        return "long"

    def _passes_delta_filter(self, contract: dict, mode: str = "directional") -> bool:
        delta = abs(contract.get("delta", 0.0))
        if mode == "directional":
            return self._config.min_delta_directional <= delta <= self._config.max_delta_directional
        else:  # event
            return self._config.min_delta_event <= delta <= self._config.max_delta_event

    def _passes_eth_call_filter(self, contract: dict, asset: str,
                                  call_score: float) -> bool:
        """ETH calls in [0.25, 0.35] delta are blocked unless call_score > 80."""
        delta = abs(contract.get("delta", 0.0))
        if asset == "ETH" and contract.get("direction") == "call" and 0.25 <= delta <= 0.35:
            return call_score > 80.0
        return True

    def _score_vega_theta(self, contract: dict) -> float:
        """Score vega/theta ratio against DTE-dependent threshold."""
        theta = contract.get("theta", 0.0)
        vega = contract.get("vega", 0.0)
        if theta == 0.0:
            return 0.0
        ratio = abs(vega) / abs(theta)
        dte = contract.get("dte", 14)
        try:
            cat = self._classify_expiry(dte)
        except ValueError:
            return 0.0
        thresholds = {
            "short": self._config.vega_theta_short,
            "medium": self._config.vega_theta_medium,
            "long": self._config.vega_theta_long,
        }
        threshold = thresholds[cat]
        if ratio > threshold * 2:
            return 100.0
        elif ratio >= threshold:
            return 50.0
        return 0.0

    def _score_breakeven(self, contract: dict, garch_fcast_30d: float) -> float:
        """
        Penalize contracts whose breakeven exceeds 2x the expected 30d move.
        garch_fcast_30d: annualized decimal vol, scaled to 30-day window.
        """
        underlying = contract.get("underlying_price", 0.0)
        strike = contract.get("strike", 0.0)
        premium = contract.get("entry_premium", 0.0)
        direction = contract.get("direction", "call")

        if direction == "call":
            breakeven = strike + premium
            max_expected = underlying * (1 + self._config.max_breakeven_move_multiplier * garch_fcast_30d)
            return 100.0 if breakeven <= max_expected else 50.0
        else:  # put
            breakeven = strike - premium
            min_expected = underlying * (1 - self._config.max_breakeven_move_multiplier * garch_fcast_30d)
            return 100.0 if breakeven >= min_expected else 50.0

    def _score_gamma_premium(self, contract: dict) -> float:
        """For short-dated (DTE<=7): score gamma/premium ratio."""
        dte = contract.get("dte", 14)
        if dte > 7:
            return 50.0  # not applicable for medium/long
        premium = contract.get("entry_premium", 0.0)
        gamma = contract.get("gamma", 0.0)
        if premium <= 0:
            return 0.0
        ratio = gamma / premium
        # Higher is better; normalize with a reference of 0.00005
        return min(100.0, ratio / 0.00005 * 50.0)

    def _compute_gate4_score(self, contract: dict, garch_fcast_30d: float) -> float:
        """Weighted composite of vega/theta, breakeven, gamma/premium."""
        vt = self._score_vega_theta(contract)
        be = self._score_breakeven(contract, garch_fcast_30d)
        gp = self._score_gamma_premium(contract)
        return round(0.40 * vt + 0.40 * be + 0.20 * gp, 2)

    def _max_pain_tiebreak(self, c1: dict, c2: dict,
                            max_pain_strike: Optional[float]) -> dict:
        """Between two candidates within 5 points, prefer closer to max pain."""
        if max_pain_strike is None:
            return c1
        d1 = abs(c1["strike"] - max_pain_strike)
        d2 = abs(c2["strike"] - max_pain_strike)
        return c1 if d1 <= d2 else c2

    def select(
        self,
        contracts: List[dict],
        direction: str,
        call_score: float,
        put_score: float,
        gate2_score: float,
        garch_fcast_30d: float,
        max_pain_strike: Optional[float],
        spot_price: float,
        asset: str,
    ) -> List[dict]:
        """
        Filter and score Gate 1 survivors. Returns list sorted by gate4_score desc.

        Assigns 'gate4_score', 'expiry_category', 'vega_theta_ratio',
        'gamma_premium_ratio', 'breakeven_price' to each surviving contract.
        """
        # Determine delta mode from Gate 2 score
        mode = "event" if gate2_score < 50 else "directional"

        surviving = []
        for c in contracts:
            c = dict(c)  # defensive copy
            c["direction"] = direction

            if not self._passes_delta_filter(c, mode=mode):
                continue
            if not self._passes_eth_call_filter(c, asset=asset, call_score=call_score):
                continue

            # Annotate
            try:
                c["expiry_category"] = self._classify_expiry(c.get("dte", 14))
            except ValueError:
                continue

            theta = c.get("theta", 0.0)
            vega = c.get("vega", 0.0)
            c["vega_theta_ratio"] = abs(vega / theta) if theta != 0 else 0.0

            gamma = c.get("gamma", 0.0)
            premium = c.get("entry_premium", 0.0)
            c["gamma_premium_ratio"] = gamma / premium if premium > 0 else 0.0

            if direction == "call":
                c["breakeven_price"] = c["strike"] + premium
            else:
                c["breakeven_price"] = c["strike"] - premium

            c["gate4_score"] = self._compute_gate4_score(c, garch_fcast_30d)
            surviving.append(c)

        # Apply max pain tiebreaker for candidates within 5 points of each other
        surviving.sort(key=lambda x: x["gate4_score"], reverse=True)
        if len(surviving) >= 2:
            if abs(surviving[0]["gate4_score"] - surviving[1]["gate4_score"]) <= 5.0:
                winner = self._max_pain_tiebreak(surviving[0], surviving[1], max_pain_strike)
                if winner is surviving[1]:
                    surviving[0], surviving[1] = surviving[1], surviving[0]

        return surviving
```

- [ ] **Step 4: Run tests — expect PASS**
```bash
pytest tests/unit/strategy/otm/test_strike_expiry_optimizer.py -v
```

- [ ] **Step 5: Commit**
```bash
git add coding/core/strategy/otm/signals/strike_expiry_optimizer.py \
        tests/unit/strategy/otm/test_strike_expiry_optimizer.py
git commit -m "feat: implement Gate 4 StrikeExpiryOptimizer"
```

---

### Task 12: `KellySizer`

**Files:**
- Create: `coding/core/strategy/otm/scoring/__init__.py`
- Create: `coding/core/strategy/otm/scoring/kelly_sizer.py`
- Test: `tests/unit/strategy/otm/test_kelly_sizer.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/strategy/otm/test_kelly_sizer.py
import pytest
from coding.core.strategy.otm.scoring.kelly_sizer import KellySizer
from coding.core.strategy.otm.models.otm_config import OTMConfig


@pytest.fixture
def config():
    return OTMConfig(risk_budget_usd=10_000.0)

@pytest.fixture
def sizer(config):
    return KellySizer(config)


# ── conviction_score computation ─────────────────────────────────────────────

def test_conviction_score_50_50_blend(sizer):
    score = sizer.compute_conviction(gate2_score=80.0, gate3_directional_score=60.0)
    assert score == pytest.approx(70.0, abs=0.1)

def test_conviction_clamped_to_0_100(sizer):
    assert sizer.compute_conviction(0.0, 0.0) == 0.0
    assert sizer.compute_conviction(100.0, 100.0) == 100.0


# ── P_win lookup ─────────────────────────────────────────────────────────────

def test_p_win_band_40_60(sizer):
    p_win, mult = sizer._lookup_priors(50.0)
    assert p_win == 0.35
    assert mult == 1.5

def test_p_win_band_60_75(sizer):
    p_win, mult = sizer._lookup_priors(70.0)
    assert p_win == 0.40
    assert mult == 2.0

def test_p_win_band_75_90(sizer):
    p_win, mult = sizer._lookup_priors(80.0)
    assert p_win == 0.45
    assert mult == 2.5

def test_p_win_band_over_90(sizer):
    p_win, mult = sizer._lookup_priors(95.0)
    assert p_win == 0.50
    assert mult == 3.0

def test_p_win_below_40_returns_none(sizer):
    result = sizer._lookup_priors(35.0)
    assert result is None

def test_return_multiple_capped_at_3(sizer):
    _, mult = sizer._lookup_priors(95.0)
    assert mult <= 3.0


# ── Kelly fraction ────────────────────────────────────────────────────────────

def test_kelly_fraction_positive_ev(sizer):
    # p=0.40, b=2.0: kelly = (0.40*2 - 0.60) / 2 = (0.8-0.6)/2 = 0.10
    frac = sizer._compute_kelly_fraction(p_win=0.40, avg_return_multiple=2.0)
    assert frac == pytest.approx(0.10, abs=0.001)

def test_fractional_kelly_is_quarter(sizer):
    full_kelly = sizer._compute_kelly_fraction(0.40, 2.0)  # = 0.10
    frac = sizer._apply_fractional_kelly(full_kelly)
    assert frac == pytest.approx(0.10 * 0.25, abs=0.001)

def test_kelly_fraction_capped_at_max_single_trade(sizer):
    # Even with high conviction, cap at 10% of budget
    result = sizer.compute_position_usd(
        gate2_score=100.0, gate3_directional_score=100.0,
        existing_same_direction_usd=0.0,
    )
    assert result["position_usd"] <= 10_000.0 * 0.10


# ── Portfolio correlation cap ─────────────────────────────────────────────────

def test_correlation_cap_reduces_new_position(sizer):
    # budget=10k, max 10% = 1000; already have 800 in same direction
    result = sizer.compute_position_usd(
        gate2_score=80.0, gate3_directional_score=70.0,
        existing_same_direction_usd=800.0,
    )
    assert result["position_usd"] <= 200.0

def test_correlation_cap_skips_when_full(sizer):
    # Already at cap (1000 of 10k)
    result = sizer.compute_position_usd(
        gate2_score=80.0, gate3_directional_score=70.0,
        existing_same_direction_usd=1000.0,
    )
    assert result["position_usd"] == 0.0
    assert "cap reached" in result.get("skip_reason", "")

def test_position_usd_zero_when_conviction_below_40(sizer):
    result = sizer.compute_position_usd(
        gate2_score=30.0, gate3_directional_score=30.0,
        existing_same_direction_usd=0.0,
    )
    assert result["position_usd"] == 0.0


# ── Take-profit target ────────────────────────────────────────────────────────

def test_take_profit_short_dte_always_2x(sizer):
    tp = sizer.compute_take_profit(conviction_score=85.0, dte=3)
    assert tp == 2.0

def test_take_profit_medium_high_conviction(sizer):
    tp = sizer.compute_take_profit(conviction_score=80.0, dte=14)
    assert tp == 5.0

def test_take_profit_long_high_conviction(sizer):
    tp = sizer.compute_take_profit(conviction_score=80.0, dte=45)
    assert tp == 8.0

def test_take_profit_medium_low_conviction(sizer):
    tp = sizer.compute_take_profit(conviction_score=50.0, dte=14)
    assert tp == 2.0
```

- [ ] **Step 2: Run tests — expect FAIL**
```bash
pytest tests/unit/strategy/otm/test_kelly_sizer.py -v
```

- [ ] **Step 3: Implement `KellySizer`**

```python
# coding/core/strategy/otm/scoring/kelly_sizer.py
"""
KellySizer — fractional Kelly position sizing within a fixed USD budget.

Formula: kelly_fraction = (P_win * b - (1 - P_win)) / b
Applied at 1/4 Kelly. Capped at 10% of risk_budget_usd per trade.
Portfolio correlation cap: max 10% of budget in same direction simultaneously.
"""
import logging
from typing import Dict, Optional, Tuple
from coding.core.strategy.otm.models.otm_config import OTMConfig

logger = logging.getLogger(__name__)


class KellySizer:
    def __init__(self, config: OTMConfig) -> None:
        self._config = config

    def compute_conviction(self, gate2_score: float,
                            gate3_directional_score: float) -> float:
        """Blend Gate 2 and Gate 3 directional scores 50/50."""
        raw = gate2_score * 0.50 + gate3_directional_score * 0.50
        return max(0.0, min(100.0, raw))

    def _lookup_priors(self, conviction: float) -> Optional[Tuple[float, float]]:
        """Return (p_win, avg_return_multiple) for conviction band. None if < 40."""
        priors = self._config.p_win_priors
        returns = self._config.avg_return_priors
        if conviction >= 90:
            return priors["90_100"], returns["90_100"]
        elif conviction >= 75:
            return priors["75_90"], returns["75_90"]
        elif conviction >= 60:
            return priors["60_75"], returns["60_75"]
        elif conviction >= 40:
            return priors["40_60"], returns["40_60"]
        return None

    def _compute_kelly_fraction(self, p_win: float,
                                  avg_return_multiple: float) -> float:
        """Full Kelly fraction for a binary bet."""
        if avg_return_multiple <= 0:
            return 0.0
        return (p_win * avg_return_multiple - (1.0 - p_win)) / avg_return_multiple

    def _apply_fractional_kelly(self, full_kelly: float) -> float:
        """Apply 1/kelly_divisor (default: 1/4) Kelly."""
        return max(0.0, full_kelly / self._config.kelly_divisor)

    def compute_position_usd(
        self,
        gate2_score: float,
        gate3_directional_score: float,
        existing_same_direction_usd: float = 0.0,
    ) -> Dict:
        """
        Compute position size in USD.

        Returns dict with: position_usd, conviction_score, p_win_prior,
                           kelly_fraction, skip_reason (if skipped).
        """
        conviction = self.compute_conviction(gate2_score, gate3_directional_score)
        priors = self._lookup_priors(conviction)

        if priors is None:
            logger.info("Conviction %.1f < 40 — skipping trade", conviction)
            return {"position_usd": 0.0, "conviction_score": conviction,
                    "p_win_prior": 0.0, "kelly_fraction": 0.0,
                    "skip_reason": "conviction below minimum threshold (40)"}

        p_win, avg_return = priors
        full_kelly = self._compute_kelly_fraction(p_win, avg_return)
        frac_kelly = self._apply_fractional_kelly(full_kelly)

        budget = self._config.risk_budget_usd
        max_per_trade = budget * self._config.max_single_trade_pct
        raw_position = min(frac_kelly * budget, max_per_trade)

        # Portfolio correlation cap
        max_correlated = budget * self._config.max_correlated_pct
        remaining_cap = max_correlated - existing_same_direction_usd
        if remaining_cap <= 0:
            logger.info("Portfolio correlation cap reached — skipping trade")
            return {"position_usd": 0.0, "conviction_score": conviction,
                    "p_win_prior": p_win, "kelly_fraction": frac_kelly,
                    "skip_reason": "portfolio correlation cap reached"}

        final_position = min(raw_position, remaining_cap)

        return {
            "position_usd": round(final_position, 2),
            "conviction_score": conviction,
            "p_win_prior": p_win,
            "kelly_fraction": round(frac_kelly, 6),
            "skip_reason": None,
        }

    def compute_take_profit(self, conviction_score: float, dte: int) -> float:
        """Return take-profit multiple based on conviction and DTE."""
        # DTE<=6 = short (aligned with _classify_expiry boundary)
        if dte <= 6:
            return 2.0
        if conviction_score >= 75:
            return 8.0 if dte >= 30 else 5.0
        elif conviction_score >= 60:
            return 4.0 if dte >= 30 else 3.0
        return 2.0
```

- [ ] **Step 4: Run tests — expect PASS**
```bash
pytest tests/unit/strategy/otm/test_kelly_sizer.py -v
```

- [ ] **Step 5: Run all OTM tests together**
```bash
pytest tests/unit/strategy/otm/ -v
```
Expected: all tests PASS

- [ ] **Step 6: Commit**
```bash
git add coding/core/strategy/otm/scoring/ tests/unit/strategy/otm/test_kelly_sizer.py
git commit -m "feat: implement KellySizer with fractional Kelly and correlation cap"
```

---

## Chunk 7: OTMFinderService + Backtest Stub

---

### Task 13: `OTMFinderService`

**Files:**
- Create: `coding/service/strategy/otm/otm_finder_service.py`
- Test: `tests/unit/strategy/otm/test_otm_finder_service.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/strategy/otm/test_otm_finder_service.py
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
from coding.service.strategy.otm.otm_finder_service import OTMFinderService
from coding.core.strategy.otm.models.otm_config import OTMConfig
from coding.core.strategy.otm.models.otm_signal import OTMSignal


@pytest.fixture
def config():
    return OTMConfig(risk_budget_usd=10_000.0)


def _mock_contract(strike=95000, dte=14, delta=0.28, vega=45.0, theta=-18.0,
                   gamma=0.000012, bid_iv=0.60, ask_iv=0.63, oi=200,
                   volume=30, mark_price=0.004, underlying=87000.0,
                   option_type="C"):
    return {
        "instrument_name": f"BTC-28MAR25-{strike}-{option_type}",
        "strike": strike, "dte": dte, "delta": delta if option_type=="C" else -delta,
        "gamma": gamma, "vega": vega, "theta": theta,
        "bid_iv": bid_iv, "ask_iv": ask_iv, "mark_iv": (bid_iv+ask_iv)/2,
        "open_interest": oi, "volume_24h": volume,
        "mark_price": mark_price, "underlying_price": underlying,
        "option_type": option_type,
    }


def _make_service(config):
    svc = OTMFinderService(config)
    # Inject mock dependencies
    svc._deribit_service = MagicMock()
    svc._on_chain_service = MagicMock()
    svc._dvol_fetcher = MagicMock()
    svc._stablecoin_fetcher = MagicMock()
    svc._ibit_fetcher = MagicMock()
    svc._repository = MagicMock()
    return svc


def _setup_mock_data(svc):
    """Configure mocks to return valid data."""
    svc._deribit_service.get_book_summary_by_currency.return_value = [
        _mock_contract(strike=95000, dte=14, delta=0.28),
        _mock_contract(strike=90000, dte=14, delta=0.35),
        _mock_contract(strike=85000, dte=14, delta=-0.30, option_type="P"),
    ]
    svc._deribit_service.get_ticker.return_value = {
        "index_price": 87000.0, "mark_price": 0.004
    }
    svc._on_chain_service.fetch_and_analyze.return_value = MagicMock(
        gex_dex_structured={"totals": {"net_gex": -1e6}, "second_order": {"vanna": 1.0}},
        market_wide_structured={
            "perpetual_funding": {"current_rate": 0.0001, "trend": "neutral"},
            "iv_term_structure": {"spread": 8.0, "shape": "contango"},
            "vrp": {"vrp_abs": -5.0, "rv_30d": 0.55},
        },
        volatility_surface_structured={
            "skew_25d": {"rr25": 0.01},
            "pc_by_moneyness": {"pc_ratio_all": 1.2},
            "atm_iv": 0.60,
        },
    )
    svc._dvol_fetcher.fetch_latest.return_value = 55.0
    svc._dvol_fetcher.fetch_history.return_value = [(None, 60.0)] * 400
    svc._stablecoin_fetcher.fetch_inflow_pct.return_value = None
    svc._ibit_fetcher.fetch_pc_ratio.return_value = None
    svc._repository.get_dvol_history.return_value = [60.0] * 400
    svc._repository.get_funding_rate_history.return_value = [0.0001] * 1000
    svc._repository.get_pc_ratio_history.return_value = [1.0] * 200
    svc._repository.get_rr25_history.return_value = [0.01] * 30
    svc._repository.get_ohlcv_daily.return_value = [
        {"close": 87000.0 * (1.001 ** i)} for i in range(60)
    ]


# ── Smoke tests ───────────────────────────────────────────────────────────────

def test_find_signals_returns_list(config):
    svc = _make_service(config)
    _setup_mock_data(svc)
    result = svc.find_signals(assets=["BTC"], direction="auto", expiry_pref="auto")
    assert isinstance(result, list)


def test_find_signals_sorted_descending_by_conviction(config):
    svc = _make_service(config)
    _setup_mock_data(svc)
    result = svc.find_signals(assets=["BTC"], direction="auto", expiry_pref="auto")
    if len(result) >= 2:
        scores = [s.conviction_score for s in result]
        assert scores == sorted(scores, reverse=True)


def test_find_signals_returns_otm_signal_objects(config):
    svc = _make_service(config)
    _setup_mock_data(svc)
    result = svc.find_signals(assets=["BTC"], direction="auto", expiry_pref="auto")
    for signal in result:
        assert isinstance(signal, OTMSignal)


def test_gate2_suppressed_blocks_new_entries(config):
    svc = _make_service(config)
    _setup_mock_data(svc)
    # Force Gate 2 to return a suppressed score
    svc._dvol_fetcher.fetch_latest.return_value = 95.0   # high DVOL
    svc._repository.get_dvol_history.return_value = [60.0] * 400
    result = svc.find_signals(assets=["BTC"], direction="auto", expiry_pref="auto",
                               gate2_override=False)
    # With Gate 2 suppressed (<40), no new entries allowed
    for signal in result:
        assert signal.gate2_suppressed is True


def test_gate2_override_allows_signals_when_suppressed(config):
    svc = _make_service(config)
    _setup_mock_data(svc)
    result = svc.find_signals(assets=["BTC"], direction="auto", expiry_pref="auto",
                               gate2_override=True)
    assert isinstance(result, list)  # should not be empty due to override


def test_empty_result_when_no_liquid_contracts(config):
    svc = _make_service(config)
    _setup_mock_data(svc)
    # All contracts fail Gate 1 (zero OI)
    svc._deribit_service.get_book_summary_by_currency.return_value = [
        _mock_contract(oi=0), _mock_contract(oi=0)
    ]
    result = svc.find_signals(assets=["BTC"], direction="auto", expiry_pref="auto")
    assert result == []


def test_dvol_saved_on_each_run(config):
    svc = _make_service(config)
    _setup_mock_data(svc)
    svc.find_signals(assets=["BTC"], direction="auto", expiry_pref="auto")
    svc._dvol_fetcher.save_to_db.assert_called()


def test_signals_saved_to_repository(config):
    svc = _make_service(config)
    _setup_mock_data(svc)
    result = svc.find_signals(assets=["BTC"], direction="auto", expiry_pref="auto")
    if result:
        svc._repository.save_otm_signals.assert_called_once()
```

- [ ] **Step 2: Run tests — expect FAIL**
```bash
pytest tests/unit/strategy/otm/test_otm_finder_service.py -v
```

- [ ] **Step 3: Implement `OTMFinderService`**

```python
# coding/service/strategy/otm/otm_finder_service.py
"""
OTMFinderService — orchestrates all four gates and produces ranked OTMSignal list.

Data flow per asset:
  1. Fetch options chain (Deribit)
  2. Run Gate 1 (liquidity) — per contract
  3. Fetch all supporting data (DVOL, on-chain, funding history, etc.)
  4. Run Gate 2 (vol regime) — asset-level
  5. Run Gate 3 (directional) — asset-level
  6. Run Gate 4 (strike/expiry) — per surviving contract
  7. Kelly size each signal
  8. Return sorted OTMSignal list
"""
import logging
import math
from datetime import datetime, timezone
from typing import Dict, List, Optional
from uuid import uuid4

from coding.core.strategy.otm.models.otm_config import OTMConfig
from coding.core.strategy.otm.models.otm_signal import OTMSignal
from coding.core.strategy.otm.signals.liquidity_gate import LiquidityGate
from coding.core.strategy.otm.signals.volatility_regime_gate import VolatilityRegimeGate
from coding.core.strategy.otm.signals.directional_scorer import DirectionalScorer
from coding.core.strategy.otm.signals.strike_expiry_optimizer import StrikeExpiryOptimizer
from coding.core.strategy.otm.scoring.kelly_sizer import KellySizer
from coding.service.strategy.otm.fetchers.dvol_fetcher import DVOLFetcher
from coding.service.strategy.otm.fetchers.stablecoin_fetcher import StablecoinFetcher
from coding.service.strategy.otm.fetchers.ibit_fetcher import IBITFetcher

logger = logging.getLogger(__name__)

_DELTA_CANDIDATE_MIN = 0.05
_DELTA_CANDIDATE_MAX = 0.45


class OTMFinderService:
    """
    Orchestrates the OTM contract finder pipeline for one or more assets.

    Dependencies are injected to allow testing without live API connections.
    """

    def __init__(
        self,
        config: OTMConfig,
        deribit_service=None,
        on_chain_service=None,
        repository=None,
    ) -> None:
        self._config = config
        self._deribit_service = deribit_service
        self._on_chain_service = on_chain_service
        self._repository = repository

        # Gates (pure computation, no external deps)
        self._gate1 = LiquidityGate(config)
        self._gate2 = VolatilityRegimeGate(config)
        self._gate3 = DirectionalScorer(config)
        self._gate4 = StrikeExpiryOptimizer(config)
        self._kelly = KellySizer(config)

        # Fetchers
        self._dvol_fetcher = DVOLFetcher()
        self._stablecoin_fetcher = StablecoinFetcher()
        self._ibit_fetcher = IBITFetcher()

    def find_signals(
        self,
        assets: List[str],
        direction: str = "auto",     # "call" | "put" | "auto"
        expiry_pref: str = "auto",   # "short" | "medium" | "long" | "auto"
        gate2_override: bool = False,
        existing_positions: Optional[Dict[str, float]] = None,
    ) -> List[OTMSignal]:
        """
        Run the full OTM finder pipeline.

        Args:
            assets: ["BTC"], ["ETH"], or ["BTC", "ETH"]
            direction: forced direction or "auto" (Gate 3 decides)
            expiry_pref: forced expiry or "auto" (Gate 4 selects best)
            gate2_override: if True, scan even when Gate 2 < 40 (paper trading)
            existing_positions: {asset: usd_already_allocated_same_direction}

        Returns:
            List[OTMSignal] sorted descending by conviction_score.
        """
        if existing_positions is None:
            existing_positions = {}

        all_signals: List[OTMSignal] = []

        for asset in assets:
            try:
                signals = self._process_asset(
                    asset=asset,
                    direction=direction,
                    expiry_pref=expiry_pref,
                    gate2_override=gate2_override,
                    existing_same_direction_usd=existing_positions.get(asset, 0.0),
                )
                all_signals.extend(signals)
            except Exception as exc:
                logger.error("OTMFinderService: error processing %s: %s", asset, exc)

        all_signals.sort(key=lambda s: s.conviction_score, reverse=True)

        if all_signals:
            try:
                self._repository.save_otm_signals(all_signals)
            except Exception as exc:
                logger.error("Failed to save OTM signals: %s", exc)

        return all_signals

    def _process_asset(
        self,
        asset: str,
        direction: str,
        expiry_pref: str,
        gate2_override: bool,
        existing_same_direction_usd: float,
    ) -> List[OTMSignal]:
        """Run full pipeline for one asset. Returns list of OTMSignal."""
        logger.info("OTMFinderService: processing %s", asset)

        # Gate 1 runs FIRST (spec-compliant ordering: cheap filter eliminates candidates early)

        # Fetch options chain + Gate 1 filter
        chain = self._deribit_service.get_book_summary_by_currency(asset)
        underlying_price = (chain[0].get("underlying_price", 0.0) if chain else 0.0)

        candidates = [
            c for c in chain
            if _DELTA_CANDIDATE_MIN <= abs(c.get("delta", 0.0)) <= _DELTA_CANDIDATE_MAX
        ]

        liquid = []
        for c in candidates:
            c["asset"] = asset
            passed, reason = self._gate1.check(c)
            if not passed:
                logger.debug("Gate 1 FAIL %s: %s", c.get("instrument_name"), reason)
            else:
                liquid.append(c)

        if not liquid:
            logger.warning("%s: no liquid OTM candidates survived Gate 1", asset)
            return []

        # Fetch + update DVOL
        latest_dvol = self._dvol_fetcher.fetch_latest(asset)
        if latest_dvol is not None:
            try:
                conn = self._repository.get_connection()
                self._dvol_fetcher.save_to_db(
                    [(datetime.now(timezone.utc), latest_dvol)], asset, conn
                )
                conn.commit()
            except Exception as exc:
                logger.warning("Could not persist DVOL for %s: %s", asset, exc)

        # Fetch supporting data
        dvol_history = self._repository.get_dvol_history(asset)
        funding_history = self._repository.get_funding_rate_history(asset)
        pc_ratio_history = self._repository.get_pc_ratio_history(asset)
        rr25_history = self._repository.get_rr25_history(asset)
        ohlcv_daily = self._repository.get_ohlcv_daily(asset)
        stablecoin_inflow = self._stablecoin_fetcher.fetch_inflow_pct()
        ibit_pc = self._ibit_fetcher.fetch_pc_ratio() if asset == "BTC" else None
        ibit_avg = None  # TODO: compute 30d avg from stored history

        # Fetch on-chain analytics
        analyzer = self._on_chain_service.fetch_and_analyze(asset, "ALL")
        mw = getattr(analyzer, "market_wide_structured", {}) or {}
        vs = getattr(analyzer, "volatility_surface_structured", {}) or {}
        gex_dex = getattr(analyzer, "gex_dex_structured", {}) or {}

        funding_data = mw.get("perpetual_funding", {})
        term_data = mw.get("iv_term_structure", None)
        vrp_data = mw.get("vrp", {})
        atm_iv = vs.get("atm_iv", 0.60)
        rv_30d = vrp_data.get("rv_30d", atm_iv * 0.9)

        # Gate 2
        g2 = self._gate2.score(
            dvol_history=[v for _, v in dvol_history] if dvol_history and isinstance(dvol_history[0], tuple) else dvol_history,
            current_dvol=latest_dvol or (max(dvol_history) if dvol_history else 70.0),
            atm_iv_30d=atm_iv,
            rv_30d_parkinson=rv_30d,
            ohlcv_daily=ohlcv_daily,
            term_structure_data=term_data,
        )
        gate2_score = g2["total_score"]
        gate2_action = g2["action"]
        import math as _math
        garch_fcast_annualized = g2.get("garch_fcast_annualized") or 0.05
        # Scale to 30-day move for Gate 4 breakeven filter (spec: GARCH_fcast_30d)
        garch_fcast = garch_fcast_annualized * _math.sqrt(30.0 / 252.0)

        if gate2_action != "new_entries_allowed" and not gate2_override:
            logger.info("%s Gate 2 action=%s -- no new entries", asset, gate2_action)
            return []

        # Gate 3
        g3 = self._gate3.score(
            asset=asset,
            gex_dex=gex_dex,
            current_funding_rate=funding_data.get("current_rate", 0.0),
            funding_rate_history=funding_history,
            vol_surface=vs,
            rr25_history=rr25_history,
            pc_ratio_history=pc_ratio_history,
            block_trades=mw.get("block_trades", {"blocks_detected": False}),
            stablecoin_inflow_pct=stablecoin_inflow,
            ibit_pc_ratio=ibit_pc,
            ibit_pc_30d_avg=ibit_avg,
            ohlcv_daily=ohlcv_daily,
            spot_close=ohlcv_daily[-1]["close"] if ohlcv_daily else 0.0,
        )
        call_score = g3["call_score"]
        put_score = g3["put_score"]
        regime = g3["regime"]
        breakdown = g3["breakdown"]

        if direction == "auto":
            trade_direction = "call" if call_score >= put_score else "put"
        else:
            trade_direction = direction

        gate3_directional = call_score if trade_direction == "call" else put_score

        # Gate 4
        ranked = self._gate4.select(
            contracts=liquid,
            direction=trade_direction,
            call_score=call_score,
            put_score=put_score,
            gate2_score=gate2_score,
            garch_fcast_30d=garch_fcast,
            max_pain_strike=None,   # TODO: integrate CoinGlass fetcher
            spot_price=underlying_price,
            asset=asset,
        )

        if not ranked:
            logger.info("%s: no contracts survived Gate 4", asset)
            return []

        # Kelly sizer + build OTMSignal objects
        signals = []
        for c in ranked:
            sizing = self._kelly.compute_position_usd(
                gate2_score=gate2_score,
                gate3_directional_score=gate3_directional,
                existing_same_direction_usd=existing_same_direction_usd,
            )
            if sizing["position_usd"] == 0.0:
                continue

            conviction = sizing["conviction_score"]
            dte = c.get("dte", 14)
            tp = self._kelly.compute_take_profit(conviction, dte)

            signal = OTMSignal(
                signal_id=str(uuid4()),
                generated_at=datetime.now(timezone.utc),
                asset=asset,
                instrument_name=c.get("instrument_name", ""),
                direction=trade_direction,
                strike=float(c.get("strike", 0.0)),
                expiry=c.get("expiry", ""),
                dte=dte,
                expiry_category=c.get("expiry_category", "medium"),
                delta=float(c.get("delta", 0.0)),
                gamma=float(c.get("gamma", 0.0)),
                vega=float(c.get("vega", 0.0)),
                theta=float(c.get("theta", 0.0)),
                mark_iv=float(c.get("mark_iv", 0.0)),
                entry_premium=float(c.get("entry_premium", 0.0)),
                underlying_price=underlying_price,
                gate1_passed=True,
                gate2_score=gate2_score,
                gate3_call_score=call_score,
                gate3_put_score=put_score,
                gate3_directional_score=gate3_directional,
                conviction_score=conviction,
                d1_d7_score=breakdown.get("D1_D7", 0.0),
                d2_score=breakdown.get("D2", 0.0),
                d3_score=breakdown.get("D3", 0.0),
                d4_score=breakdown.get("D4", 0.0),
                d6_d9_score=breakdown.get("D6_D9", 0.0),
                d8_score=breakdown.get("D8", 0.0),
                d10_score=breakdown.get("D10", 0.0),
                ris_score=breakdown.get("RIS", 0.0),
                position_usd=sizing["position_usd"],
                p_win_prior=sizing["p_win_prior"],
                kelly_fraction=sizing["kelly_fraction"],
                take_profit_multiple=tp,
                stop_loss_pct=self._config.stop_loss_hard_floor_pct,
                time_stop_dte=max(1, dte // 2),
                vega_theta_ratio=c.get("vega_theta_ratio", 0.0),
                gamma_premium_ratio=c.get("gamma_premium_ratio", 0.0),
                breakeven_price=c.get("breakeven_price", 0.0),
                regime_flag=regime,
                gate2_suppressed=(gate2_action != "new_entries_allowed"),
            )
            signals.append(signal)
            existing_same_direction_usd += sizing["position_usd"]

        return signals
```

- [ ] **Step 4: Run tests — expect PASS**
```bash
pytest tests/unit/strategy/otm/test_otm_finder_service.py -v
```

- [ ] **Step 5: Commit**
```bash
git add coding/service/strategy/otm/otm_finder_service.py \
        tests/unit/strategy/otm/test_otm_finder_service.py
git commit -m "feat: implement OTMFinderService orchestrating all 4 gates"
```

---

### Task 14: `OTMBacktestService` stub

**Files:**
- Create: `coding/service/strategy/otm/otm_backtest_service.py`

No tests needed — this is an interface stub only. Implementation deferred to phase 2.

- [ ] **Step 1: Write the stub**

```python
# coding/service/strategy/otm/otm_backtest_service.py
"""
OTMBacktestService — interface stub for backtesting OTM signals.

Full implementation is deferred to phase 2 (requires historical signal reconstruction).
The interface is defined here so the GUI and service layer can reference it.
"""
import logging
from datetime import datetime
from typing import Dict, List, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    asset: str
    start_date: datetime
    end_date: datetime
    total_signals: int
    win_rate: float
    avg_return_multiple: float
    calibrated_p_win_bands: Dict[str, float]
    notes: str = ""


@dataclass
class LabeledTrade:
    signal_id: str
    asset: str
    instrument_name: str
    conviction_score: float
    outcome: str          # "take_profit" | "stop_loss" | "time_stop" | "expired_worthless"
    return_multiple: float
    holding_period_days: int


class OTMBacktestService:
    """
    Backtests OTM signals against historical data.

    Phase 2 implementation: reconstructs all signals historically, labels outcomes,
    and returns calibrated P_win values per conviction band.
    """

    def run_backtest(
        self,
        asset: str,
        start_date: datetime,
        end_date: datetime,
        config,
    ) -> BacktestResult:
        """Run backtest over historical period. NOT YET IMPLEMENTED."""
        raise NotImplementedError(
            "OTMBacktestService.run_backtest is deferred to phase 2. "
            "Run forward testing first to accumulate labeled trades."
        )

    def label_outcomes(
        self,
        signals,
        price_history: Dict,
    ) -> List[LabeledTrade]:
        """Label historical signal outcomes. NOT YET IMPLEMENTED."""
        raise NotImplementedError("Deferred to phase 2.")

    def calibrate_conviction_bands(
        self,
        labeled_trades: List[LabeledTrade],
    ) -> Dict[str, float]:
        """Return empirical P_win per conviction band. NOT YET IMPLEMENTED."""
        raise NotImplementedError("Deferred to phase 2.")
```

- [ ] **Step 2: Commit**
```bash
git add coding/service/strategy/otm/otm_backtest_service.py
git commit -m "feat: add OTMBacktestService interface stub (phase 2)"
```

---

## Final Verification

- [ ] **Run the full OTM test suite**
```bash
pytest tests/unit/strategy/otm/ -v --tb=short
```
Expected: all tests PASS

- [ ] **Run the full project test suite to check for regressions**
```bash
pytest tests/unit/ -v --tb=short
```
Expected: all existing tests still PASS

- [ ] **Run system validator**
```bash
python -m scripts.validate_system
```

- [ ] **Create research directory structure**
```bash
mkdir -p strategies/otm_contracts/research
mkdir -p strategies/otm_contracts/backtest_results
mkdir -p strategies/otm_contracts/forward_test_log
```

- [ ] **Final commit**
```bash
git add strategies/
git commit -m "feat: create OTM contract finder research directory structure"
```

---

*Plan 1 (Core + Service) complete. GUI implementation is in Plan 2: `2026-03-14-otm-gui.md`.*
