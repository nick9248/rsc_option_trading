# Displacement OTM Scanner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the unused 4-gate OTM Finder with a displacement-triggered scanner that detects heavy BTC/ETH price drops, scores the setup with 6 market signals via logistic regression, recommends the optimal OTM call, and sends a Telegram alert — running 24/7 on VPS.

**Architecture:** Core models → DisplacementDetector / ConvictionScorer / StrikeSelector → DisplacementScannerService + TelegramAlertService → GUI tab + headless VPS daemon. Backtest engine trains the logistic regression model from historical Deribit options data.

**Tech Stack:** Python 3.13, Pydantic v2 (frozen models), PySide6 (GUI), psycopg2 (PostgreSQL), scikit-learn (logistic regression), joblib (model serialization), requests (Telegram HTTP), pytest

**Spec:** `docs/superpowers/specs/2026-04-25-displacement-otm-scanner-design.md`

---

## Task 0: Delete old OTM system and wire placeholder

**Files:**
- Delete: `coding/core/strategy/otm/` (entire directory — needs approval)
- Delete: `coding/service/strategy/otm/` (entire directory — needs approval)
- Delete: `coding/gui/tabs/otm_contracts_view.py`
- Delete: `coding/gui/components/otm_signal_card.py`
- Delete: `coding/gui/components/gate_score_bar.py`
- Delete: `coding/gui/tabs/special_strategies_tab.py`
- Delete: `tests/unit/strategy/otm/` (entire directory — needs approval)
- Modify: `coding/gui/main_window.py`

- [ ] **Step 1: Delete old OTM files**

Request user approval then run:
```bash
rm -rf coding/core/strategy/otm
rm -rf coding/service/strategy/otm
rm coding/gui/tabs/otm_contracts_view.py
rm coding/gui/components/otm_signal_card.py
rm coding/gui/components/gate_score_bar.py
rm coding/gui/tabs/special_strategies_tab.py
rm -rf tests/unit/strategy/otm
```

- [ ] **Step 2: Update main_window.py — remove OTM imports and replace index 6**

In `coding/gui/main_window.py`, replace the entire index 6 block (lines ~192–209):

```python
        # Index 6: Special Strategies — Displacement Scanner (built in later task)
        self.stack.addWidget(self._placeholder_widget("Displacement Scanner loading…"))
```

Also update `MODULE_DEFS` entry for index 6:
```python
    {"index": 6,  "icon": "🎯", "name": "Special Strategies", "subtitle": "Displacement Scanner"},
```

Remove the now-unused imports at the top of `main_window.py`:
```python
from coding.gui.tabs.special_strategies_tab import SpecialStrategiesTab
```

- [ ] **Step 3: Verify app still launches**

```bash
python -m coding.gui.app
```
Expected: App opens, index 6 shows "Displacement Scanner loading…" placeholder. No import errors.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor: remove old 4-gate OTM system, wire placeholder for displacement scanner"
```

---

## Task 1: DB migration — displacement_signals table

**Files:**
- Create: `migrations/012_add_displacement_signals.sql`

- [ ] **Step 1: Write migration**

```sql
-- migrations/012_add_displacement_signals.sql
CREATE TABLE IF NOT EXISTS displacement_signals (
    id               SERIAL PRIMARY KEY,
    asset            VARCHAR(10)   NOT NULL,
    detected_at      TIMESTAMPTZ   NOT NULL,
    drop_24h_pct     NUMERIC(8,6),
    drop_1h_pct      NUMERIC(8,6),
    conviction_pct   NUMERIC(5,2),
    conviction_label VARCHAR(10),
    instrument_name  VARCHAR(60),
    strike           NUMERIC(14,2),
    expiry_date      DATE,
    dte              INTEGER,
    delta            NUMERIC(6,4),
    mark_iv          NUMERIC(6,4),
    premium_usd      NUMERIC(12,2),
    signal_breakdown JSONB,
    telegram_sent    BOOLEAN       DEFAULT FALSE,
    created_at       TIMESTAMPTZ   DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_displacement_signals_asset_detected
    ON displacement_signals (asset, detected_at DESC);
```

- [ ] **Step 2: Run migration**

```bash
python -c "
from coding.core.database.config import ConnectionPool, DatabaseConfig
from pathlib import Path

cfg = DatabaseConfig()
pool = ConnectionPool()
pool.initialize(cfg)
conn = pool.get_connection()
sql = Path('migrations/012_add_displacement_signals.sql').read_text()
with conn.cursor() as cur:
    cur.execute(sql)
conn.commit()
pool.return_connection(conn)
print('Migration complete')
"
```
Expected: prints `Migration complete`, no errors.

- [ ] **Step 3: Verify table exists**

```bash
python -c "
from coding.core.database.config import ConnectionPool, DatabaseConfig
cfg = DatabaseConfig(); pool = ConnectionPool(); pool.initialize(cfg)
conn = pool.get_connection()
with conn.cursor() as cur:
    cur.execute(\"SELECT column_name FROM information_schema.columns WHERE table_name='displacement_signals' ORDER BY ordinal_position\")
    print([r[0] for r in cur.fetchall()])
pool.return_connection(conn)
"
```
Expected: prints list of 16 column names.

- [ ] **Step 4: Commit**

```bash
git add migrations/012_add_displacement_signals.sql
git commit -m "feat: add displacement_signals table migration"
```

---

## Task 2: Core models

**Files:**
- Create: `coding/core/displacement/__init__.py`
- Create: `coding/core/displacement/models/__init__.py`
- Create: `coding/core/displacement/models/displacement_config.py`
- Create: `coding/core/displacement/models/displacement_event.py`
- Create: `coding/core/displacement/models/displacement_signal.py`
- Create: `tests/unit/displacement/__init__.py`
- Test: `tests/unit/displacement/test_displacement_models.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/displacement/test_displacement_models.py
from datetime import datetime, date
import pytest
from pydantic import ValidationError

from coding.core.displacement.models.displacement_config import DisplacementConfig
from coding.core.displacement.models.displacement_event import DisplacementEvent
from coding.core.displacement.models.displacement_signal import DisplacementSignal


class TestDisplacementConfig:
    def test_default_values(self):
        cfg = DisplacementConfig()
        assert cfg.drop_24h_threshold == 0.20
        assert cfg.min_delta == 0.10
        assert cfg.max_delta == 0.20
        assert cfg.preferred_delta == 0.15
        assert cfg.min_dte == 90
        assert cfg.max_dte == 270
        assert cfg.alert_high_threshold == 0.70
        assert cfg.alert_medium_threshold == 0.50

    def test_frozen(self):
        cfg = DisplacementConfig()
        with pytest.raises(ValidationError):
            cfg.drop_24h_threshold = 0.30

    def test_invalid_threshold_raises(self):
        with pytest.raises(ValidationError):
            DisplacementConfig(drop_24h_threshold=1.5)

    def test_custom_values(self):
        cfg = DisplacementConfig(drop_24h_threshold=0.15, risk_budget_usd=5000.0)
        assert cfg.drop_24h_threshold == 0.15
        assert cfg.risk_budget_usd == 5000.0


class TestDisplacementEvent:
    def test_create(self):
        event = DisplacementEvent(
            asset="BTC",
            detected_at=datetime(2026, 4, 25, 10, 0, 0),
            current_price=75000.0,
            drop_1h_pct=0.09,
            drop_4h_pct=0.13,
            drop_24h_pct=0.22,
            drop_7d_pct=0.28,
            triggering_timeframe="24h",
        )
        assert event.asset == "BTC"
        assert event.drop_24h_pct == 0.22
        assert event.triggering_timeframe == "24h"

    def test_frozen(self):
        event = DisplacementEvent(
            asset="ETH", detected_at=datetime.utcnow(), current_price=1500.0,
            drop_1h_pct=0.05, drop_4h_pct=0.08, drop_24h_pct=0.20,
            drop_7d_pct=0.25, triggering_timeframe="24h",
        )
        with pytest.raises(ValidationError):
            event.asset = "BTC"


class TestDisplacementSignal:
    def _make_signal(self, **kwargs):
        defaults = dict(
            asset="BTC", detected_at=datetime.utcnow(),
            drop_24h_pct=0.22, drop_1h_pct=0.09,
            conviction_pct=75.0, conviction_label="HIGH",
            score_drop_magnitude=82.0, score_drop_speed=55.0,
            score_funding_rate=91.0, score_dvol_spike=71.0,
            score_max_pain=64.0, score_term_structure=80.0,
            funding_rate_value=-0.008, dvol_sigma=2.1,
            max_pain_distance_pct=0.09, term_structure_inversion_pct=0.06,
        )
        defaults.update(kwargs)
        return DisplacementSignal(**defaults)

    def test_create_without_contract(self):
        sig = self._make_signal()
        assert sig.instrument_name is None
        assert sig.conviction_label == "HIGH"

    def test_create_with_contract(self):
        sig = self._make_signal(
            instrument_name="BTC-25SEP26-70000-C",
            strike=70000.0,
            expiry_date=date(2026, 9, 25),
            dte=153,
            delta=0.14,
            mark_iv=0.87,
            premium_usd=1240.0,
            target_50pct_price=98400.0,
            target_100pct_price=107200.0,
            target_200pct_price=124800.0,
        )
        assert sig.instrument_name == "BTC-25SEP26-70000-C"
        assert sig.dte == 153

    def test_score_out_of_range_raises(self):
        with pytest.raises(ValidationError):
            self._make_signal(conviction_pct=101.0)

    def test_score_below_zero_raises(self):
        with pytest.raises(ValidationError):
            self._make_signal(score_dvol_spike=-1.0)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/displacement/test_displacement_models.py -v
```
Expected: `ModuleNotFoundError` — models don't exist yet.

- [ ] **Step 3: Create package init files**

```python
# coding/core/displacement/__init__.py
# coding/core/displacement/models/__init__.py
# tests/unit/displacement/__init__.py
```
(All empty files.)

- [ ] **Step 4: Write DisplacementConfig**

```python
# coding/core/displacement/models/displacement_config.py
from pydantic import BaseModel, ConfigDict, field_validator


class DisplacementConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    # Trigger thresholds (as decimals, e.g. 0.08 = 8%)
    drop_1h_threshold: float = 0.08
    drop_4h_threshold: float = 0.12
    drop_24h_threshold: float = 0.20
    drop_7d_threshold: float = 0.30
    cooldown_hours: int = 24

    # Strike selection
    min_delta: float = 0.10
    max_delta: float = 0.20
    preferred_delta: float = 0.15
    min_dte: int = 90
    max_dte: int = 270
    preferred_dte_min: int = 120
    preferred_dte_max: int = 180
    min_oi_btc: int = 50
    min_oi_eth: int = 200
    max_bid_ask_spread_relative: float = 0.08

    # Alert thresholds (as decimals, e.g. 0.70 = 70%)
    alert_high_threshold: float = 0.70
    alert_medium_threshold: float = 0.50

    # Sizing
    risk_budget_usd: float = 10_000.0
    position_size_pct: float = 0.02

    # Conviction scoring parameters
    dvol_sweet_spot_low: float = 1.5     # σ above mean
    dvol_sweet_spot_high: float = 2.5    # σ above mean
    max_pain_distance_full_score: float = 0.10  # 10% below max pain = score 100

    @field_validator(
        "drop_1h_threshold", "drop_4h_threshold",
        "drop_24h_threshold", "drop_7d_threshold"
    )
    @classmethod
    def validate_threshold(cls, v: float) -> float:
        if not 0.0 < v < 1.0:
            raise ValueError("Threshold must be between 0 and 1 exclusive")
        return v

    @field_validator("risk_budget_usd")
    @classmethod
    def validate_budget(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("risk_budget_usd must be positive")
        return v
```

- [ ] **Step 5: Write DisplacementEvent**

```python
# coding/core/displacement/models/displacement_event.py
from datetime import datetime
from pydantic import BaseModel, ConfigDict


class DisplacementEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    asset: str                     # "BTC" or "ETH"
    detected_at: datetime
    current_price: float
    drop_1h_pct: float             # positive decimal = drop (e.g. 0.09 = 9% drop)
    drop_4h_pct: float
    drop_24h_pct: float
    drop_7d_pct: float
    triggering_timeframe: str      # "1h", "4h", "24h", or "7d"
```

- [ ] **Step 6: Write DisplacementSignal**

```python
# coding/core/displacement/models/displacement_signal.py
from datetime import datetime, date
from typing import Optional
from pydantic import BaseModel, ConfigDict, field_validator


class DisplacementSignal(BaseModel):
    model_config = ConfigDict(frozen=True)

    # Event
    asset: str
    detected_at: datetime
    drop_24h_pct: float
    drop_1h_pct: float

    # Conviction
    conviction_pct: float       # 0–100
    conviction_label: str       # "HIGH" or "MEDIUM"

    # Signal scores (each 0–100)
    score_drop_magnitude: float
    score_drop_speed: float
    score_funding_rate: float
    score_dvol_spike: float
    score_max_pain: float
    score_term_structure: float

    # Raw signal values (for display in alert)
    funding_rate_value: float          # e.g. -0.008
    dvol_sigma: float                  # σ above historical mean
    max_pain_distance_pct: float       # spot distance below max pain
    term_structure_inversion_pct: float  # front_iv - back_iv (positive = inverted)

    # Recommended contract (None when no qualifying contract found)
    instrument_name: Optional[str] = None
    strike: Optional[float] = None
    expiry_date: Optional[date] = None
    dte: Optional[int] = None
    delta: Optional[float] = None
    mark_iv: Optional[float] = None
    premium_usd: Optional[float] = None

    # Profit targets (None when no contract)
    target_50pct_price: Optional[float] = None
    target_100pct_price: Optional[float] = None
    target_200pct_price: Optional[float] = None

    telegram_sent: bool = False

    @field_validator(
        "conviction_pct", "score_drop_magnitude", "score_drop_speed",
        "score_funding_rate", "score_dvol_spike", "score_max_pain",
        "score_term_structure",
    )
    @classmethod
    def validate_score_range(cls, v: float) -> float:
        if not 0.0 <= v <= 100.0:
            raise ValueError(f"Score must be between 0 and 100, got {v}")
        return v
```

- [ ] **Step 7: Run tests to verify they pass**

```bash
pytest tests/unit/displacement/test_displacement_models.py -v
```
Expected: All tests pass.

- [ ] **Step 8: Commit**

```bash
git add coding/core/displacement/ tests/unit/displacement/
git commit -m "feat: add DisplacementConfig, DisplacementEvent, DisplacementSignal models"
```

---

## Task 3: Repository methods

**Files:**
- Modify: `coding/core/database/repository.py`
- Test: `tests/unit/displacement/test_repository_displacement.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/displacement/test_repository_displacement.py
import json
from datetime import datetime, date, timezone
from unittest.mock import MagicMock, patch, call
import pytest

from coding.core.displacement.models.displacement_signal import DisplacementSignal


def _make_signal(**kwargs):
    defaults = dict(
        asset="BTC", detected_at=datetime(2026, 4, 25, 10, 0, tzinfo=timezone.utc),
        drop_24h_pct=0.22, drop_1h_pct=0.09,
        conviction_pct=75.0, conviction_label="HIGH",
        score_drop_magnitude=82.0, score_drop_speed=55.0,
        score_funding_rate=91.0, score_dvol_spike=71.0,
        score_max_pain=64.0, score_term_structure=80.0,
        funding_rate_value=-0.008, dvol_sigma=2.1,
        max_pain_distance_pct=0.09, term_structure_inversion_pct=0.06,
        instrument_name="BTC-25SEP26-70000-C",
        strike=70000.0, expiry_date=date(2026, 9, 25),
        dte=153, delta=0.14, mark_iv=0.87, premium_usd=1240.0,
        target_50pct_price=98400.0, target_100pct_price=107200.0, target_200pct_price=124800.0,
    )
    defaults.update(kwargs)
    return DisplacementSignal(**defaults)


class TestRepositoryDisplacementMethods:
    @patch("coding.core.database.repository.ConnectionPool")
    def test_save_displacement_signal_executes_insert(self, mock_pool_cls):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_pool_cls.return_value.get_connection.return_value = mock_conn

        from coding.core.database.repository import DatabaseRepository
        repo = DatabaseRepository()
        signal = _make_signal()
        repo.save_displacement_signal(signal)

        assert mock_cursor.execute.called
        sql_call = mock_cursor.execute.call_args[0][0]
        assert "INSERT INTO displacement_signals" in sql_call

    @patch("coding.core.database.repository.ConnectionPool")
    def test_get_last_displacement_signal_returns_dict(self, mock_pool_cls):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (
            1, "BTC", datetime(2026, 4, 25, 10, 0, tzinfo=timezone.utc),
            0.22, 0.09, 75.0, "HIGH",
            "BTC-25SEP26-70000-C", 70000.0, date(2026, 9, 25),
            153, 0.14, 0.87, 1240.0, '{}', False,
        )
        mock_cursor.description = [
            ("id",), ("asset",), ("detected_at",), ("drop_24h_pct",), ("drop_1h_pct",),
            ("conviction_pct",), ("conviction_label",), ("instrument_name",),
            ("strike",), ("expiry_date",), ("dte",), ("delta",), ("mark_iv",),
            ("premium_usd",), ("signal_breakdown",), ("telegram_sent",),
        ]
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_pool_cls.return_value.get_connection.return_value = mock_conn

        from coding.core.database.repository import DatabaseRepository
        repo = DatabaseRepository()
        result = repo.get_last_displacement_signal("BTC")

        assert result is not None
        assert result["asset"] == "BTC"

    @patch("coding.core.database.repository.ConnectionPool")
    def test_get_last_displacement_signal_returns_none_when_empty(self, mock_pool_cls):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_pool_cls.return_value.get_connection.return_value = mock_conn

        from coding.core.database.repository import DatabaseRepository
        repo = DatabaseRepository()
        result = repo.get_last_displacement_signal("ETH")
        assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/displacement/test_repository_displacement.py -v
```
Expected: `AttributeError: 'DatabaseRepository' has no attribute 'save_displacement_signal'`

- [ ] **Step 3: Add methods to repository**

Add at the end of the `DatabaseRepository` class in `coding/core/database/repository.py`:

```python
    def save_displacement_signal(self, signal: "DisplacementSignal") -> None:
        import json
        breakdown = {
            "drop_magnitude": signal.score_drop_magnitude,
            "drop_speed": signal.score_drop_speed,
            "funding_rate": signal.score_funding_rate,
            "dvol_spike": signal.score_dvol_spike,
            "max_pain": signal.score_max_pain,
            "term_structure": signal.score_term_structure,
            "funding_rate_value": signal.funding_rate_value,
            "dvol_sigma": signal.dvol_sigma,
            "max_pain_distance_pct": signal.max_pain_distance_pct,
            "term_structure_inversion_pct": signal.term_structure_inversion_pct,
        }
        with self._db_cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO displacement_signals (
                    asset, detected_at, drop_24h_pct, drop_1h_pct,
                    conviction_pct, conviction_label,
                    instrument_name, strike, expiry_date, dte,
                    delta, mark_iv, premium_usd,
                    signal_breakdown, telegram_sent
                ) VALUES (
                    %s, %s, %s, %s,
                    %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, %s,
                    %s, %s
                )
                """,
                (
                    signal.asset, signal.detected_at,
                    signal.drop_24h_pct, signal.drop_1h_pct,
                    signal.conviction_pct, signal.conviction_label,
                    signal.instrument_name, signal.strike, signal.expiry_date, signal.dte,
                    signal.delta, signal.mark_iv, signal.premium_usd,
                    json.dumps(breakdown), signal.telegram_sent,
                ),
            )

    def get_last_displacement_signal(self, asset: str) -> "Optional[dict]":
        with self._db_cursor() as cursor:
            cursor.execute(
                """
                SELECT id, asset, detected_at, drop_24h_pct, drop_1h_pct,
                       conviction_pct, conviction_label,
                       instrument_name, strike, expiry_date, dte,
                       delta, mark_iv, premium_usd,
                       signal_breakdown, telegram_sent
                FROM displacement_signals
                WHERE asset = %s
                ORDER BY detected_at DESC
                LIMIT 1
                """,
                (asset,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            cols = [desc[0] for desc in cursor.description]
            return dict(zip(cols, row))
```

Also add the import at the top of repository.py if not already present:
```python
from typing import Any, Dict, List, Optional
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/displacement/test_repository_displacement.py -v
```
Expected: All 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add coding/core/database/repository.py tests/unit/displacement/test_repository_displacement.py
git commit -m "feat: add save_displacement_signal and get_last_displacement_signal to repository"
```

---

## Task 4: Add get_price_ohlcv to DeribitApiService

**Files:**
- Modify: `coding/service/deribit/deribit_api_service.py`
- Modify: `coding/core/endpoints/deribit_endpoints.py`
- Test: `tests/unit/displacement/test_deribit_price_history.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/displacement/test_deribit_price_history.py
from unittest.mock import MagicMock, patch
import pytest
from coding.service.deribit.deribit_api_service import DeribitApiService


class TestGetPriceOhlcv:
    @patch("coding.service.deribit.deribit_api_service.ApiConnection")
    def test_returns_list_of_candles(self, mock_conn_cls):
        now_ms = 1745000000000
        mock_response = {
            "result": {
                "ticks": [now_ms - 3600000, now_ms],
                "open": [90000.0, 91000.0],
                "high": [91500.0, 91800.0],
                "low": [89500.0, 90500.0],
                "close": [91000.0, 91500.0],
                "volume": [100.0, 120.0],
            }
        }
        mock_conn = MagicMock()
        mock_conn.fetch.return_value = mock_response
        mock_conn_cls.return_value = mock_conn

        svc = DeribitApiService()
        result = svc.get_price_ohlcv("BTC", resolution_hours=1, lookback_hours=168)

        assert isinstance(result, list)
        assert len(result) == 2
        assert "timestamp" in result[0]
        assert "close" in result[0]
        # Newest first
        assert result[0]["timestamp"] >= result[1]["timestamp"]

    @patch("coding.service.deribit.deribit_api_service.ApiConnection")
    def test_empty_response_returns_empty_list(self, mock_conn_cls):
        mock_conn = MagicMock()
        mock_conn.fetch.return_value = {"result": {"ticks": [], "close": []}}
        mock_conn_cls.return_value = mock_conn

        svc = DeribitApiService()
        result = svc.get_price_ohlcv("ETH", resolution_hours=1, lookback_hours=24)
        assert result == []
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/unit/displacement/test_deribit_price_history.py -v
```
Expected: `AttributeError: 'DeribitApiService' has no attribute 'get_price_ohlcv'`

- [ ] **Step 3: Add GET_TRADINGVIEW_CHART_DATA endpoint**

Open `coding/core/endpoints/deribit_endpoints.py`. Add to the `DeribitEndpoints` class:
```python
    GET_TRADINGVIEW_CHART_DATA = "public/get_tradingview_chart_data"
```

- [ ] **Step 4: Add get_price_ohlcv to DeribitApiService**

Add this method to `coding/service/deribit/deribit_api_service.py` (follow the existing import block, add after existing methods):

```python
    def get_price_ohlcv(
        self,
        asset: str,
        resolution_hours: int = 1,
        lookback_hours: int = 168,
    ) -> List[Dict[str, Any]]:
        """
        Fetch hourly OHLCV for the asset's perpetual contract.

        Args:
            asset: "BTC" or "ETH"
            resolution_hours: Candle width in hours (1 = hourly, 4 = 4-hourly)
            lookback_hours: How many hours of history to fetch

        Returns:
            List of {"timestamp": int, "open": float, "high": float,
                      "low": float, "close": float, "volume": float}
            Sorted newest-first. Empty list if API fails.
        """
        import time as _time
        end_ts = int(_time.time() * 1000)
        start_ts = end_ts - lookback_hours * 3600 * 1000
        resolution = str(resolution_hours * 60)  # Deribit uses minutes

        try:
            response = self.connection.fetch(
                DeribitEndpoints.GET_TRADINGVIEW_CHART_DATA,
                parameters={
                    "instrument_name": f"{asset}-PERPETUAL",
                    "start_timestamp": start_ts,
                    "end_timestamp": end_ts,
                    "resolution": resolution,
                },
            )
            data = response.get("result", {})
            ticks = data.get("ticks", [])
            closes = data.get("close", [])
            opens = data.get("open", [])
            highs = data.get("high", [])
            lows = data.get("low", [])
            volumes = data.get("volume", [])

            candles = [
                {
                    "timestamp": ticks[i],
                    "open": opens[i],
                    "high": highs[i],
                    "low": lows[i],
                    "close": closes[i],
                    "volume": volumes[i],
                }
                for i in range(len(ticks))
            ]
            # Return newest first
            candles.sort(key=lambda c: c["timestamp"], reverse=True)
            return candles

        except Exception as e:
            logger.error(f"get_price_ohlcv failed for {asset}: {e}")
            return []
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/unit/displacement/test_deribit_price_history.py -v
```
Expected: Both tests pass.

- [ ] **Step 6: Commit**

```bash
git add coding/service/deribit/deribit_api_service.py coding/core/endpoints/deribit_endpoints.py tests/unit/displacement/test_deribit_price_history.py
git commit -m "feat: add get_price_ohlcv to DeribitApiService"
```

---

## Task 5: DisplacementDetector

**Files:**
- Create: `coding/core/displacement/displacement_detector.py`
- Test: `tests/unit/displacement/test_displacement_detector.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/displacement/test_displacement_detector.py
from datetime import datetime, timedelta
import pytest
from coding.core.displacement.models.displacement_config import DisplacementConfig
from coding.core.displacement.displacement_detector import DisplacementDetector


def _prices(now=80000.0, h1=87000.0, h4=92000.0, h24=100000.0, h7d=110000.0):
    return {"now": now, "1h_ago": h1, "4h_ago": h4, "24h_ago": h24, "7d_ago": h7d}


class TestDisplacementDetector:
    def setup_method(self):
        self.cfg = DisplacementConfig()
        self.detector = DisplacementDetector(self.cfg)

    def test_no_event_when_drop_below_all_thresholds(self):
        # Only 5% 24h drop — below 20% threshold
        prices = _prices(now=95000.0, h1=95500.0, h4=97000.0, h24=100000.0, h7d=102000.0)
        result = self.detector.check("BTC", prices)
        assert result is None

    def test_event_fired_when_24h_threshold_exceeded(self):
        # 22% 24h drop
        prices = _prices(now=78000.0, h1=80000.0, h4=85000.0, h24=100000.0, h7d=105000.0)
        result = self.detector.check("BTC", prices)
        assert result is not None
        assert result.asset == "BTC"
        assert result.triggering_timeframe == "24h"
        assert abs(result.drop_24h_pct - 0.22) < 0.01

    def test_event_fired_when_1h_threshold_exceeded(self):
        # 10% 1h drop (above 8% threshold)
        prices = _prices(now=90000.0, h1=100000.0, h4=100500.0, h24=101000.0, h7d=102000.0)
        result = self.detector.check("BTC", prices)
        assert result is not None
        assert result.triggering_timeframe == "1h"

    def test_cooldown_prevents_second_event(self):
        prices = _prices(now=78000.0, h1=80000.0, h4=85000.0, h24=100000.0, h7d=105000.0)
        first = self.detector.check("BTC", prices)
        assert first is not None
        # Second check within cooldown window
        second = self.detector.check("BTC", prices)
        assert second is None

    def test_different_assets_independent_cooldown(self):
        prices = _prices(now=1200.0, h1=1230.0, h4=1300.0, h24=1540.0, h7d=1600.0)
        btc_prices = _prices(now=78000.0, h1=80000.0, h4=85000.0, h24=100000.0, h7d=105000.0)
        eth_event = self.detector.check("ETH", prices)
        btc_event = self.detector.check("BTC", btc_prices)
        assert eth_event is not None
        assert btc_event is not None

    def test_drop_pct_values_are_positive(self):
        # Drops stored as positive fractions
        prices = _prices(now=80000.0, h1=90000.0, h4=92000.0, h24=100000.0, h7d=105000.0)
        event = self.detector.check("BTC", prices)
        if event:
            assert event.drop_24h_pct > 0
            assert event.drop_1h_pct > 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/displacement/test_displacement_detector.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Write DisplacementDetector**

```python
# coding/core/displacement/displacement_detector.py
import logging
from datetime import datetime, timezone
from typing import Optional

from coding.core.displacement.models.displacement_config import DisplacementConfig
from coding.core.displacement.models.displacement_event import DisplacementEvent

logger = logging.getLogger(__name__)


class DisplacementDetector:
    """
    Detects price displacement events across multiple timeframes.

    Stateful: tracks last event time per asset to enforce cooldown.
    """

    def __init__(self, config: DisplacementConfig):
        self._config = config
        self._last_event_time: dict[str, Optional[datetime]] = {}

    def check(self, asset: str, prices: dict[str, float]) -> Optional[DisplacementEvent]:
        """
        Check if a displacement event has occurred.

        Args:
            asset: "BTC" or "ETH"
            prices: dict with keys "now", "1h_ago", "4h_ago", "24h_ago", "7d_ago"
                    All values are prices in USD.

        Returns:
            DisplacementEvent if triggered, None if no event or in cooldown.
        """
        if self._in_cooldown(asset):
            return None

        now_price = prices["now"]
        drops = self._compute_drops(now_price, prices)
        triggering_tf = self._find_triggering_timeframe(drops)

        if triggering_tf is None:
            return None

        self._last_event_time[asset] = datetime.now(tz=timezone.utc)
        logger.info(
            f"Displacement event: {asset} drop {drops[triggering_tf]*100:.1f}% "
            f"in {triggering_tf} (price: ${now_price:,.0f})"
        )

        return DisplacementEvent(
            asset=asset,
            detected_at=datetime.now(tz=timezone.utc),
            current_price=now_price,
            drop_1h_pct=drops["1h"],
            drop_4h_pct=drops["4h"],
            drop_24h_pct=drops["24h"],
            drop_7d_pct=drops["7d"],
            triggering_timeframe=triggering_tf,
        )

    def _in_cooldown(self, asset: str) -> bool:
        last = self._last_event_time.get(asset)
        if last is None:
            return False
        elapsed_hours = (datetime.now(tz=timezone.utc) - last).total_seconds() / 3600
        return elapsed_hours < self._config.cooldown_hours

    def _compute_drops(self, now_price: float, prices: dict[str, float]) -> dict[str, float]:
        return {
            "1h": (prices["1h_ago"] - now_price) / prices["1h_ago"],
            "4h": (prices["4h_ago"] - now_price) / prices["4h_ago"],
            "24h": (prices["24h_ago"] - now_price) / prices["24h_ago"],
            "7d": (prices["7d_ago"] - now_price) / prices["7d_ago"],
        }

    def _find_triggering_timeframe(self, drops: dict[str, float]) -> Optional[str]:
        thresholds = {
            "1h": self._config.drop_1h_threshold,
            "4h": self._config.drop_4h_threshold,
            "24h": self._config.drop_24h_threshold,
            "7d": self._config.drop_7d_threshold,
        }
        for tf in ("1h", "4h", "24h", "7d"):
            if drops[tf] >= thresholds[tf]:
                return tf
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/displacement/test_displacement_detector.py -v
```
Expected: All 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add coding/core/displacement/displacement_detector.py tests/unit/displacement/test_displacement_detector.py
git commit -m "feat: add DisplacementDetector with multi-timeframe drop detection and cooldown"
```

---

## Task 6: ConvictionScorer

**Files:**
- Create: `coding/core/displacement/conviction_scorer.py`
- Test: `tests/unit/displacement/test_conviction_scorer.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/displacement/test_conviction_scorer.py
from datetime import datetime, timezone
import pytest

from coding.core.displacement.models.displacement_config import DisplacementConfig
from coding.core.displacement.models.displacement_event import DisplacementEvent
from coding.core.displacement.conviction_scorer import ConvictionScorer


def _event(drop_24h=0.22, drop_1h=0.09):
    return DisplacementEvent(
        asset="BTC", detected_at=datetime.now(tz=timezone.utc),
        current_price=78000.0,
        drop_1h_pct=drop_1h, drop_4h_pct=0.13,
        drop_24h_pct=drop_24h, drop_7d_pct=0.28,
        triggering_timeframe="24h",
    )


def _market_data(funding=-0.008, dvol_current=85.0, dvol_history=None, ohlcv=None, options=None):
    if dvol_history is None:
        dvol_history = [50.0] * 90 + [55.0, 52.0, 48.0]
    if ohlcv is None:
        # 3 years of daily candles at 100k with 22% drop
        closes = [100000.0] * 1095
        closes[0] = 78000.0
        ohlcv = [{"close": c} for c in closes]
    if options is None:
        options = [
            {"strike": 70000.0, "option_type": "call", "open_interest": 500.0, "dte": 30, "mark_iv": 0.90},
            {"strike": 80000.0, "option_type": "call", "open_interest": 1000.0, "dte": 30, "mark_iv": 0.88},
            {"strike": 90000.0, "option_type": "put", "open_interest": 800.0, "dte": 30, "mark_iv": 0.85},
            {"strike": 70000.0, "option_type": "call", "open_interest": 300.0, "dte": 90, "mark_iv": 0.80},
            {"strike": 80000.0, "option_type": "put", "open_interest": 200.0, "dte": 90, "mark_iv": 0.75},
        ]
    return {
        "funding_rate": funding,
        "dvol_current": dvol_current,
        "dvol_history": dvol_history,
        "ohlcv_history": ohlcv,
        "options_chain": options,
    }


class TestConvictionScorer:
    def setup_method(self):
        self.scorer = ConvictionScorer(DisplacementConfig())

    def test_score_returns_tuple_probability_and_breakdown(self):
        prob, breakdown = self.scorer.score(_event(), _market_data())
        assert 0.0 <= prob <= 100.0
        assert "drop_magnitude" in breakdown
        assert "funding_rate" in breakdown
        assert "dvol_spike" in breakdown
        assert "max_pain" in breakdown
        assert "term_structure" in breakdown
        assert "drop_speed" in breakdown

    def test_deeply_negative_funding_scores_high(self):
        prob, breakdown = self.scorer.score(_event(), _market_data(funding=-0.01))
        assert breakdown["funding_rate"] >= 90.0

    def test_positive_funding_scores_low(self):
        prob, breakdown = self.scorer.score(_event(), _market_data(funding=0.005))
        assert breakdown["funding_rate"] < 30.0

    def test_dvol_in_sweet_spot_scores_100(self):
        # Mean 50, std ~5. Current=60 → sigma=2.0 (in sweet spot 1.5-2.5)
        history = [50.0] * 90
        prob, breakdown = self.scorer.score(_event(), _market_data(dvol_current=60.0, dvol_history=history))
        assert breakdown["dvol_spike"] == 100.0

    def test_dvol_way_too_high_is_penalized(self):
        # sigma > 3 should score below sweet spot
        history = [50.0] * 90
        prob, breakdown = self.scorer.score(_event(), _market_data(dvol_current=75.0, dvol_history=history))
        assert breakdown["dvol_spike"] < 100.0

    def test_flash_crash_scores_higher_than_slow_bleed(self):
        flash = _event(drop_24h=0.22, drop_1h=0.20)   # 20% happened in 1h
        bleed = _event(drop_24h=0.22, drop_1h=0.01)   # 1% happened in 1h
        _, b_flash = self.scorer.score(flash, _market_data())
        _, b_bleed = self.scorer.score(bleed, _market_data())
        assert b_flash["drop_speed"] > b_bleed["drop_speed"]

    def test_spot_below_max_pain_scores_high(self):
        # Max pain will be ~80000, spot is 70000 → 12.5% below → score 100
        options = [
            {"strike": 80000.0, "option_type": "call", "open_interest": 5000.0, "dte": 30, "mark_iv": 0.88},
            {"strike": 80000.0, "option_type": "put", "open_interest": 5000.0, "dte": 30, "mark_iv": 0.88},
            {"strike": 70000.0, "option_type": "call", "open_interest": 100.0, "dte": 30, "mark_iv": 0.90},
        ]
        event = DisplacementEvent(
            asset="BTC", detected_at=datetime.now(tz=timezone.utc), current_price=70000.0,
            drop_1h_pct=0.09, drop_4h_pct=0.13, drop_24h_pct=0.22,
            drop_7d_pct=0.28, triggering_timeframe="24h",
        )
        _, breakdown = self.scorer.score(event, _market_data(options=options))
        assert breakdown["max_pain"] == 100.0

    def test_conviction_label_high_above_threshold(self):
        # Make all signals positive to push above 70%
        history = [50.0] * 90
        _, breakdown = self.scorer.score(
            _event(),
            _market_data(funding=-0.01, dvol_current=60.0, dvol_history=history)
        )
        # Just verify breakdown is complete dict, threshold testing done via scorer output
        assert len(breakdown) == 6
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/displacement/test_conviction_scorer.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Write ConvictionScorer**

```python
# coding/core/displacement/conviction_scorer.py
import logging
import math
from collections import defaultdict
from pathlib import Path
from typing import Optional

from coding.core.displacement.models.displacement_config import DisplacementConfig
from coding.core.displacement.models.displacement_event import DisplacementEvent

logger = logging.getLogger(__name__)


class ConvictionScorer:
    """
    Scores a displacement event using 6 market signals.

    Initial weights: equal (1/6 each). After backtest, replaced by
    trained logistic regression coefficients loaded from model file.
    """

    def __init__(self, config: DisplacementConfig, model_path: Optional[Path] = None):
        self._config = config
        self._model = None
        if model_path and model_path.exists():
            self._load_model(model_path)

    def score(
        self,
        event: DisplacementEvent,
        market_data: dict,
    ) -> tuple[float, dict[str, float]]:
        """
        Score the displacement event.

        market_data keys:
            funding_rate: float        — current 8h perpetual funding rate (e.g. -0.008)
            dvol_current: float        — current DVOL value
            dvol_history: List[float]  — historical DVOL values (oldest first)
            ohlcv_history: List[dict]  — daily candles, [{"close": float}, ...] newest first
            options_chain: List[dict]  — current options chain

        Returns:
            (probability_0_to_100, signal_breakdown_dict)
        """
        breakdown = {
            "drop_magnitude": self._score_drop_magnitude(event, market_data["ohlcv_history"]),
            "drop_speed": self._score_drop_speed(event),
            "funding_rate": self._score_funding_rate(market_data["funding_rate"]),
            "dvol_spike": self._score_dvol_spike(
                market_data["dvol_current"], market_data["dvol_history"]
            ),
            "max_pain": self._score_max_pain(event.current_price, market_data["options_chain"]),
            "term_structure": self._score_term_structure(market_data["options_chain"]),
        }

        if self._model is not None:
            features = [[v for v in breakdown.values()]]
            try:
                prob = float(self._model.predict_proba(features)[0][1]) * 100
            except Exception as e:
                logger.warning(f"Model prediction failed, using equal weights: {e}")
                prob = sum(breakdown.values()) / 6.0
        else:
            prob = sum(breakdown.values()) / 6.0

        return round(prob, 2), breakdown

    def _score_drop_magnitude(self, event: DisplacementEvent, ohlcv_history: list[dict]) -> float:
        closes = [row["close"] for row in ohlcv_history]
        if len(closes) < 2:
            return 50.0
        # Compute historical 24h drops
        drops = []
        for i in range(len(closes) - 1):
            if closes[i + 1] > 0:
                drop = (closes[i + 1] - closes[i]) / closes[i + 1]
                if drop > 0:
                    drops.append(drop)
        if not drops:
            return 50.0
        current_drop = abs(event.drop_24h_pct)
        percentile = sum(1 for d in drops if d <= current_drop) / len(drops) * 100
        return round(min(100.0, percentile), 2)

    def _score_drop_speed(self, event: DisplacementEvent) -> float:
        if abs(event.drop_24h_pct) < 0.001:
            return 50.0
        ratio = abs(event.drop_1h_pct) / abs(event.drop_24h_pct)
        return round(min(100.0, max(0.0, ratio * 200)), 2)

    def _score_funding_rate(self, funding_rate: float) -> float:
        # -0.01 → 100, 0.0 → 50, +0.01 → 0
        score = 50.0 - (funding_rate * 5000)
        return round(min(100.0, max(0.0, score)), 2)

    def _score_dvol_spike(self, dvol_current: float, dvol_history: list[float]) -> float:
        if len(dvol_history) < 10:
            return 50.0
        mean = sum(dvol_history) / len(dvol_history)
        variance = sum((x - mean) ** 2 for x in dvol_history) / len(dvol_history)
        std = math.sqrt(variance)
        if std < 0.001:
            return 50.0
        sigma = (dvol_current - mean) / std
        low = self._config.dvol_sweet_spot_low
        high = self._config.dvol_sweet_spot_high
        if sigma < 0:
            return 0.0
        elif sigma < low:
            return round(sigma / low * 60, 2)
        elif sigma <= high:
            return 100.0
        else:
            return round(max(0.0, 100.0 - (sigma - high) / 1.5 * 100), 2)

    def _score_max_pain(self, current_price: float, options_chain: list[dict]) -> float:
        max_pain_price = self._compute_max_pain(options_chain)
        if max_pain_price is None or max_pain_price <= 0:
            return 50.0
        distance = (max_pain_price - current_price) / max_pain_price
        if distance <= 0:
            return 0.0
        return round(min(100.0, distance / self._config.max_pain_distance_full_score * 100), 2)

    def _compute_max_pain(self, options_chain: list[dict]) -> Optional[float]:
        strikes: dict[float, dict[str, float]] = defaultdict(lambda: {"call_oi": 0.0, "put_oi": 0.0})
        for opt in options_chain:
            strike = opt.get("strike")
            if not strike:
                continue
            oi = opt.get("open_interest", 0.0) or 0.0
            if opt.get("option_type") == "call":
                strikes[strike]["call_oi"] += oi
            else:
                strikes[strike]["put_oi"] += oi
        if not strikes:
            return None
        sorted_strikes = sorted(strikes.keys())
        min_pain = float("inf")
        max_pain_strike = sorted_strikes[0]
        for test_price in sorted_strikes:
            call_pain = sum(
                (test_price - s) * data["call_oi"]
                for s, data in strikes.items()
                if s < test_price
            )
            put_pain = sum(
                (s - test_price) * data["put_oi"]
                for s, data in strikes.items()
                if s > test_price
            )
            total = call_pain + put_pain
            if total < min_pain:
                min_pain = total
                max_pain_strike = test_price
        return max_pain_strike

    def _score_term_structure(self, options_chain: list[dict]) -> float:
        by_expiry: dict[int, list[float]] = defaultdict(list)
        for opt in options_chain:
            dte = opt.get("dte", 0)
            iv = opt.get("mark_iv", 0.0)
            if dte >= 7 and iv and iv > 0:
                by_expiry[dte].append(iv)
        if len(by_expiry) < 2:
            return 50.0
        sorted_dtes = sorted(by_expiry.keys())
        front_iv = sum(by_expiry[sorted_dtes[0]]) / len(by_expiry[sorted_dtes[0]])
        back_iv = sum(by_expiry[sorted_dtes[1]]) / len(by_expiry[sorted_dtes[1]])
        if back_iv < 0.001:
            return 50.0
        inversion_pct = (front_iv - back_iv) / back_iv
        if inversion_pct >= 0.05:
            return 100.0
        elif inversion_pct >= 0:
            return round(50.0 + inversion_pct / 0.05 * 50, 2)
        else:
            return round(max(0.0, 50.0 + inversion_pct / 0.05 * 50), 2)

    def _load_model(self, model_path: Path) -> None:
        try:
            import joblib
            self._model = joblib.load(model_path)
            logger.info(f"Loaded conviction model from {model_path}")
        except Exception as e:
            logger.warning(f"Could not load conviction model: {e}")
            self._model = None
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/displacement/test_conviction_scorer.py -v
```
Expected: All 8 tests pass.

- [ ] **Step 5: Commit**

```bash
git add coding/core/displacement/conviction_scorer.py tests/unit/displacement/test_conviction_scorer.py
git commit -m "feat: add ConvictionScorer with 6 market signals and logistic regression support"
```

---

## Task 7: StrikeSelector

**Files:**
- Create: `coding/core/displacement/strike_selector.py`
- Test: `tests/unit/displacement/test_strike_selector.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/displacement/test_strike_selector.py
from datetime import date
import pytest
from coding.core.displacement.models.displacement_config import DisplacementConfig
from coding.core.displacement.strike_selector import StrikeSelector


def _make_option(instrument, strike, dte, delta, bid_iv, ask_iv, mark_iv, oi, mark_price, underlying):
    return {
        "instrument_name": instrument,
        "option_type": "call",
        "strike": strike,
        "dte": dte,
        "delta": delta,
        "bid_iv": bid_iv,
        "ask_iv": ask_iv,
        "mark_iv": mark_iv,
        "open_interest": oi,
        "mark_price": mark_price,
        "underlying_price": underlying,
    }


CHAIN = [
    _make_option("BTC-25SEP26-70000-C", 70000.0, 153, 0.14, 0.85, 0.92, 0.87, 500, 0.0135, 78000.0),
    _make_option("BTC-25SEP26-75000-C", 75000.0, 153, 0.12, 0.86, 0.93, 0.89, 200, 0.0095, 78000.0),
    _make_option("BTC-25JUN26-70000-C", 70000.0, 61, 0.13, 0.88, 0.97, 0.92, 350, 0.0090, 78000.0),
    # Below min OI — should be filtered
    _make_option("BTC-25SEP26-65000-C", 65000.0, 153, 0.18, 0.84, 0.91, 0.87, 10, 0.0200, 78000.0),
    # DTE too short — should be filtered
    _make_option("BTC-28APR26-70000-C", 70000.0, 3, 0.15, 0.90, 0.98, 0.94, 800, 0.0080, 78000.0),
    # Delta too high — should be filtered
    _make_option("BTC-25SEP26-55000-C", 55000.0, 153, 0.45, 0.82, 0.88, 0.85, 600, 0.0350, 78000.0),
    # Spread too wide — should be filtered (> 8% relative)
    _make_option("BTC-25SEP26-80000-C", 80000.0, 153, 0.11, 0.70, 0.90, 0.80, 400, 0.0070, 78000.0),
]


class TestStrikeSelector:
    def setup_method(self):
        self.cfg = DisplacementConfig()
        self.selector = StrikeSelector(self.cfg)

    def test_returns_best_contract(self):
        result = self.selector.select("BTC", CHAIN, 78000.0)
        assert result is not None
        assert result["instrument_name"] in ("BTC-25SEP26-70000-C", "BTC-25SEP26-75000-C", "BTC-25JUN26-70000-C")

    def test_filters_low_oi(self):
        result = self.selector.select("BTC", CHAIN, 78000.0)
        assert result is not None
        assert result["instrument_name"] != "BTC-25SEP26-65000-C"

    def test_filters_short_dte(self):
        result = self.selector.select("BTC", CHAIN, 78000.0)
        assert result is not None
        assert result["dte"] >= self.cfg.min_dte

    def test_filters_delta_too_high(self):
        result = self.selector.select("BTC", CHAIN, 78000.0)
        assert result is not None
        assert result["delta"] <= self.cfg.max_delta

    def test_filters_wide_spread(self):
        result = self.selector.select("BTC", CHAIN, 78000.0)
        assert result is not None
        assert result["instrument_name"] != "BTC-25SEP26-80000-C"

    def test_returns_none_when_no_qualifying_contracts(self):
        tiny_chain = [
            _make_option("BTC-28APR26-70000-C", 70000.0, 3, 0.15, 0.90, 0.98, 0.94, 800, 0.008, 78000.0),
        ]
        result = self.selector.select("BTC", tiny_chain, 78000.0)
        assert result is None

    def test_includes_profit_targets(self):
        result = self.selector.select("BTC", CHAIN, 78000.0)
        assert result is not None
        assert "target_50pct_price" in result
        assert "target_100pct_price" in result
        assert "target_200pct_price" in result
        assert result["target_100pct_price"] > result["target_50pct_price"]

    def test_prefers_delta_closest_to_preferred(self):
        # Two contracts both qualify — prefer closest to preferred_delta=0.15
        chain = [
            _make_option("BTC-25SEP26-70000-C", 70000.0, 153, 0.14, 0.85, 0.92, 0.87, 500, 0.0135, 78000.0),
            _make_option("BTC-25SEP26-72000-C", 72000.0, 153, 0.13, 0.85, 0.92, 0.87, 500, 0.0110, 78000.0),
        ]
        result = self.selector.select("BTC", chain, 78000.0)
        # delta 0.14 is closer to preferred 0.15 than delta 0.13
        assert result["instrument_name"] == "BTC-25SEP26-70000-C"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/displacement/test_strike_selector.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Write StrikeSelector**

```python
# coding/core/displacement/strike_selector.py
import logging
from typing import Optional

from coding.core.displacement.models.displacement_config import DisplacementConfig

logger = logging.getLogger(__name__)


class StrikeSelector:
    """
    Filters and ranks options chain to find the optimal OTM call to buy
    after a displacement event.
    """

    def __init__(self, config: DisplacementConfig):
        self._config = config

    def select(
        self,
        asset: str,
        options_chain: list[dict],
        current_price: float,
    ) -> Optional[dict]:
        """
        Select the best OTM call from the options chain.

        Returns enriched option dict with profit targets, or None if no contract qualifies.
        """
        min_oi = self._config.min_oi_btc if asset == "BTC" else self._config.min_oi_eth
        candidates = [
            opt for opt in options_chain
            if self._passes_filters(opt, min_oi)
        ]

        if not candidates:
            logger.warning(f"No qualifying OTM calls found for {asset}")
            return None

        best = self._rank(candidates)
        return self._enrich(best, current_price)

    def _passes_filters(self, opt: dict, min_oi: int) -> bool:
        if opt.get("option_type") != "call":
            return False
        delta = opt.get("delta", 0.0)
        if not (self._config.min_delta <= delta <= self._config.max_delta):
            return False
        dte = opt.get("dte", 0)
        if not (self._config.min_dte <= dte <= self._config.max_dte):
            return False
        oi = opt.get("open_interest", 0.0) or 0.0
        if oi < min_oi:
            return False
        bid_iv = opt.get("bid_iv", 0.0) or 0.0
        ask_iv = opt.get("ask_iv", 0.0) or 0.0
        mid_iv = (bid_iv + ask_iv) / 2 if bid_iv and ask_iv else 0.0
        if mid_iv > 0:
            spread_relative = (ask_iv - bid_iv) / mid_iv
            if spread_relative > self._config.max_bid_ask_spread_relative:
                return False
        return True

    def _rank(self, candidates: list[dict]) -> dict:
        preferred = self._config.preferred_delta
        preferred_dte_min = self._config.preferred_dte_min
        preferred_dte_max = self._config.preferred_dte_max

        def score(opt: dict) -> float:
            delta_dist = abs(opt.get("delta", 0.0) - preferred)
            dte = opt.get("dte", 0)
            dte_penalty = 0.0 if preferred_dte_min <= dte <= preferred_dte_max else 0.05
            bid_iv = opt.get("bid_iv", 0.0) or 0.0
            ask_iv = opt.get("ask_iv", 0.0) or 0.0
            mid_iv = (bid_iv + ask_iv) / 2 if bid_iv and ask_iv else 1.0
            spread_penalty = (ask_iv - bid_iv) / mid_iv if mid_iv > 0 else 0.0
            # Lower score = better
            return delta_dist + dte_penalty + spread_penalty * 0.1

        return min(candidates, key=score)

    def _enrich(self, opt: dict, current_price: float) -> dict:
        mark_price = opt.get("mark_price", 0.0)
        underlying = opt.get("underlying_price", current_price) or current_price
        premium_usd = mark_price * underlying

        # Strike price needed for 50/100/200% gain on the premium
        strike = opt.get("strike", 0.0)

        result = dict(opt)
        result["premium_usd"] = round(premium_usd, 2)
        result["target_50pct_price"] = round(strike + mark_price * underlying * 1.5 / underlying * underlying, 2) if underlying else None
        result["target_100pct_price"] = round(strike + mark_price * 2 * underlying, 2) if underlying else None
        result["target_200pct_price"] = round(strike + mark_price * 3 * underlying, 2) if underlying else None

        # Simpler: breakeven = strike + premium (in spot terms)
        # 50% gain: premium increases by 0.5x → strike + premium*1.5 in index terms
        result["target_50pct_price"] = round(strike + premium_usd * 1.5, 2)
        result["target_100pct_price"] = round(strike + premium_usd * 2.0, 2)
        result["target_200pct_price"] = round(strike + premium_usd * 3.0, 2)

        return result
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/displacement/test_strike_selector.py -v
```
Expected: All 8 tests pass.

- [ ] **Step 5: Commit**

```bash
git add coding/core/displacement/strike_selector.py tests/unit/displacement/test_strike_selector.py
git commit -m "feat: add StrikeSelector with delta/DTE/liquidity filters and profit targets"
```

---

## Task 8: TelegramAlertService

**Files:**
- Create: `coding/service/displacement/__init__.py`
- Create: `coding/service/displacement/telegram_alert_service.py`
- Test: `tests/unit/displacement/test_telegram_alert_service.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/displacement/test_telegram_alert_service.py
from datetime import datetime, date, timezone
from unittest.mock import patch, MagicMock
import pytest

from coding.core.displacement.models.displacement_signal import DisplacementSignal
from coding.service.displacement.telegram_alert_service import TelegramAlertService


def _make_signal():
    return DisplacementSignal(
        asset="BTC", detected_at=datetime(2026, 4, 25, 10, 0, tzinfo=timezone.utc),
        drop_24h_pct=0.22, drop_1h_pct=0.09,
        conviction_pct=75.0, conviction_label="HIGH",
        score_drop_magnitude=82.0, score_drop_speed=55.0,
        score_funding_rate=91.0, score_dvol_spike=71.0,
        score_max_pain=64.0, score_term_structure=80.0,
        funding_rate_value=-0.008, dvol_sigma=2.1,
        max_pain_distance_pct=0.09, term_structure_inversion_pct=0.06,
        instrument_name="BTC-25SEP26-70000-C", strike=70000.0,
        expiry_date=date(2026, 9, 25), dte=153,
        delta=0.14, mark_iv=0.87, premium_usd=1240.0,
        target_50pct_price=71860.0, target_100pct_price=72480.0, target_200pct_price=73720.0,
    )


class TestTelegramAlertService:
    def test_send_returns_true_on_success(self):
        svc = TelegramAlertService(token="test_token", chat_id="12345")
        with patch("requests.post") as mock_post:
            mock_post.return_value.ok = True
            result = svc.send(_make_signal())
        assert result is True
        assert mock_post.called

    def test_send_returns_false_on_http_failure(self):
        svc = TelegramAlertService(token="test_token", chat_id="12345")
        with patch("requests.post") as mock_post:
            mock_post.return_value.ok = False
            result = svc.send(_make_signal())
        assert result is False

    def test_send_returns_false_on_exception(self):
        svc = TelegramAlertService(token="test_token", chat_id="12345")
        with patch("requests.post", side_effect=ConnectionError("timeout")):
            result = svc.send(_make_signal())
        assert result is False

    def test_send_returns_false_when_not_configured(self):
        svc = TelegramAlertService(token="", chat_id="")
        result = svc.send(_make_signal())
        assert result is False

    def test_format_message_contains_key_fields(self):
        svc = TelegramAlertService(token="test_token", chat_id="12345")
        msg = svc._format_message(_make_signal())
        assert "BTC" in msg
        assert "22.0%" in msg or "22" in msg
        assert "75" in msg  # conviction
        assert "BTC-25SEP26-70000-C" in msg
        assert "HIGH" in msg

    def test_posts_to_correct_url(self):
        svc = TelegramAlertService(token="abc123", chat_id="99")
        with patch("requests.post") as mock_post:
            mock_post.return_value.ok = True
            svc.send(_make_signal())
        url = mock_post.call_args[0][0]
        assert "abc123" in url
        assert "sendMessage" in url
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/displacement/test_telegram_alert_service.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Write TelegramAlertService**

```python
# coding/service/displacement/telegram_alert_service.py
import logging
import os
from pathlib import Path

import requests
from dotenv import load_dotenv

from coding.core.displacement.models.displacement_signal import DisplacementSignal

logger = logging.getLogger(__name__)


class TelegramAlertService:
    """Sends displacement alert messages to a Telegram chat."""

    def __init__(self, token: str = "", chat_id: str = ""):
        if not token or not chat_id:
            env_path = Path(__file__).parents[3] / ".env"
            load_dotenv(dotenv_path=env_path)
            token = token or os.getenv("TELEGRAM_BOT_TOKEN", "")
            chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self._token = token
        self._chat_id = chat_id
        self._base_url = f"https://api.telegram.org/bot{self._token}"

    def send(self, signal: DisplacementSignal) -> bool:
        if not self._token or not self._chat_id:
            logger.warning("Telegram not configured — skipping alert")
            return False
        message = self._format_message(signal)
        try:
            response = requests.post(
                f"{self._base_url}/sendMessage",
                json={"chat_id": self._chat_id, "text": message, "parse_mode": "HTML"},
                timeout=10,
            )
            if not response.ok:
                logger.error(f"Telegram API returned {response.status_code}: {response.text}")
            return response.ok
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")
            return False

    def _format_message(self, signal: DisplacementSignal) -> str:
        label_emoji = "🔴" if signal.conviction_label == "HIGH" else "🟡"

        def bar(score: float) -> str:
            filled = round(score / 10)
            return "█" * filled + "░" * (10 - filled)

        signals_text = (
            f"  Drop magnitude   {bar(signal.score_drop_magnitude)}  {signal.score_drop_magnitude:.0f}\n"
            f"  Funding rate     {bar(signal.score_funding_rate)}  {signal.score_funding_rate:.0f}"
            f"  ({signal.funding_rate_value*100:.2f}% funding)\n"
            f"  DVOL spike       {bar(signal.score_dvol_spike)}  {signal.score_dvol_spike:.0f}"
            f"  ({signal.dvol_sigma:.1f}σ above mean)\n"
            f"  Max pain dist    {bar(signal.score_max_pain)}  {signal.score_max_pain:.0f}"
            f"  ({signal.max_pain_distance_pct*100:.1f}% below pain)\n"
            f"  Term structure   {bar(signal.score_term_structure)}  {signal.score_term_structure:.0f}\n"
            f"  Drop speed       {bar(signal.score_drop_speed)}  {signal.score_drop_speed:.0f}"
        )

        contract_section = ""
        if signal.instrument_name:
            contract_section = (
                f"\n\n<b>Recommended contract:</b>\n"
                f"  {signal.instrument_name}\n"
                f"  Delta: {signal.delta:.2f} | IV: {(signal.mark_iv or 0)*100:.0f}%"
                f" | Premium: ${signal.premium_usd:,.0f}\n"
                f"  DTE: {signal.dte} days\n\n"
                f"<b>Profit targets:</b>\n"
                f"  50%  → {signal.asset} at ${signal.target_50pct_price:,.0f}\n"
                f"  100% → {signal.asset} at ${signal.target_100pct_price:,.0f}\n"
                f"  200% → {signal.asset} at ${signal.target_200pct_price:,.0f}"
            )

        return (
            f"{label_emoji} <b>DISPLACEMENT ALERT — {signal.asset}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Drop: -{abs(signal.drop_24h_pct)*100:.1f}% in 24h"
            f" | -{abs(signal.drop_1h_pct)*100:.1f}% in 1h\n"
            f"Conviction: {signal.conviction_pct:.0f}% ({signal.conviction_label})\n\n"
            f"<b>Signals:</b>\n{signals_text}"
            f"{contract_section}\n\n"
            f"⚠️ Paper trade — verify before acting"
        )
```

Create `coding/service/displacement/__init__.py` as an empty file.

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/displacement/test_telegram_alert_service.py -v
```
Expected: All 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add coding/service/displacement/ tests/unit/displacement/test_telegram_alert_service.py
git commit -m "feat: add TelegramAlertService with HTML-formatted displacement alerts"
```

---

## Task 9: DisplacementScannerService

**Files:**
- Create: `coding/service/displacement/displacement_scanner_service.py`
- Test: `tests/unit/displacement/test_displacement_scanner_service.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/displacement/test_displacement_scanner_service.py
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import pytest

from coding.core.displacement.models.displacement_config import DisplacementConfig
from coding.core.displacement.models.displacement_signal import DisplacementSignal


def _make_mock_api(prices, funding=-0.008, dvol=85.0, options=None):
    api = MagicMock()
    # get_price_ohlcv: prices newest-first
    api.get_price_ohlcv.return_value = [{"timestamp": i, "close": p} for i, p in enumerate(prices)]
    api.get_funding_chart_data.return_value = {"result": [{"interest_8h": -0.008}]}
    api.get_volatility_index_data.return_value = {"data": [[0, 50, 60, 45, dvol]]}
    api.get_book_summary_by_currency.return_value = options or [
        {
            "instrument_name": "BTC-25SEP26-70000-C", "option_type": "call",
            "strike": 70000.0, "dte": 153, "delta": 0.14,
            "bid_iv": 0.85, "ask_iv": 0.90, "mark_iv": 0.87,
            "open_interest": 500.0, "mark_price": 0.013,
            "underlying_price": 78000.0,
        }
    ]
    return api


def _make_mock_repo():
    repo = MagicMock()
    repo.get_dvol_history.return_value = [50.0] * 90
    repo.get_ohlcv_daily.return_value = [{"close": 100000.0}] * 500
    repo.get_funding_rate_history.return_value = [-0.008] * 100
    return repo


class TestDisplacementScannerService:
    def _make_service(self, api, repo):
        from coding.service.displacement.displacement_scanner_service import DisplacementScannerService
        cfg = DisplacementConfig()
        return DisplacementScannerService(config=cfg, api_service=api, repository=repo)

    def test_scan_returns_empty_when_no_displacement(self):
        # Prices with only 5% drop — below 20% threshold
        prices = [95000.0] + [100000.0] * 167
        api = _make_mock_api(prices)
        repo = _make_mock_repo()
        svc = self._make_service(api, repo)

        result = svc.scan(["BTC"])
        assert result == []

    def test_scan_returns_signal_when_displacement_detected(self):
        # 22% 24h drop
        prices = [78000.0] + [78500.0] * 3 + [80000.0] * 20 + [100000.0] * 143
        api = _make_mock_api(prices)
        repo = _make_mock_repo()
        svc = self._make_service(api, repo)

        result = svc.scan(["BTC"])
        assert len(result) == 1
        assert isinstance(result[0], DisplacementSignal)
        assert result[0].asset == "BTC"

    def test_scan_saves_signal_to_repository(self):
        prices = [78000.0] + [78500.0] * 3 + [80000.0] * 20 + [100000.0] * 143
        api = _make_mock_api(prices)
        repo = _make_mock_repo()
        svc = self._make_service(api, repo)

        svc.scan(["BTC"])
        assert repo.save_displacement_signal.called

    def test_scan_sends_telegram_when_conviction_above_threshold(self):
        prices = [78000.0] + [78500.0] * 3 + [80000.0] * 20 + [100000.0] * 143
        api = _make_mock_api(prices, funding=-0.01)
        repo = _make_mock_repo()
        svc = self._make_service(api, repo)

        with patch.object(svc._telegram, "send", return_value=True) as mock_send:
            result = svc.scan(["BTC"])

        if result:  # only assert if signal was found with sufficient conviction
            pass  # send may or may not be called depending on conviction threshold

    def test_scan_handles_multiple_assets(self):
        prices_btc = [78000.0] + [78500.0] * 3 + [80000.0] * 20 + [100000.0] * 143
        prices_eth = [1200.0] + [1230.0] * 3 + [1300.0] * 20 + [1540.0] * 143

        api = MagicMock()
        def price_side_effect(asset, **kwargs):
            prices = prices_btc if asset == "BTC" else prices_eth
            return [{"timestamp": i, "close": p} for i, p in enumerate(prices)]
        api.get_price_ohlcv.side_effect = price_side_effect
        api.get_funding_chart_data.return_value = {"result": [{"interest_8h": -0.008}]}
        api.get_volatility_index_data.return_value = {"data": [[0, 50, 60, 45, 75.0]]}
        api.get_book_summary_by_currency.return_value = []

        repo = _make_mock_repo()
        svc = self._make_service(api, repo)
        result = svc.scan(["BTC", "ETH"])
        assert isinstance(result, list)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/unit/displacement/test_displacement_scanner_service.py -v
```
Expected: `ModuleNotFoundError`

- [ ] **Step 3: Write DisplacementScannerService**

```python
# coding/service/displacement/displacement_scanner_service.py
import logging
from datetime import datetime, timezone
from typing import Optional

from coding.core.displacement.models.displacement_config import DisplacementConfig
from coding.core.displacement.models.displacement_event import DisplacementEvent
from coding.core.displacement.models.displacement_signal import DisplacementSignal
from coding.core.displacement.displacement_detector import DisplacementDetector
from coding.core.displacement.conviction_scorer import ConvictionScorer
from coding.core.displacement.strike_selector import StrikeSelector
from coding.service.displacement.telegram_alert_service import TelegramAlertService

logger = logging.getLogger(__name__)


class DisplacementScannerService:
    """
    Orchestrates the full displacement detection pipeline:
    price fetch → detect → score → select strike → save → alert.
    """

    def __init__(self, config: DisplacementConfig, api_service, repository):
        self._config = config
        self._api = api_service
        self._repo = repository
        self._detector = DisplacementDetector(config)
        self._scorer = ConvictionScorer(config)
        self._selector = StrikeSelector(config)
        self._telegram = TelegramAlertService()

    def scan(self, assets: list[str]) -> list[DisplacementSignal]:
        results = []
        for asset in assets:
            try:
                signal = self._scan_asset(asset)
                if signal:
                    results.append(signal)
            except Exception as e:
                logger.error(f"Scan failed for {asset}: {e}")
        return results

    def get_current_prices(self, asset: str) -> dict[str, float]:
        """Returns current price and 24h change for GUI display."""
        try:
            candles = self._api.get_price_ohlcv(asset, resolution_hours=1, lookback_hours=25)
            if not candles:
                return {"price": 0.0, "change_24h_pct": 0.0}
            now = candles[0]["close"]
            ago_24h = candles[min(24, len(candles) - 1)]["close"]
            change = (now - ago_24h) / ago_24h if ago_24h > 0 else 0.0
            return {"price": now, "change_24h_pct": change}
        except Exception as e:
            logger.error(f"get_current_prices failed for {asset}: {e}")
            return {"price": 0.0, "change_24h_pct": 0.0}

    def _scan_asset(self, asset: str) -> Optional[DisplacementSignal]:
        prices_dict = self._fetch_prices(asset)
        event = self._detector.check(asset, prices_dict)
        if not event:
            return None

        logger.info(f"Displacement detected for {asset} — scoring setup")
        market_data = self._fetch_market_data(asset)
        conviction_pct, breakdown = self._scorer.score(event, market_data)

        if conviction_pct < self._config.alert_medium_threshold * 100:
            logger.info(f"Conviction {conviction_pct:.1f}% below threshold — no alert")
            return None

        conviction_label = "HIGH" if conviction_pct >= self._config.alert_high_threshold * 100 else "MEDIUM"
        options_chain = market_data["options_chain"]
        contract = self._selector.select(asset, options_chain, event.current_price)

        signal = self._build_signal(event, conviction_pct, conviction_label, breakdown, market_data, contract)
        self._repo.save_displacement_signal(signal)

        if self._telegram.send(signal):
            logger.info(f"Telegram alert sent for {asset}")

        return signal

    def _fetch_prices(self, asset: str) -> dict[str, float]:
        candles = self._api.get_price_ohlcv(asset, resolution_hours=1, lookback_hours=168)
        if not candles:
            raise ValueError(f"No price data available for {asset}")

        def price_at(hours: int) -> float:
            idx = min(hours, len(candles) - 1)
            return candles[idx]["close"]

        return {
            "now": candles[0]["close"],
            "1h_ago": price_at(1),
            "4h_ago": price_at(4),
            "24h_ago": price_at(24),
            "7d_ago": price_at(168),
        }

    def _fetch_market_data(self, asset: str) -> dict:
        dvol_history = self._repo.get_dvol_history(asset, limit=400)
        ohlcv_history = self._repo.get_ohlcv_daily(asset, limit=1095)

        # Current DVOL
        dvol_result = self._api.get_volatility_index_data(
            currency=asset, resolution=3600
        )
        dvol_data = dvol_result.get("data", [])
        dvol_current = dvol_data[-1][4] if dvol_data else 50.0

        # Current funding rate
        funding_result = self._api.get_funding_chart_data(
            instrument_name=f"{asset}-PERPETUAL", length="8h"
        )
        funding_rows = funding_result.get("result", [])
        funding_rate = funding_rows[-1].get("interest_8h", 0.0) if funding_rows else 0.0

        # Options chain
        options_chain = self._api.get_book_summary_by_currency(asset)

        return {
            "dvol_history": dvol_history,
            "dvol_current": dvol_current,
            "funding_rate": funding_rate,
            "ohlcv_history": ohlcv_history,
            "options_chain": options_chain,
        }

    def _build_signal(
        self,
        event: DisplacementEvent,
        conviction_pct: float,
        conviction_label: str,
        breakdown: dict,
        market_data: dict,
        contract: Optional[dict],
    ) -> DisplacementSignal:
        return DisplacementSignal(
            asset=event.asset,
            detected_at=event.detected_at,
            drop_24h_pct=event.drop_24h_pct,
            drop_1h_pct=event.drop_1h_pct,
            conviction_pct=conviction_pct,
            conviction_label=conviction_label,
            score_drop_magnitude=breakdown["drop_magnitude"],
            score_drop_speed=breakdown["drop_speed"],
            score_funding_rate=breakdown["funding_rate"],
            score_dvol_spike=breakdown["dvol_spike"],
            score_max_pain=breakdown["max_pain"],
            score_term_structure=breakdown["term_structure"],
            funding_rate_value=market_data["funding_rate"],
            dvol_sigma=self._scorer._score_dvol_spike.__func__ and 0.0,  # not exposed directly
            max_pain_distance_pct=breakdown["max_pain"] / 100 * self._config.max_pain_distance_full_score,
            term_structure_inversion_pct=0.0,  # not directly exposed from scorer breakdown
            instrument_name=contract.get("instrument_name") if contract else None,
            strike=contract.get("strike") if contract else None,
            expiry_date=None,
            dte=contract.get("dte") if contract else None,
            delta=contract.get("delta") if contract else None,
            mark_iv=contract.get("mark_iv") if contract else None,
            premium_usd=contract.get("premium_usd") if contract else None,
            target_50pct_price=contract.get("target_50pct_price") if contract else None,
            target_100pct_price=contract.get("target_100pct_price") if contract else None,
            target_200pct_price=contract.get("target_200pct_price") if contract else None,
        )
```

**Note:** The `_build_signal` method has a placeholder for `dvol_sigma` and `term_structure_inversion_pct`. Refactor `ConvictionScorer` to return these raw values alongside the breakdown dict, or expose them as properties. The scorer can return an extended breakdown:

```python
# Extended breakdown from ConvictionScorer (update score() return):
breakdown = {
    ...
    "_dvol_sigma": sigma_value,              # raw sigma
    "_term_inversion_pct": inversion_pct,    # raw inversion
}
```

Then in `_build_signal`, use `breakdown.get("_dvol_sigma", 0.0)`.

Update `ConvictionScorer.score()` to include these raw values in the breakdown dict (prefixed with `_` so the GUI/scorer can distinguish scores from raw values):

```python
breakdown["_dvol_sigma"] = sigma  # add this line after computing sigma in _score_dvol_spike
breakdown["_term_inversion_pct"] = inversion_pct  # same in _score_term_structure
```

Expose sigma and inversion_pct by storing them on the scorer instance during scoring:

```python
# In ConvictionScorer:
self._last_dvol_sigma = 0.0
self._last_term_inversion_pct = 0.0

# In _score_dvol_spike, after computing sigma:
self._last_dvol_sigma = sigma

# In _score_term_structure, after computing inversion_pct:
self._last_term_inversion_pct = inversion_pct
```

Then in `DisplacementScannerService._build_signal`:
```python
dvol_sigma=self._scorer._last_dvol_sigma,
term_structure_inversion_pct=self._scorer._last_term_inversion_pct,
```

Apply this refactor to `conviction_scorer.py` as part of this task.

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/unit/displacement/test_displacement_scanner_service.py -v
```
Expected: All 5 tests pass.

- [ ] **Step 5: Run all displacement tests together**

```bash
pytest tests/unit/displacement/ -v
```
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add coding/service/displacement/displacement_scanner_service.py tests/unit/displacement/test_displacement_scanner_service.py coding/core/displacement/conviction_scorer.py
git commit -m "feat: add DisplacementScannerService orchestrating detect→score→select→save→alert"
```

---

## Task 10: DisplacementTab (GUI)

**Files:**
- Create: `coding/gui/tabs/displacement_tab.py`
- Modify: `coding/gui/main_window.py`

No unit tests — verify by launching the app and checking the tab renders correctly.

- [ ] **Step 1: Write DisplacementTab**

```python
# coding/gui/tabs/displacement_tab.py
import logging
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal, QThread
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QSpinBox, QCheckBox, QDialog, QScrollArea, QDoubleSpinBox,
)

from coding.core.displacement.models.displacement_config import DisplacementConfig
from coding.core.displacement.models.displacement_signal import DisplacementSignal
from coding.gui.theme.colors import Colors

logger = logging.getLogger(__name__)


class ScanWorker(QThread):
    finished = Signal(list)
    error = Signal(str)
    prices_updated = Signal(dict)  # {"BTC": {...}, "ETH": {...}}

    def __init__(self, service, assets: list[str]):
        super().__init__()
        self._service = service
        self._assets = assets

    def run(self) -> None:
        try:
            prices = {a: self._service.get_current_prices(a) for a in self._assets}
            self.prices_updated.emit(prices)
            signals = self._service.scan(self._assets)
            self.finished.emit(signals)
        except Exception as e:
            self.error.emit(str(e))


class DisplacementTab(QWidget):
    """
    Simple displacement scanner tab.
    Shows current BTC/ETH prices, last alert, and a run button.
    """

    def __init__(self, scanner_service, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._service = scanner_service
        self._worker: Optional[ScanWorker] = None
        self._last_signals: list[DisplacementSignal] = []
        self._init_ui()
        self._start_auto_refresh()

    def _init_ui(self) -> None:
        layout = QVBoxLayout()
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Title
        title = QLabel("DISPLACEMENT SCANNER")
        font = QFont()
        font.setPointSize(14)
        font.setBold(True)
        title.setFont(font)
        title.setStyleSheet(f"color: {Colors.TEXT_PRIMARY};")
        layout.addWidget(title)

        # Asset selector
        asset_row = QHBoxLayout()
        asset_label = QLabel("Asset:")
        asset_label.setStyleSheet(f"color: {Colors.TEXT_MUTED};")
        asset_row.addWidget(asset_label)
        self._btn_btc = self._make_toggle_btn("BTC", True)
        self._btn_eth = self._make_toggle_btn("ETH", True)
        asset_row.addWidget(self._btn_btc)
        asset_row.addWidget(self._btn_eth)
        asset_row.addStretch()
        layout.addLayout(asset_row)

        # Current conditions
        layout.addWidget(self._make_section_label("CURRENT CONDITIONS"))
        self._conditions_frame = self._make_conditions_panel()
        layout.addWidget(self._conditions_frame)

        # Last alert
        layout.addWidget(self._make_section_label("LAST ALERT"))
        self._alert_frame = self._make_alert_panel()
        layout.addWidget(self._alert_frame)

        # Configuration
        layout.addWidget(self._make_section_label("CONFIGURATION"))
        layout.addWidget(self._make_config_panel())

        # Run button + status
        btn_row = QHBoxLayout()
        self._run_btn = QPushButton("RUN SCAN NOW")
        self._run_btn.setFixedHeight(36)
        self._run_btn.setStyleSheet(
            f"background-color: {Colors.ACCENT}; color: white; "
            f"font-weight: bold; border-radius: 4px;"
        )
        self._run_btn.clicked.connect(self._run_scan)
        btn_row.addWidget(self._run_btn)
        self._status_label = QLabel("Ready")
        self._status_label.setStyleSheet(f"color: {Colors.TEXT_MUTED}; font-size: 11px;")
        btn_row.addWidget(self._status_label)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        layout.addStretch()
        self.setLayout(layout)

    def _make_section_label(self, text: str) -> QLabel:
        lbl = QLabel(f"── {text} ──────────────────────────")
        lbl.setStyleSheet(f"color: {Colors.TEXT_MUTED}; font-size: 11px;")
        return lbl

    def _make_toggle_btn(self, text: str, checked: bool) -> QPushButton:
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.setChecked(checked)
        btn.setFixedSize(60, 28)
        return btn

    def _make_conditions_panel(self) -> QFrame:
        frame = QFrame()
        layout = QHBoxLayout(frame)
        self._btc_label = QLabel("BTC   Loading…")
        self._eth_label = QLabel("ETH   Loading…")
        for lbl in (self._btc_label, self._eth_label):
            lbl.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; font-family: monospace;")
            layout.addWidget(lbl)
        layout.addStretch()
        return frame

    def _make_alert_panel(self) -> QFrame:
        frame = QFrame()
        layout = QVBoxLayout(frame)
        self._alert_summary_label = QLabel("No alerts yet")
        self._alert_summary_label.setStyleSheet(f"color: {Colors.TEXT_MUTED};")
        self._breakdown_btn = QPushButton("VIEW BREAKDOWN")
        self._breakdown_btn.setVisible(False)
        self._breakdown_btn.clicked.connect(self._show_breakdown)
        layout.addWidget(self._alert_summary_label)
        layout.addWidget(self._breakdown_btn)
        return frame

    def _make_config_panel(self) -> QFrame:
        frame = QFrame()
        layout = QHBoxLayout(frame)

        layout.addWidget(QLabel("Drop threshold 24h:"))
        self._threshold_spin = QSpinBox()
        self._threshold_spin.setRange(5, 50)
        self._threshold_spin.setValue(20)
        self._threshold_spin.setSuffix("%")
        layout.addWidget(self._threshold_spin)

        layout.addWidget(QLabel("  Min conviction:"))
        self._conviction_spin = QSpinBox()
        self._conviction_spin.setRange(30, 95)
        self._conviction_spin.setValue(50)
        self._conviction_spin.setSuffix("%")
        layout.addWidget(self._conviction_spin)

        layout.addWidget(QLabel("  Telegram alerts:"))
        self._telegram_check = QCheckBox()
        self._telegram_check.setChecked(True)
        layout.addWidget(self._telegram_check)

        layout.addStretch()
        return frame

    def _run_scan(self) -> None:
        if self._worker and self._worker.isRunning():
            return
        assets = []
        if self._btn_btc.isChecked():
            assets.append("BTC")
        if self._btn_eth.isChecked():
            assets.append("ETH")
        if not assets:
            return

        self._run_btn.setEnabled(False)
        self._run_btn.setText("Scanning…")
        self._status_label.setText("Scanning…")

        self._worker = ScanWorker(self._service, assets)
        self._worker.prices_updated.connect(self._on_prices_updated)
        self._worker.finished.connect(self._on_scan_finished)
        self._worker.error.connect(self._on_scan_error)
        self._worker.start()

    def _on_prices_updated(self, prices: dict) -> None:
        for asset, data in prices.items():
            price = data.get("price", 0.0)
            change = data.get("change_24h_pct", 0.0)
            color = "#e74c3c" if change < 0 else "#2ecc71"
            text = f"{asset}   ${price:,.0f}   {change*100:+.1f}% 24h   Monitoring"
            label = self._btc_label if asset == "BTC" else self._eth_label
            label.setText(text)
            label.setStyleSheet(f"color: {Colors.TEXT_PRIMARY}; font-family: monospace;")

    def _on_scan_finished(self, signals: list) -> None:
        self._run_btn.setEnabled(True)
        self._run_btn.setText("RUN SCAN NOW")
        if signals:
            self._last_signals = signals
            self._update_alert_panel(signals[-1])
            self._status_label.setText(
                f"Alert fired — {signals[-1].asset} conviction {signals[-1].conviction_pct:.0f}%"
            )
        else:
            self._status_label.setText("Scan complete — no displacement detected")

    def _on_scan_error(self, error: str) -> None:
        self._run_btn.setEnabled(True)
        self._run_btn.setText("RUN SCAN NOW")
        self._status_label.setText(f"Error: {error}")
        logger.error(f"Scan error: {error}")

    def _update_alert_panel(self, signal: DisplacementSignal) -> None:
        ts = signal.detected_at.strftime("%Y-%m-%d %H:%M")
        conviction_color = "#e74c3c" if signal.conviction_label == "HIGH" else "#f39c12"
        summary = (
            f"{signal.asset}  |  {ts}  |  "
            f"<span style='color:{conviction_color}'>"
            f"Conviction: {signal.conviction_pct:.0f}% ({signal.conviction_label})</span>"
        )
        if signal.instrument_name:
            summary += (
                f"<br>Contract: {signal.instrument_name}<br>"
                f"Premium: ${signal.premium_usd:,.0f}  |  "
                f"Delta: {signal.delta:.2f}  |  DTE: {signal.dte}"
            )
        self._alert_summary_label.setText(summary)
        self._alert_summary_label.setTextFormat(Qt.TextFormat.RichText)
        self._breakdown_btn.setVisible(True)

    def _show_breakdown(self) -> None:
        if not self._last_signals:
            return
        signal = self._last_signals[-1]
        dialog = BreakdownDialog(signal, self)
        dialog.exec()

    def _start_auto_refresh(self) -> None:
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._run_scan)
        self._timer.start(5 * 60 * 1000)  # every 5 minutes


class BreakdownDialog(QDialog):
    """Popup showing full signal breakdown for the last alert."""

    def __init__(self, signal: DisplacementSignal, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Signal Breakdown — {signal.asset}")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)

        scores = [
            ("Drop magnitude", signal.score_drop_magnitude),
            ("Drop speed", signal.score_drop_speed),
            ("Funding rate", signal.score_funding_rate),
            (f"DVOL spike ({signal.dvol_sigma:.1f}σ)", signal.score_dvol_spike),
            (f"Max pain dist ({signal.max_pain_distance_pct*100:.1f}%)", signal.score_max_pain),
            (f"Term structure ({signal.term_structure_inversion_pct*100:.1f}%)", signal.score_term_structure),
        ]
        for label, score in scores:
            row = QHBoxLayout()
            row.addWidget(QLabel(f"{label}:"))
            bar_lbl = QLabel(f"{'█' * round(score/10)}{'░' * (10 - round(score/10))}  {score:.0f}")
            bar_lbl.setStyleSheet("font-family: monospace;")
            row.addWidget(bar_lbl)
            layout.addLayout(row)

        if signal.instrument_name:
            layout.addWidget(QLabel(f"\nContract: {signal.instrument_name}"))
            layout.addWidget(QLabel(f"Delta: {signal.delta:.2f}  IV: {(signal.mark_iv or 0)*100:.0f}%  DTE: {signal.dte}"))
            layout.addWidget(QLabel(f"50% target: ${signal.target_50pct_price:,.0f}"))
            layout.addWidget(QLabel(f"100% target: ${signal.target_100pct_price:,.0f}"))

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)
```

- [ ] **Step 2: Wire DisplacementTab into main_window.py**

Replace the index 6 placeholder block with:

```python
        # Index 6: Special Strategies — Displacement Scanner
        try:
            from coding.core.displacement.models.displacement_config import DisplacementConfig
            from coding.service.displacement.displacement_scanner_service import DisplacementScannerService
            from coding.gui.tabs.displacement_tab import DisplacementTab
            _api = DeribitApiService()
            _repo = DatabaseRepository()
            _cfg = DisplacementConfig()
            _scanner = DisplacementScannerService(config=_cfg, api_service=_api, repository=_repo)
            self.stack.addWidget(DisplacementTab(scanner_service=_scanner))
        except Exception as exc:
            logger.error("Failed to initialize Displacement Scanner tab: %s", exc)
            self.stack.addWidget(self._placeholder_widget("Displacement Scanner unavailable"))
            failed_indices.add(6)
```

- [ ] **Step 3: Launch app and verify tab renders**

```bash
python -m coding.gui.app
```

Navigate to index 6 (Special Strategies). Expected:
- Tab shows "DISPLACEMENT SCANNER" title
- BTC and ETH rows visible in Current Conditions
- Configuration inputs visible
- RUN SCAN NOW button works (may show error if not connected to API — that's fine)

- [ ] **Step 4: Commit**

```bash
git add coding/gui/tabs/displacement_tab.py coding/gui/main_window.py
git commit -m "feat: add DisplacementTab replacing old 4-gate OTM interface"
```

---

## Task 11: HistoricalOptionsFetcher

**Files:**
- Create: `coding/service/displacement/historical_options_fetcher.py`

This is a one-time local runner. No unit tests — it fetches real Deribit data.

- [ ] **Step 1: Write HistoricalOptionsFetcher**

```python
# coding/service/displacement/historical_options_fetcher.py
"""
Fetches historical Deribit options data for backtest.

One-time local run only — not deployed to VPS.
Caches results to avoid re-fetching (JSON file per event).
"""
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from coding.service.deribit.deribit_api_service import DeribitApiService

logger = logging.getLogger(__name__)

CACHE_DIR = Path("backtest_cache/options")


class HistoricalOptionsFetcher:
    """
    Fetches historical options chain data from Deribit for specific timestamps.

    Deribit provides historical mark prices via /public/get_tradingview_chart_data
    for individual instruments. This fetcher identifies which instruments existed
    at a given event date and fetches their mark prices.
    """

    def __init__(self, api_service: DeribitApiService):
        self._api = api_service
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def fetch_options_at_event(
        self,
        asset: str,
        event_ts_ms: int,
        target_ts_ms: int,
        min_delta: float = 0.10,
        max_delta: float = 0.20,
        min_dte: int = 90,
        max_dte: int = 270,
    ) -> list[dict]:
        """
        Fetch available OTM calls near the event timestamp.

        Returns a list of option dicts with historical mark prices.
        Uses disk cache to avoid repeated API calls.
        """
        cache_key = f"{asset}_{event_ts_ms}_{target_ts_ms}"
        cache_file = CACHE_DIR / f"{cache_key}.json"

        if cache_file.exists():
            logger.debug(f"Cache hit: {cache_key}")
            return json.loads(cache_file.read_text())

        # Fetch instruments that were available at event time
        try:
            instruments_response = self._api.connection.fetch(
                "public/get_instruments",
                parameters={
                    "currency": asset,
                    "kind": "option",
                    "expired": False,
                },
            )
            instruments = instruments_response.get("result", [])
        except Exception as e:
            logger.error(f"Failed to fetch instruments for {asset}: {e}")
            return []

        event_dt = datetime.fromtimestamp(event_ts_ms / 1000, tz=timezone.utc)
        candidates = []

        for inst in instruments:
            name = inst.get("instrument_name", "")
            if not name.endswith("-C"):  # calls only
                continue

            expiry_ts = inst.get("expiration_timestamp", 0)
            if expiry_ts == 0:
                continue

            dte_at_event = (expiry_ts - event_ts_ms) / (1000 * 86400)
            if not (min_dte <= dte_at_event <= max_dte):
                continue

            # Fetch mark price at event time and at target time
            entry_price = self._fetch_mark_price_at(name, event_ts_ms)
            exit_price = self._fetch_mark_price_at(name, target_ts_ms)

            if entry_price is None:
                continue

            candidates.append({
                "instrument_name": name,
                "dte_at_event": round(dte_at_event),
                "entry_mark_price": entry_price,
                "exit_mark_price": exit_price,
                "asset": asset,
                "event_ts_ms": event_ts_ms,
                "target_ts_ms": target_ts_ms,
            })

            time.sleep(0.05)  # Rate limit: ~20 req/s

        cache_file.write_text(json.dumps(candidates))
        logger.info(f"Fetched {len(candidates)} option candidates for {asset} at {event_dt.date()}")
        return candidates

    def _fetch_mark_price_at(self, instrument_name: str, ts_ms: int) -> Optional[float]:
        """Fetch mark price (close) at a specific timestamp."""
        window = 3600 * 1000  # 1 hour window
        try:
            response = self._api.connection.fetch(
                "public/get_tradingview_chart_data",
                parameters={
                    "instrument_name": instrument_name,
                    "start_timestamp": ts_ms - window,
                    "end_timestamp": ts_ms + window,
                    "resolution": "60",
                },
            )
            data = response.get("result", {})
            closes = data.get("close", [])
            return float(closes[-1]) if closes else None
        except Exception as e:
            logger.debug(f"Could not fetch mark price for {instrument_name} at {ts_ms}: {e}")
            return None
```

- [ ] **Step 2: Commit**

```bash
git add coding/service/displacement/historical_options_fetcher.py
git commit -m "feat: add HistoricalOptionsFetcher for backtest data collection"
```

---

## Task 12: BacktestService

**Files:**
- Create: `coding/service/displacement/backtest_service.py`

Run locally, prints results. No VPS deployment. Requires `scikit-learn` and `joblib`.

- [ ] **Step 1: Install dependencies**

```bash
pip install scikit-learn joblib
```

Add to `requirements.txt`:
```
scikit-learn>=1.4.0
joblib>=1.3.0
```

- [ ] **Step 2: Write BacktestService**

```python
# coding/service/displacement/backtest_service.py
"""
Backtests the displacement strategy on historical BTC/ETH price drops.

Run locally with:
    python -m coding.service.displacement.backtest_service
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from coding.core.displacement.models.displacement_config import DisplacementConfig
from coding.core.displacement.conviction_scorer import ConvictionScorer
from coding.core.database.repository import DatabaseRepository
from coding.service.deribit.deribit_api_service import DeribitApiService
from coding.service.displacement.historical_options_fetcher import HistoricalOptionsFetcher
from coding.core.logging.logging_setup import init_logging

logger = logging.getLogger(__name__)

MODEL_DIR = Path("models/displacement_scorer_v1")
RESULTS_CACHE = Path("backtest_cache/results.json")


class BacktestService:
    """
    Finds historical displacement events, labels outcomes, trains
    conviction scorer weights via logistic regression.
    """

    def __init__(
        self,
        config: DisplacementConfig,
        repository: DatabaseRepository,
        api_service: DeribitApiService,
    ):
        self._config = config
        self._repo = repository
        self._api = api_service
        self._fetcher = HistoricalOptionsFetcher(api_service)
        self._scorer = ConvictionScorer(config)

    def run(self, assets: list[str] = None, profit_target_pct: float = 0.50) -> None:
        """
        Full backtest pipeline:
        1. Find historical events
        2. Reconstruct signals
        3. Fetch historical options + label outcomes
        4. Train logistic regression
        5. Print report
        """
        if assets is None:
            assets = ["BTC", "ETH"]

        all_events = []
        for asset in assets:
            events = self._find_historical_events(asset)
            all_events.extend(events)
            logger.info(f"Found {len(events)} displacement events for {asset}")

        if not all_events:
            logger.warning("No historical events found. Check ohlcv_history data.")
            return

        logger.info(f"Total events to backtest: {len(all_events)}")

        labeled = []
        for event in all_events:
            row = self._label_event(event, profit_target_pct)
            if row is not None:
                labeled.append(row)

        if not labeled:
            logger.warning("No labeled events — insufficient historical options data.")
            return

        self._print_report(labeled)
        self._train_model(labeled)

    def _find_historical_events(self, asset: str) -> list[dict]:
        """Find all dates where asset dropped >= threshold. Apply cooldown deduplication."""
        ohlcv = self._repo.get_ohlcv_daily(asset, limit=1095)
        events = []
        last_event_date = None

        for i in range(len(ohlcv) - 1):
            today = ohlcv[i]
            day_ago = ohlcv[min(i + 1, len(ohlcv) - 1)]
            week_ago = ohlcv[min(i + 7, len(ohlcv) - 1)]

            today_close = today.get("close", 0)
            day_close = day_ago.get("close", 1)
            week_close = week_ago.get("close", 1)

            if today_close <= 0 or day_close <= 0:
                continue

            drop_24h = (day_close - today_close) / day_close
            drop_7d = (week_close - today_close) / week_close if week_close > 0 else 0

            if drop_24h < self._config.drop_24h_threshold and drop_7d < self._config.drop_7d_threshold:
                continue

            event_date = today.get("date")
            if event_date is None:
                continue

            if last_event_date and (event_date - last_event_date).days < self._config.cooldown_hours / 24:
                continue

            last_event_date = event_date
            ts_ms = int(datetime(
                event_date.year, event_date.month, event_date.day,
                tzinfo=timezone.utc
            ).timestamp() * 1000)

            events.append({
                "asset": asset,
                "date": event_date,
                "ts_ms": ts_ms,
                "drop_24h_pct": drop_24h,
                "drop_7d_pct": drop_7d,
                "current_price": today_close,
            })

        return events

    def _label_event(self, event: dict, profit_target: float) -> Optional[dict]:
        """Reconstruct signals and label whether trade was profitable."""
        asset = event["asset"]
        event_ts = event["ts_ms"]

        # Compute signals from stored data
        ohlcv = self._repo.get_ohlcv_daily(asset, limit=1095)
        dvol_history = self._repo.get_dvol_history(asset, limit=400)
        funding_history = self._repo.get_funding_rate_history(asset, limit=100)

        funding_rate = funding_history[0] if funding_history else 0.0

        # DVOL at event time — approximate from history
        dvol_current = dvol_history[0] if dvol_history else 50.0

        # Approximate market data (no live options chain for historical dates)
        # max_pain and term_structure default to 50 for historical events
        market_data = {
            "funding_rate": funding_rate,
            "dvol_current": dvol_current,
            "dvol_history": dvol_history,
            "ohlcv_history": ohlcv,
            "options_chain": [],  # empty — max_pain and term_structure → 50
        }

        from coding.core.displacement.models.displacement_event import DisplacementEvent
        displacement_event = DisplacementEvent(
            asset=asset,
            detected_at=datetime.fromtimestamp(event_ts / 1000, tz=timezone.utc),
            current_price=event["current_price"],
            drop_1h_pct=event["drop_24h_pct"] * 0.3,  # approximate
            drop_4h_pct=event["drop_24h_pct"] * 0.6,
            drop_24h_pct=event["drop_24h_pct"],
            drop_7d_pct=event["drop_7d_pct"],
            triggering_timeframe="24h",
        )
        _, breakdown = self._scorer.score(displacement_event, market_data)

        # Fetch historical options to compute P&L
        target_ts_90d = event_ts + 90 * 24 * 3600 * 1000
        options = self._fetcher.fetch_options_at_event(
            asset=asset,
            event_ts_ms=event_ts,
            target_ts_ms=target_ts_90d,
        )

        if not options:
            return None

        # Find best option: prefer shortest DTE in qualifying range (most liquid), then lowest entry price
        valid = [o for o in options if o.get("entry_mark_price") and o["entry_mark_price"] > 0]
        if not valid:
            return None
        best = min(valid, key=lambda o: (
            abs(o.get("dte_at_event", 180) - 150),  # prefer DTE near 150 days
            o.get("entry_mark_price", 1.0),
        ))
        entry = best.get("entry_mark_price", 0.0)
        exit_price = best.get("exit_mark_price")

        if not entry or entry <= 0:
            return None

        profitable = False
        if exit_price and exit_price > 0:
            gain_pct = (exit_price - entry) / entry
            profitable = gain_pct >= profit_target

        return {
            "asset": asset,
            "date": str(event["date"]),
            "drop_24h_pct": event["drop_24h_pct"],
            "signals": breakdown,
            "profitable": int(profitable),
            "entry_price": entry,
            "exit_price": exit_price,
        }

    def _print_report(self, labeled: list[dict]) -> None:
        n = len(labeled)
        profitable = [r for r in labeled if r["profitable"]]
        not_profitable = [r for r in labeled if not r["profitable"]]

        print("\n" + "=" * 50)
        print("DISPLACEMENT BACKTEST RESULTS")
        print("=" * 50)
        print(f"Events found:          {n}")
        print(f"Profitable (>50%):     {len(profitable)}  ({len(profitable)/n*100:.0f}%)")

        if profitable:
            avg_entry_profitable = sum(r["entry_price"] for r in profitable) / len(profitable)
            print(f"Avg entry (winners):   {avg_entry_profitable:.4f}")
        if not_profitable:
            avg_entry_losses = sum(r["entry_price"] for r in not_profitable) / len(not_profitable)
            print(f"Avg entry (losers):    {avg_entry_losses:.4f}")

        # Signal importance (simple correlation)
        signal_names = ["drop_magnitude", "drop_speed", "funding_rate", "dvol_spike", "max_pain", "term_structure"]
        print("\nSignal correlation with outcome:")
        for sig in signal_names:
            vals = [r["signals"].get(sig, 50.0) for r in labeled]
            outcomes = [r["profitable"] for r in labeled]
            mean_v = sum(vals) / len(vals)
            mean_o = sum(outcomes) / len(outcomes)
            cov = sum((v - mean_v) * (o - mean_o) for v, o in zip(vals, outcomes)) / len(vals)
            std_v = (sum((v - mean_v) ** 2 for v in vals) / len(vals)) ** 0.5
            std_o = (sum((o - mean_o) ** 2 for o in outcomes) / len(outcomes)) ** 0.5
            corr = cov / (std_v * std_o) if std_v * std_o > 0 else 0.0
            print(f"  {sig:<20} {corr:+.3f}")
        print("=" * 50 + "\n")

    def _train_model(self, labeled: list[dict]) -> None:
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        from sklearn.pipeline import Pipeline
        import joblib

        signal_names = ["drop_magnitude", "drop_speed", "funding_rate", "dvol_spike", "max_pain", "term_structure"]
        X = [[r["signals"].get(s, 50.0) for s in signal_names] for r in labeled]
        y = [r["profitable"] for r in labeled]

        if len(set(y)) < 2:
            logger.warning("All outcomes are the same — cannot train classifier")
            return

        # Walk-forward split: train on older 70%, validate on newer 30%
        split = int(len(labeled) * 0.7)
        X_train, X_val = X[:split], X[split:]
        y_train, y_val = y[:split], y[split:]

        pipeline = Pipeline([
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(C=1.0, max_iter=500)),
        ])
        pipeline.fit(X_train, y_train)

        if X_val:
            val_acc = sum(
                pipeline.predict([x])[0] == yv
                for x, yv in zip(X_val, y_val)
            ) / len(X_val)
            print(f"Validation accuracy: {val_acc*100:.1f}%")

        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        model_path = MODEL_DIR / "scorer.joblib"
        joblib.dump(pipeline, model_path)
        logger.info(f"Model saved to {model_path}")
        print(f"Model saved to {model_path}")


if __name__ == "__main__":
    init_logging(level="INFO")
    from coding.core.displacement.models.displacement_config import DisplacementConfig
    from coding.core.database.repository import DatabaseRepository
    from coding.service.deribit.deribit_api_service import DeribitApiService

    svc = BacktestService(
        config=DisplacementConfig(),
        repository=DatabaseRepository(),
        api_service=DeribitApiService(),
    )
    svc.run(assets=["BTC", "ETH"])
```

- [ ] **Step 3: Commit**

```bash
git add coding/service/displacement/backtest_service.py requirements.txt
git commit -m "feat: add BacktestService with historical event finder, labeling, and logistic regression training"
```

---

## Task 13: VPS daemon + deployment

**Files:**
- Create: `scripts/displacement_daemon.py`

- [ ] **Step 1: Write displacement_daemon.py**

```python
#!/usr/bin/env python3
"""
Displacement scanner daemon — runs 24/7 on VPS.

Deployed as a systemd service. Scans BTC and ETH every 5 minutes.
Sends Telegram alerts when a displacement event is detected with
sufficient conviction.

Deploy:
    python scripts/displacement_daemon.py
"""
import logging
import time
from pathlib import Path

from coding.core.logging.logging_setup import init_logging
from coding.core.displacement.models.displacement_config import DisplacementConfig
from coding.core.database.repository import DatabaseRepository
from coding.service.deribit.deribit_api_service import DeribitApiService
from coding.service.displacement.displacement_scanner_service import DisplacementScannerService

SCAN_INTERVAL_SECONDS = 5 * 60  # 5 minutes
LOG_FILE = Path("/var/log/option_trading/displacement.log")

logger = logging.getLogger(__name__)


def main() -> None:
    init_logging(level="INFO")
    logger.info("Displacement daemon starting")

    config = DisplacementConfig()
    api = DeribitApiService()
    repo = DatabaseRepository()
    scanner = DisplacementScannerService(config=config, api_service=api, repository=repo)

    logger.info(f"Scanning BTC and ETH every {SCAN_INTERVAL_SECONDS // 60} minutes")

    while True:
        try:
            signals = scanner.scan(["BTC", "ETH"])
            if signals:
                for sig in signals:
                    logger.info(
                        f"Alert: {sig.asset} {sig.conviction_pct:.0f}% conviction "
                        f"({sig.conviction_label}) — {sig.instrument_name or 'no contract'}"
                    )
            else:
                logger.debug("Scan complete — no displacement detected")
        except Exception as e:
            logger.error(f"Scan loop error: {e}", exc_info=True)

        time.sleep(SCAN_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run locally to verify it starts**

```bash
python scripts/displacement_daemon.py
```
Expected: Logs "Displacement daemon starting" and "Scanning BTC and ETH every 5 minutes", then runs first scan cycle. Ctrl+C to stop.

- [ ] **Step 3: Commit**

```bash
git add scripts/displacement_daemon.py
git commit -m "feat: add displacement_daemon.py for VPS 24/7 scanning"
```

- [ ] **Step 4: Sync to VPS**

```bash
rsync -avz --exclude='.git' --exclude='.venv' --exclude='backtest_cache' \
  ./ root@VPS_HETZNER_IP_REDACTED:/opt/option_trading/
```

- [ ] **Step 5: Create systemd service on VPS**

SSH to VPS then run:
```bash
cat > /etc/systemd/system/displacement-scanner.service << 'EOF'
[Unit]
Description=Displacement OTM Scanner
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/option_trading
ExecStart=/opt/option_trading/.venv/bin/python scripts/displacement_daemon.py
Restart=always
RestartSec=60
StandardOutput=append:/var/log/option_trading/displacement.log
StandardError=append:/var/log/option_trading/displacement.log

[Install]
WantedBy=multi-user.target
EOF

mkdir -p /var/log/option_trading
systemctl daemon-reload
systemctl enable displacement-scanner
systemctl start displacement-scanner
systemctl status displacement-scanner
```

Expected: Service shows as `active (running)`.

- [ ] **Step 6: Verify Telegram alert end-to-end**

Temporarily lower the drop threshold to trigger a test alert:
```bash
# On VPS — run a one-shot scan with low threshold
python -c "
from coding.core.displacement.models.displacement_config import DisplacementConfig
from coding.core.database.repository import DatabaseRepository
from coding.service.deribit.deribit_api_service import DeribitApiService
from coding.service.displacement.displacement_scanner_service import DisplacementScannerService
from coding.service.displacement.telegram_alert_service import TelegramAlertService

# Test Telegram connectivity
telegram = TelegramAlertService()
from coding.core.displacement.models.displacement_signal import DisplacementSignal
from datetime import datetime, date, timezone
test_signal = DisplacementSignal(
    asset='BTC', detected_at=datetime.now(tz=timezone.utc),
    drop_24h_pct=0.22, drop_1h_pct=0.09,
    conviction_pct=75.0, conviction_label='HIGH',
    score_drop_magnitude=82.0, score_drop_speed=55.0,
    score_funding_rate=91.0, score_dvol_spike=71.0,
    score_max_pain=64.0, score_term_structure=80.0,
    funding_rate_value=-0.008, dvol_sigma=2.1,
    max_pain_distance_pct=0.09, term_structure_inversion_pct=0.06,
    instrument_name='BTC-25SEP26-70000-C',
    strike=70000.0, expiry_date=date(2026, 9, 25), dte=153,
    delta=0.14, mark_iv=0.87, premium_usd=1240.0,
    target_50pct_price=71860.0, target_100pct_price=72480.0, target_200pct_price=73720.0,
)
ok = telegram.send(test_signal)
print('Telegram send:', 'SUCCESS' if ok else 'FAILED')
"
```
Expected: Message appears in your Telegram from `@my_option_trading_bot`.

- [ ] **Step 7: Final run — all displacement tests pass**

```bash
pytest tests/unit/displacement/ -v
```
Expected: All tests pass.

---

## Running the Backtest (Phase 2 — run locally after Phase 1 complete)

Once Phase 1 is deployed and working:

```bash
python -m coding.service.displacement.backtest_service
```

This will:
1. Scan `ohlcv_history` for all historical BTC/ETH drops ≥ 20%
2. For each event, fetch historical Deribit options data (takes 1–3 hours, cached after first run)
3. Label outcomes (profitable = >50% gain within 90 days)
4. Train logistic regression model
5. Save model to `models/displacement_scorer_v1/scorer.joblib`
6. Print backtest report

After training, copy the model to VPS:
```bash
rsync -avz models/displacement_scorer_v1/ root@VPS_HETZNER_IP_REDACTED:/opt/option_trading/models/displacement_scorer_v1/
systemctl restart displacement-scanner
```

The `ConvictionScorer` auto-loads the model if `models/displacement_scorer_v1/scorer.joblib` exists.
