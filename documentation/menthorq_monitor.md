# MenthorQ-style level monitor: Telegram bot and alerts

The code is the source of truth. This page summarises it; when they disagree, trust the code:

- `coding/service/on_chain/menthorq_monitor/`: the job, delivery, commands and settings
- `coding/core/analytics/menthorq_monitor/`: records, comparisons, alert rules and mute logic (pure)
- `migrations/031_add_menthorq_monitor_tables.sql` and `migrations/032_add_menthorq_monitor_state.sql`

The monitor runs inside the collector (`unified_scheduler`, systemd unit `option-trading` on the VPS).
It has no process of its own. Its jobs, all in UTC:

| Job id | When | What |
|---|---|---|
| `menthorq_monitor_hourly` | every hour at `MENTHORQ_LIVE_RUN_MINUTE` | stores the live record, stores yesterday's daily-close record if it is missing, sends urgent alerts, catches up a missed weekly brief |
| `menthorq_weekly_brief` | `MENTHORQ_WEEKLY_BRIEF_DAY` at `MENTHORQ_WEEKLY_BRIEF_TIME_UTC` | sends the weekly brief once per week |
| `menthorq_command_poll` | every `MENTHORQ_COMMAND_POLL_SECONDS` | polls Telegram for bot commands (getUpdates) |

## Bot commands

Only the owner chat (`OSF_TELEGRAM_CHAT_ID`) can run a command. A command from any other chat is logged
as `REJECTED` and gets no reply. A command older than 10 minutes, for example one sent while the
service was down, is skipped. At its first poll the listener sends the command menu to Telegram
once (`setMyCommands`). If that fails, it logs the failure and the commands still work.

| Command | Reply |
|---|---|
| `/brief` | The full brief: header (with the DATA STALE line when it applies), key levels, gamma regime, changes vs previous run / 24h / 7d (a missing comparison is one "no stored record" line there, not repeated in the caveats), panels with a CR / PS / HVL legend, events, caveats. |
| `/levels` | Call Resistance, HVL and Put Support of the newest stored live record, each with its distance to spot (level minus spot, in USD and in % of spot), plus spot, the regime sentence and total net GEX. |
| `/changes` | The newest live record compared with the previous run, 24h ago and 7d ago: spot, the three levels and net GEX, with expiries that settled in each window. The events list says when a level move coincides with an expiry roll-off. |
| `/status` | Health of the monitor job, the last run of each mode (live, daily_close), data age and the snapshot and open-interest instants, whether the hourly job ran in the last 2h, the alert state (off, muted until, or not muted), the last delivered urgent alert, and the last alert held back by a mute. |
| `/mute <duration>` | `/mute 24h`, `/mute 3d`, `/mute 90m` (units m, h, d; at most 30d) mutes URGENT alerts until now + duration. `/mute off` unmutes. `/mute` with no argument shows the current state. |
| `/help` | The command list and which urgent alert rules are on. |
| anything else | `Unknown command ... Send /help for details.` |

Each reply uses Telegram HTML (only `<b>`, `<i>` and the code block
`<pre><code class="language-text">...</code></pre>`), escapes all dynamic text, and stays
under 4096 UTF-16 units. The code-block markup is one constant, `CODE_BLOCK_OPEN` in
`brief_formatter.py`. A bare `<pre>` block gets Telegram's syntax colors; whether
`language-text` turns them off is not yet checked in a real chat. If it does not, change only that
constant (for example to `<pre><code class="language-plaintext">`). `/changes` cuts its event list when it would be too long. If a reply cannot
be built (for example, the database is down), the bot answers `<command> failed (see the collector log)`.

Health in `/status` is `OK` unless one of these holds: no live run is stored, the newest live run
was computed more than 2h ago, the newest live run was not computed (the stored reason is shown),
or its snapshot is more than 45 min older than the run.

## Mute

- A mute only silences URGENT alerts. Command replies and the weekly brief are always sent.
- The mute is stored as an end instant in `menthorq_monitor_state.alerts_muted_until`
  (migration 032), so it survives a restart and ends on its own when that instant passes.
- While muted, the alerts that would have been sent are not written to `menthorq_alert_deliveries`,
  so their 12h cooldown does not start. Their keys and time are written to
  `menthorq_monitor_state.last_suppressed_*` and logged. An alert whose condition still holds once
  the mute ends is sent at the next hourly run.
- If the state cannot be read (for example, migration 032 is not applied), alerts are treated as
  NOT muted and the error is logged.

## Urgent alert rules

All percentages are % of the latest spot. The current defaults are the owner's choice for the first
test. They were chosen by hand, not fitted by a backtest.

| Rule | Default | Setting |
|---|---|---|
| HVL moved more than X% vs 24h ago | ON, 5 | `MENTHORQ_ALERT_HVL_MOVE_PERCENT` |
| Call Resistance moved more than X% vs 24h ago | ON, 10 | `MENTHORQ_ALERT_CALL_RESISTANCE_MOVE_PERCENT` |
| Put Support moved more than X% vs 24h ago | ON, 5 | `MENTHORQ_ALERT_PUT_SUPPORT_MOVE_PERCENT` |
| Total net GEX changed sign since the previous run | ON | `MENTHORQ_ALERT_NET_GEX_SIGN_CHANGE` (1/0) |
| Spot crossed HVL since the previous run | OFF | `MENTHORQ_ALERT_SPOT_CROSSES_HVL` (1/0) |
| Spot within X% of Call Resistance or Put Support | OFF | `MENTHORQ_ALERT_SPOT_NEAR_WALL_PERCENT` (e.g. 1) |

- "More than" is strict: a move of exactly 5.00% does not fire. "Within" is inclusive: a distance
  of exactly 1.00% fires.
- A percent setting takes a number in (0, 100] or `off`. An invalid value raises an error and the
  monitor does not start (the collector logs it). A typo never silently falls back to the default.
- Cooldown: an alert key delivered less than 12h ago is not sent again. A crossing and a sign flip
  carry their direction in the key.
- An alert over a window in which an expiry settled says that the move coincides with the roll-off.

The brief and `/levels` add one line when the gamma regime (spot vs HVL) and the sign of total net
GEX disagree. These are different measures: spot below HVL (a negative-gamma regime) can go with a
positive total net GEX.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `MENTHORQ_MONITOR_ENABLED` | 1 | hourly computation and storage |
| `MENTHORQ_ALERTS_ENABLED` | 1 | urgent alerts and the weekly brief |
| `MENTHORQ_COMMANDS_ENABLED` | 1 | bot commands (getUpdates polling) |
| `MENTHORQ_EXPIRY_UNIVERSE` | monthly | all, monthly, quarterly_plus_front_month |
| `MENTHORQ_LIVE_RUN_MINUTE` | 10 | minute past each hour of the live run |
| `MENTHORQ_WEEKLY_BRIEF_DAY` | fri | mon to sun |
| `MENTHORQ_WEEKLY_BRIEF_TIME_UTC` | 09:00 | HH:MM |
| `MENTHORQ_COMMAND_POLL_SECONDS` | 20 | at least 5 |
| `MENTHORQ_STALE_SNAPSHOT_MINUTES` | 120 | at least 10; stale-data limit, see below |
| `MENTHORQ_ALERT_*` | see the rules table | alert rules |

An invalid value of any of these variables (for example `MENTHORQ_STALE_SNAPSHOT_MINUTES=9`) stops
only the MenthorQ monitor from starting; the collector logs the error and runs on.

### The DATA STALE line

The brief, `/levels`, `/changes` and the GUI preview start (right under the title) with one line
when the data behind the record is older than `MENTHORQ_STALE_SNAPSHOT_MINUTES` before the record's
data time, for example:

`DATA STALE: snapshot is 12 h 41 min older than the data time (spot trade 12 h 31 min); levels may be out of date.`

- Snapshot older than the limit: the levels come from an old option chain. The spot-trade age is
  added in brackets when it is also over the limit (one line, not two).
- Only the spot trade older than the limit: `spot comes from a trade ... before the data time;
  distances to spot may be out of date.`
- Exactly the limit is not stale; one minute more is. Durations are whole minutes, rounded down.
- A spot trade more than 15 min but not more than the limit before the data time is a normal
  caveat line instead. `/status` keeps its own 45 min snapshot check for its health line.

The usual cause is a collector that stopped writing snapshots (or a local database that was not
synced from the VPS), so the newest snapshot is hours behind the time the brief was asked for.

The bot token and the owner chat are the scanner's `OSF_TELEGRAM_BOT_TOKEN` and `OSF_TELEGRAM_CHAT_ID`.
Only one process can poll a bot token. If another process polls the same token, Telegram answers 409,
and that is logged.

## Deployment of the bot commands (migration 032)

Migration 032 depends on 031 being applied first. Both are idempotent (`IF NOT EXISTS`).

1. Locally: `python scripts/run_migration.py migrations/032_add_menthorq_monitor_state.sql`
   (031 first if it is missing). Then run the monitor tests and
   `.venv\Scripts\python -m scripts.validate_system`.
2. On the VPS, as a separate step and only when asked: pull the code, then run
   `python scripts/run_migration.py migrations/031_add_menthorq_monitor_tables.sql` (if not yet
   applied) and `python scripts/run_migration.py migrations/032_add_menthorq_monitor_state.sql`.
3. Set any `MENTHORQ_ALERT_*` overrides in the VPS `.env`. Without them the defaults above apply.
4. Restart the collector: `sudo systemctl restart option-trading`. The log should show
   `MenthorQ monitor scheduled`, `MenthorQ bot command polling every 20 s` and, at the first poll,
   `command menu registered (6 commands)`.
5. In Telegram, send `/help` and then `/status`.

`menthorq_monitor_state` is per-deployment alert state and is not in `scripts/sync_from_vps.py`.
