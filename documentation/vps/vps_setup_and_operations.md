# VPS Setup and Operations

**Date completed**: 2026-03-15
**Status**: Fully operational

> **Audit note 2026-10-12:** sections 6 to 8 and 12 were updated to the code as of this date (tables, sync list, health checks). The remaining sections describe the setup done on 2026-03-15 and were not re-checked against the server itself; this audit could not verify the VPS side. Whether migrations 026 to 030 have been applied on the VPS is also not recorded here: each new migration is applied on the VPS as a separate, explicit deployment step (`python scripts/run_migration.py migrations/NNN_name.sql`).

---

## 1. Why a VPS?

The local PC was running the data collection daemon but sleeping every night (~23:00–16:00), creating 16–24 hour gaps in data. The result was only **55% data coverage** — 65 gaps totalling 516 missing hours out of ~934 possible.

This directly blocked ML training: the first training window requires **720 consecutive hours** of `hourly_snapshots`. At 55% coverage that target would take months to reach.

A VPS running 24/7 eliminates the sleep gap entirely and gets to 720h in approximately 30 days of continuous uptime.

---

## 2. VPS Details

| Field | Value |
|-------|-------|
| **Provider** | Hetzner Cloud |
| **Plan** | CPX11 AMD |
| **Specs** | 2 vCPU, 2GB RAM, 40GB SSD, 1TB bandwidth |
| **Cost** | €5.94/month |
| **Location** | USA (Ashburn VA) |
| **OS** | Ubuntu 24.04 LTS |
| **IPv4** | VPS_HETZNER_IP_REDACTED |
| **Hostname** | option-trading-vps |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────┐
│                  HETZNER VPS                    │
│                                                 │
│  ┌──────────────────────────────────────────┐   │
│  │  option-trading.service (systemd)        │   │
│  │  CollectionDaemon → ProspectiveCollector │   │
│  │  Every 30 minutes, 24/7                  │   │
│  └──────────────────────────────────────────┘   │
│                      ↓                          │
│  ┌──────────────────────────────────────────┐   │
│  │  PostgreSQL (port 5432, localhost only)  │   │
│  │  DB: option_trading  User: nick          │   │
│  └──────────────────────────────────────────┘   │
│                                                 │
│  ┌──────────────────────────────────────────┐   │
│  │  Cron: check_vps_health.py (hourly)      │   │
│  │  Writes: logs/vps_health.json and a row  │   │
│  │  in the health_check_runs table          │   │
│  └──────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
                        ↑
              SSH Tunnel (port 5434)
              sync_from_vps.py
                        ↓
┌─────────────────────────────────────────────────┐
│                  LOCAL PC                       │
│                                                 │
│  ┌──────────────────────────────────────────┐   │
│  │  PostgreSQL (port 5433, localhost)       │   │
│  │  DB: option_trading  User: postgres      │   │
│  └──────────────────────────────────────────┘   │
│                                                 │
│  ┌──────────────────────────────────────────┐   │
│  │  GUI App (PySide6)                       │   │
│  │  Database tab → Sync from VPS button     │   │
│  │  System Health tab → Check VPS Health    │   │
│  └──────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘
```

---

## 4. Security Configuration

### User Access
- **Root login**: disabled (`PermitRootLogin no`)
- **Password authentication**: disabled (`PasswordAuthentication no`)
- **Only access**: SSH key authentication as user `nick`

### SSH Key (local PC)
- **Key file**: `C:\Users\Nick\.ssh\option_trading`
- **SSH config**: `C:\Users\Nick\.ssh\config`

```
Host option-server
    HostName VPS_HETZNER_IP_REDACTED
    User nick
    IdentityFile C:\Users\Nick\.ssh\option_trading
    IdentitiesOnly yes
```

Connect with: `ssh option-server`

### Firewall (UFW)
| Port | Protocol | Purpose |
|------|----------|---------|
| 22 | TCP | SSH |
| 51820 | UDP | Reserved (WireGuard, not yet configured) |

### PostgreSQL
- Bound to `localhost` only — no external port exposure
- Accessed exclusively via SSH tunnel

---

## 5. What Was Set Up (Step by Step)

### Step 1: Server Creation
- Purchased CPX11 on Hetzner Cloud
- Added SSH key during creation (important — must be done at creation, not after)
- SSH key name: `option_trading`

### Step 2: Security Hardening
- Created user `nick` with sudo privileges
- Copied SSH key to `nick` user
- Disabled root login and password authentication in `/etc/ssh/sshd_config`
- Enabled UFW firewall with ports 22/tcp and 51820/udp

### Step 3: Software Installation
- Updated system: `apt update && apt upgrade -y`
- Installed PostgreSQL 16: `apt install postgresql postgresql-contrib`
- Installed Python 3.13 via deadsnakes PPA
- Installed build tools: `python3-pip build-essential libpq-dev git`

### Step 4: Database Configuration
- Created PostgreSQL user `nick` with password `DB_PASSWORD_REDACTED`
- Created database `option_trading` owned by `nick`
- Set `listen_addresses = 'localhost'` in `postgresql.conf`
- PostgreSQL set to auto-start on boot

### Step 5: Project Deployment
- Added VPS SSH key to GitHub (key: `/root/.ssh/github_vps`)
- Configured `/root/.ssh/config` for GitHub
- Cloned repo: `git clone git@github.com:nick9248/rsc_option_trading.git`
- Created Python venv: `python3.13 -m venv .venv`
- Installed dependencies: `pip install -r requirements.txt`
- Created `.env` file with DB credentials and API token

### Step 6: Database Migrations
- Ran `migrations/000_base_schema.sql` — creates all base tables
- Ran migrations 001 through 011 in order (at that time; 24 tables were created)
- Later migrations (012 to 030) are applied one at a time at deployment. After migrations 024 to 030 the schema has 21 tables (see `documentation/system_overview.md`).

**Key code change for VPS**: `coding/core/database/config.py` updated to read `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_NAME` from `.env` (previously hardcoded to 5433/postgres for local PC). Defaults preserved for backward compatibility.

### Step 7: Systemd Daemon Service
- Created `/etc/systemd/system/option-trading.service`
- Service runs as user `nick`
- Auto-restarts on crash (RestartSec=30)
- Enabled on boot: `systemctl enable option-trading`

Service file:
```ini
[Unit]
Description=Option Trading Data Collection Daemon
After=network.target postgresql.service
Requires=postgresql.service

[Service]
Type=simple
User=nick
WorkingDirectory=/home/nick/option_trading
Environment=PYTHONUNBUFFERED=1
ExecStart=/home/nick/option_trading/.venv/bin/python -m coding.service.data_collection.collection_daemon
Restart=always
RestartSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

### Step 8: Sync Script
- `scripts/sync_from_vps.py` — runs on local PC
- Opens SSH tunnel: local port 5434 → VPS port 5432
- Syncs the 20 tables in `SYNC_TABLES` incrementally using watermark columns (12 when first set up)
- Uses `ON CONFLICT DO NOTHING` for tables with unique constraints
- Also pulls `logs/vps_health.json` from VPS

### Step 9: VPS Health Monitor
- `scripts/check_vps_health.py` — runs on VPS
- Runs every VPS-environment check of the shared registry (`coding/service/health/registry.py`; see section 8)
- Writes structured output to `logs/vps_health.json` and stores each run in the `health_check_runs` table (rows older than 90 days are pruned)
- Cron job runs it every hour: `0 * * * *`

### Step 10: GUI Integration
- **Database tab**: "Sync from VPS" button — runs sync, logs progress
- **System Health tab**: "Check VPS Health" button — reads local `logs/vps_health.json`

---

## 6. What the Daemon Collects

Every 30 minutes, the `ProspectiveCollector` runs for BTC and ETH:

| Step | Data | Table |
|------|------|-------|
| 1 | Recent trades | `historical_trades` |
| 2 | Book summary (all instruments) | `snapshots` |
| 3 | On-chain analysis (max pain, OI, GEX/DEX, volatility, skew) and forward-test predictions | `onchain_analysis_snapshots`, `volatility_skew_history`, `forward_test_predictions` |
| 4 | DVOL (final close of the last 48 hourly candles) | `dvol_history` |
| 5 | Dated-futures basis | `futures_basis` |
| 6 | The owner's Deribit account, read-only (skipped without credentials) | `account_snapshots`, `position_snapshots`, `user_trades`, `orders`, `settlements` |
| 7 | Funding rate | `funding_rate_history` |
| 8 | OHLCV daily candle | `ohlcv_history` |
| 9 | Delta-weighted taker flow | `flow_delta_hourly` |
| 10 | Daily open-interest anchor (08:00 UTC hour only) | `daily_oi_snapshots` |
| after | Hourly aggregation | `hourly_snapshots` |
| after | Volatility reconstruction | `onchain_volatility_snapshots` |
| after | Straddle, iron condor and butterfly scanners | `straddle_scan_history`, `defined_risk_scan_history` |

The tables `max_pain`, `open_interest`, `volume`, `gex_dex`, `levels`, `volatility_index_history` and `buy_sell_flow_metrics` no longer exist (migrations 024, 027, 028).

---

## 7. Synced Tables

The sync script pulls these 20 tables (`SYNC_TABLES` in `scripts/sync_from_vps.py`) from the VPS to the local PC. `health_check_runs` stays on the VPS.

| Table | Watermark Column | Conflict Handling |
|-------|-----------------|-------------------|
| `historical_trades` | `captured_at` | `(trade_id, trade_timestamp)` |
| `snapshots` | `captured_at` | none |
| `hourly_snapshots` | `snapshot_hour` | `(instrument_name, snapshot_hour)` |
| `onchain_analysis_snapshots` | `snapshot_hour` | `(snapshot_hour, currency, expiration)` |
| `flow_delta_hourly` | `snapshot_hour` | `(snapshot_hour, currency, expiration)` |
| `funding_rate_history` | `date` | `(instrument_name, timestamp)` |
| `ohlcv_history` | `date` | `(instrument_name, timestamp)` |
| `onchain_volatility_snapshots` | `reconstructed_at` | `(snapshot_hour, currency, expiration)` |
| `forward_test_predictions` | `created_at` | none |
| `daily_oi_snapshots` | `created_at` | none |
| `account_snapshots` | `snapshot_hour` | `(currency, snapshot_hour)` |
| `position_snapshots` | `snapshot_hour` | `(snapshot_hour, instrument_name)` |
| `user_trades` | `trade_time` | `(trade_id)` |
| `settlements` | `settled_at` | `(currency, settled_at, type, instrument_name)` |
| `orders` | `last_update_at` | none |
| `futures_basis` | `timestamp` | `(futures_instrument, timestamp)` |
| `dvol_history` | `timestamp` | none |
| `volatility_skew_history` | `snapshot_hour` | none |
| `straddle_scan_history` | `scan_time` | none |
| `defined_risk_scan_history` | `scan_time` | none |

---

## 8. Health Checks and Thresholds

The checks live in one registry, `coding/service/health/registry.py`; each is marked LOCAL, VPS or both. `scripts/check_vps_health.py` runs the VPS and both-environment checks on the VPS every hour; `scripts/validate_system.py` and the GUI run the local ones (see `documentation/gui_tabs/tab_system_validation.md` for the list).

How fresh each table must be is set in `coding/service/health/table_registry.py` (hours since the newest row; warn / fail):

| Tables | Warn after | Fail after | Why |
|--------|-----------|-----------|-----|
| Hourly tables (`historical_trades`, `snapshots`, `hourly_snapshots`, `onchain_analysis_snapshots`, `onchain_volatility_snapshots`, `volatility_skew_history`, `flow_delta_hourly`, `straddle_scan_history`, `defined_risk_scan_history`, `forward_test_predictions`, `futures_basis`, `funding_rate_history`, `account_snapshots`) | 2 h | 6 h | Written every collection cycle |
| `dvol_history` | 3 h | 6 h | Only completed hourly candles are stored |
| `ohlcv_history` | 48 h | 120 h | Daily candle |
| `daily_oi_snapshots` | 36 h | 72 h | Written once a day |
| `position_snapshots`, `user_trades`, `orders`, `settlements` | none | none | Event tables: they only change when the owner trades |

`account_snapshots` and the event tables are optional: an empty table is only a warning, because the account collector is off without credentials.

---

## 9. Key Files

| File | Location | Purpose |
|------|----------|---------|
| Daemon entry point | `coding/service/data_collection/collection_daemon.py` | Main daemon |
| Collector | `coding/service/data_collection/prospective_collector.py` | Data collection logic |
| DB config | `coding/core/database/config.py` | Reads from .env |
| Base schema | `migrations/000_base_schema.sql` | All base tables for fresh DB |
| Health check | `scripts/check_vps_health.py` | VPS health monitor |
| Check registry | `coding/service/health/registry.py`, `table_registry.py` | Which checks run, how fresh each table must be |
| Sync script | `scripts/sync_from_vps.py` | VPS → local sync |
| VPS .env | `/home/nick/option_trading/.env` | DB credentials + API token |

---

## 10. VPS .env Contents

As recorded when the VPS was set up; not re-checked against the server.

```env
DB_PASSWORD=DB_PASSWORD_REDACTED
DB_HOST=localhost
DB_PORT=5432
DB_NAME=option_trading
DB_USER=nick
```

Further keys the code reads (set only if the feature is wanted): `client_id` and `deribit_key` (read-only Deribit credentials for the account collector; without them the account tables stay empty) and `OSF_TELEGRAM_BOT_TOKEN` / `OSF_TELEGRAM_CHAT_ID` (scanner alerts).

---

## 11. Daily Operations Checklist

### When you turn on your PC

1. Open the GUI app
2. Go to **Database tab**
3. Click **Sync from VPS**
4. Watch the log — expect "Synced X rows in ~15s"

### To check VPS health

1. Click **Sync from VPS** first (to get latest health JSON)
2. Go to **System Health tab**
3. Click **Check VPS Health**
4. **Expected**: every check passes and the file was written within the past hour

### What healthy output looks like

Every check of the VPS environment passes: daemon service active, API reachable, each table in `table_registry.py` fresh and complete, no missing cycles, scanner and forward tests active. The exact lines depend on the registry; run `python -m scripts.check_vps_health` on the VPS to see them. Each hourly run is also stored in the `health_check_runs` table, so you can look at the history.

### If a check fails

| Problem | Likely cause | Fix |
|---------|-------------|-----|
| systemd service NOT RUNNING | Daemon crashed | `ssh option-server` → `sudo systemctl restart option-trading` |
| API unreachable | Network issue on VPS | Check VPS connectivity |
| Database failed | PostgreSQL crashed | `sudo systemctl restart postgresql` |
| Table STALE (>2h) | Daemon not collecting | Check daemon logs: `journalctl -u option-trading -n 50` |

### To SSH into the VPS

```bash
ssh option-server
```

### To check daemon logs

```bash
ssh option-server
journalctl -u option-trading -n 100 --no-pager
```

### To manually run a health check on VPS

```bash
ssh option-server
cd option_trading && .venv/bin/python -m scripts.check_vps_health
```

### To restart the daemon

```bash
ssh option-server
sudo systemctl restart option-trading
sudo systemctl status option-trading
```
