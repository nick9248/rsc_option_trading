# rsc_option_trading

Automated crypto options trading for BTC and ETH on Deribit. The long-term goal is to trade automatically: on-chain analysis and market analysis pick the best strategy, and every step is validated by forward and backward tests. The current focus is data collection and validating the on-chain analysis.

---

## What it does today

| Feature | Description |
|---------|-------------|
| **Data collection daemon** | Runs 24/7 on a Hetzner VPS (systemd). Every 30 minutes it collects trades, book summaries, on-chain analysis, DVOL, funding rate, daily OHLCV, dated-futures basis and the owner's own Deribit account (read-only). |
| **On-chain analysis** | GEX/DEX by strike, max pain, put/call ratios, volatility surface, buy/sell flow (computed on demand from stored trades), dealer inventory, exposure profiles. |
| **Morning note** | Turns the on-chain analysis into a scored executive summary (`synthesis_logic.md`). |
| **Scanners and forward tests** | Straddle, iron condor and butterfly scanners with Telegram alerts; forward-test harnesses that record predictions and resolve them at settlement. |
| **Health checks** | One shared check registry, run locally (`scripts/validate_system.py`) and on the VPS (`scripts/check_vps_health.py`, hourly cron). |
| **Desktop GUI** | PySide6 app: API connection, snapshot, on-chain analysis, database sync, system health and automation tabs. |

No code in this repo places real orders.

---

## Architecture

```
coding/
├── core/       models, calculators, API client, database repository, health models
├── service/    orchestration: data collection, on-chain, scanners, health, Deribit clients
├── gui/        PySide6 app (no business logic; calls services)
└── pipelines/  fetch-and-process helpers

scripts/        backfills, VPS sync, health check, migration runner
migrations/     numbered, forward-only PostgreSQL migrations
tests/          unit, integration and characterization tests (golden files in tests/golden)
documentation/  methodology, GUI tab notes, VPS operations
strategies/     sample strategies running on the VPS (left alone)
```

Layering: Core -> Service -> GUI/CLI.

---

## Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.13 |
| Market data | Deribit REST API (public, plus a read-only private client for the owner's account) |
| Data | pandas, PostgreSQL 18 (psycopg2) |
| GUI | PySide6, Plotly |
| Alerts | Telegram Bot API |
| Scheduling | APScheduler |
| Deployment | systemd daemon on a Hetzner VPS |

The full dependency list is in `requirements.txt`.

---

## Setup

### 1. Install

```bash
git clone https://github.com/nick9248/rsc_option_trading.git
cd rsc_option_trading
python -m venv .venv
.venv\Scripts\activate          # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure

Copy `.env.example` to `.env` and fill in the values. The code reads these keys:

| Key | Purpose |
|-----|---------|
| `DB_PASSWORD` | PostgreSQL password (required) |
| `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_NAME` | Optional. Defaults: `localhost`, `5433`, `postgres`, `option_trading` |
| `client_id`, `deribit_key` | Optional. Read-only Deribit API credentials for the account collector. Without them the account tables stay empty. |
| `OSF_TELEGRAM_BOT_TOKEN`, `OSF_TELEGRAM_CHAT_ID` | Optional. Telegram scanner alerts |

### 3. Database

```bash
psql -U postgres -c "CREATE DATABASE option_trading;"
python scripts/run_migration.py migrations/000_base_schema.sql
# then every later migration in number order
```

Migrations are forward-only. The production data lives on the VPS; `python -m scripts.sync_from_vps` copies it into the local database.

### 4. Run

```bash
# Desktop GUI
.venv\Scripts\python -m coding.gui.app

# Health check
.venv\Scripts\python -m scripts.validate_system

# Tests
.venv\Scripts\python -m pytest

# Data collection daemon (runs on the VPS under systemd; locally only for debugging)
.venv\Scripts\python -m coding.service.data_collection.collection_daemon
```

---

## Documentation

- `documentation/README.md`: index of the documentation
- `synthesis_logic.md`: scoring specification for the morning note
- `documentation/gex_dex_methodology.md`: GEX/DEX formulas
- `documentation/vps/vps_setup_and_operations.md`: VPS setup and daily operations
- `scripts/README.md`: what each script does

---

## License

MIT
