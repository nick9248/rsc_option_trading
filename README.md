# option_trading

A BTC/ETH options trading decision-support platform built on the Deribit API. Collects market data continuously, runs analysis pipelines, and surfaces actionable signals through a desktop GUI and Telegram alerts.

---

## What it does

| Feature | Description |
|---------|-------------|
| **Market regime detection** | LightGBM classifier trained on realized vol, funding rate, DVOL, and OI to label market state (trending/ranging/volatile) |
| **Displacement scanner** | Detects abnormal price moves relative to options-implied expectations; scores conviction and sends Telegram alerts |
| **On-chain analytics** | GEX/DEX by strike, max pain, P/C ratio, IV surface, block trades — parsed from Deribit snapshot data |
| **Strategy scoring** | Evaluates option strategies (spreads, straddles, condors) against current regime and vol surface |
| **Data collection daemon** | Runs 24/7 on VPS; collects trades, book snapshots, DVOL, funding rate, OHLCV every 30 minutes |
| **Desktop GUI** | PySide6 app with tabs for live data, displacement signals, strategy evaluation, and system health |

---

## Architecture

```
coding/
├── core/           — data models, API client, database, ML, analytics
├── service/        — orchestration: displacement, regime, strategy, Deribit
├── gui/            — PySide6 desktop app (tabs, components, theme)
└── pipelines/      — data processing and feature engineering

scripts/            — data collection, backfill, validation utilities
migrations/         — PostgreSQL schema (14 migration files)
tests/              — 41 unit + integration tests
documentation/      — architecture, VPS setup, API reference
```

---

## Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.13 |
| Market data | Deribit API (REST + WebSocket) |
| ML | LightGBM, scikit-learn, ARCH (GARCH) |
| Data | pandas, PostgreSQL (psycopg2) |
| GUI | PySide6, Plotly |
| Alerts | Telegram Bot API |
| Scheduling | APScheduler |
| Deployment | systemd daemon on Hetzner VPS |

---

## Setup

### 1. Install

```bash
git clone https://github.com/nick9248/rsc_option_trading.git
cd rsc_option_trading
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
```

Edit `.env` — required keys:

| Key | Purpose |
|-----|---------|
| `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` | PostgreSQL connection |
| `DERIBIT_CLIENT_ID`, `DERIBIT_CLIENT_SECRET` | Deribit API credentials |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Displacement alerts |
| `CRYPTO_QUANT_TOKEN` | On-chain data feed |

### 3. Database

```bash
psql -U postgres -c "CREATE DATABASE option_trading;"
# Run migrations in order:
psql -U postgres -d option_trading -f migrations/000_base_schema.sql
# ... through migrations/013_*.sql
```

### 4. Run

```bash
# Desktop GUI
python main.py

# Data collection daemon (VPS)
python -m coding.service.data_collection.collection_daemon

# Displacement scanner daemon (VPS)
python -m scripts.displacement_daemon
```

---

## Deployment

The data collection and displacement scanner run as systemd services on a Hetzner VPS (Ubuntu 24.04). Set `DB_HOST` and `DB_PORT` in `.env` to point at the remote PostgreSQL instance. The GUI syncs from VPS to local via SSH tunnel.

---

## License

MIT
