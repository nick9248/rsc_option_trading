# MenthorQ-style level monitor: Telegram bot and alerts

The code is the source of truth. This page summarises it; when they disagree, trust the code:

- `coding/service/on_chain/menthorq_monitor/`: the job, delivery, commands and settings
- `coding/core/analytics/menthorq_monitor/`: records, comparisons, alert rules and mute logic (pure)
- `migrations/031_add_menthorq_monitor_tables.sql` and `migrations/032_add_menthorq_monitor_state.sql`

The monitor runs inside the collector (`unified_scheduler`, systemd unit `option-trading` on the VPS).
It has no process of its own, and it runs only with `MENTHORQ_MONITOR_ENABLED=1` (off by default,
set on the VPS only). Its jobs, all in UTC:

| Job id | When | What |
|---|---|---|
| `menthorq_monitor_hourly` | every hour at `MENTHORQ_LIVE_RUN_MINUTE` | stores the live record, stores every missing or not-computed daily-close record of the last 7 ended UTC days (oldest first; a stored not-computed row (no Call Resistance, Put Support, HVL or total net GEX) is retried only when a snapshot newer than the one it was last tried with exists at or before its 23:59 UTC (one MAX query, no chain load; a row without a snapshot time is retried once per collector start; a skip is logged at INFO), and a computed result replaces it; a row with levels is never overwritten; a day that fails is logged and the others still run; today is never computed; like the backtest, a daily close is stored as not computed when its snapshot or index trade is more than 60 min older than 23:59 UTC, or its open-interest snapshot more than 60 min older than 00:00 UTC), sends urgent alerts and catches up a missed weekly brief (both only with `MENTHORQ_ALERTS_ENABLED=1`) |
| `menthorq_weekly_brief` | `MENTHORQ_WEEKLY_BRIEF_DAY` at `MENTHORQ_WEEKLY_BRIEF_TIME_UTC` | sends the weekly brief once per week; registered only with `MENTHORQ_ALERTS_ENABLED=1` |
| `menthorq_command_poll` | every `MENTHORQ_COMMAND_POLL_SECONDS` | polls Telegram for bot commands (getUpdates); registered only with `MENTHORQ_COMMANDS_ENABLED=1` |

## Bot commands

Only the owner can run a command: the message must come from the owner chat (`OSF_TELEGRAM_CHAT_ID`)
and its sender (`from.id`) must be the owner user. For a private chat (positive chat id) the owner user
id is the chat id itself, so nothing else is needed. For a group or supergroup (negative chat id) every
member writes into the same chat, so `MENTHORQ_OWNER_USER_ID` (the owner's Telegram user id) is
required; without it the collector logs one warning at start-up and does not schedule command polling.
A command from any other chat or any other sender is logged as `REJECTED` and gets no reply; a message
without a sender is ignored. A command older than 10 minutes is skipped. At its first poll the
listener sends the command menu to Telegram (`setMyCommands`). If that fails, it logs the
failure (a warning the first time, debug after), the commands still work, and the next poll tries
again until Telegram accepts it once.

A command addressed to a bot (`/mute@SomeBot 24h`) runs only when that bot is this one (its
username from `getMe`, asked at the first poll and cached; compared without case). A command for
another bot is ignored (logged at DEBUG). If `getMe` fails, addressed commands are ignored, plain
ones (`/mute 24h`) still run, and `getMe` is asked again at the next poll.

At its first poll after each start the listener also skips everything Telegram still holds
(`getUpdates` with offset -1, no wait: Telegram returns only the newest update and forgets the older
ones; the listener continues after it and logs `skipped the pending updates up to update_id N`).
So a restart never runs a command a second time. The cost: commands sent while the collector was
down (or restarting) are lost, not run late. Send them again. When the newest skipped update is an
owner command that would have run (owner chat and user, at most 10 min old), the bot sends one
notice: `Monitor restarted. The command /xyz sent while it was restarting was skipped; please send
it again.` (only the command word, at most 20 characters, no arguments). Nothing is sent when no
owner command was pending; a failed notice is only logged. Only the newest update is visible, so
an older command followed by plain text gets no notice, and a command handled just before the
shutdown but not yet confirmed to Telegram can get one. If that first call fails, nothing is run
and it is retried at the next poll.

With `MENTHORQ_COMMANDS_ENABLED=1` but no `OSF_TELEGRAM_CHAT_ID`, the collector logs one warning at
start-up and does not schedule command polling.

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
or its snapshot is more than the stale limit (`MENTHORQ_STALE_SNAPSHOT_MINUTES`, default 120 min,
the same limit as the brief's "DATA STALE" line) older than the run.

## Mute

- A mute only silences URGENT alerts. Command replies and the weekly brief are always sent.
- The mute is stored as an end instant in `menthorq_monitor_state.alerts_muted_until`
  (migration 032), so it survives a restart and ends on its own when that instant passes.
- While muted, the alerts that would have been sent are not written to `menthorq_alert_deliveries`,
  so their 12h cooldown does not start. Their keys and time are written to
  `menthorq_monitor_state.last_suppressed_*` and logged.
- Muted or failed EVENT alerts (net GEX sign flip, spot crossing HVL) are re-evaluated every run
  until they are delivered or the state reverts. Each event family is compared with the state last
  REPORTED to the owner, read from `menthorq_alert_deliveries` (`net_gex_turned_negative` /
  `_positive` -> negative / positive net GEX; `spot_crossed_below_hvl` / `_above_hvl` -> negative /
  positive gamma regime). While the current state differs from the reported one, the alert for the
  current state fires (cooldown, mute and stale rules as usual); when it was not a change since the
  previous run, its line says `(earlier, not reported yet; last reported: ...)`. A flip that flips
  back before it was reported sends nothing. The 24h level-move alerts (and spot near a wall, data stale) are
  re-evaluated every run too: one whose condition still holds after the mute ends, or after a
  failed send, is sent at the next hourly run. The current state is always in `/brief` and
  `/levels`. The GUI preview and the dry run read no delivery log and use the previous-run rule.
- A family with no delivered event yet (the first-ever net GEX sign flip, the first-ever HVL
  crossing) is compared with the state about 24h ago instead: the state of the stored record the
  24h comparison uses (the newest one at or before 24h ago, at most 30h old) or, if none is that
  old, of the oldest stored record within the last 24h. A record whose state is unknown (HVL not
  computed, for example at the chain edge, spot exactly at HVL, net GEX missing or 0) is skipped,
  for the baseline and for the current state, so it cannot hide a crossing. So a first event that
  was muted or whose send failed is sent later while the state still differs from that baseline
  (the line says `(earlier, not reported yet; about 24h ago: ...)`), and a flip that flips back
  first sends nothing. Remaining limit: when no stored record older than the latest exists (for
  example the first hour of a brand-new deployment), the trigger is the change since the previous
  run.
- The four event keys (`net_gex_turned_negative`/`_positive`, `spot_crossed_below_hvl`/`_above_hvl`)
  use a cooldown of at most 2h (`EVENT_ALERT_COOLDOWN`; the configured cooldown if that is shorter).
  A flip back within 2h of the alert for the same direction is held on purpose: it keeps a state
  that flaps around zero or around HVL from sending an alert every hour. After 2h it is sent, so
  the owner's last message matches the current state (negative at 09:10, positive at 11:10,
  negative again at 13:10: all three are sent; with the old 12h cooldown the third was dropped).
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
| Total net GEX changed sign (vs the last reported sign, see Mute) | ON | `MENTHORQ_ALERT_NET_GEX_SIGN_CHANGE` (1/0) |
| Spot crossed HVL (vs the last reported regime, see Mute) | OFF | `MENTHORQ_ALERT_SPOT_CROSSES_HVL` (1/0) |
| Spot within X% of Call Resistance or Put Support | OFF | `MENTHORQ_ALERT_SPOT_NEAR_WALL_PERCENT` (e.g. 1) |

- "More than" is strict: a move of exactly 5.00% does not fire. "Within" is inclusive: a distance
  of exactly 1.00% fires.
- A percent setting takes a number in (0, 100] or `off`. An invalid value raises an error and the
  monitor does not start (the collector logs it). A typo never silently falls back to the default.
- Cooldown: an alert key delivered less than 12h ago is not sent again. A crossing and a sign flip
  carry their direction in the key and use at most 2h (see Mute). The three "vs 24h ago" move alerts use at least 24h (the
  comparison window): one move stays visible in that comparison for 24h, so a 12h cooldown would
  report it twice. Delivered 23h59 ago: held; 24h ago: may repeat.
- If Telegram accepted a message but its `menthorq_alert_deliveries` row cannot be written, the
  error is logged (`delivered by Telegram but the delivery row was NOT saved`) and the delivery is
  kept in memory: the cooldown and the once-per-week rule still see it, so it is not resent while
  the collector runs. A restart forgets it (no row exists), so it may then be sent once more.
- Stale data: when the latest live record is stale by the DATA STALE rule below (snapshot older
  than `MENTHORQ_STALE_SNAPSHOT_MINUTES` before its data time or before now), the movement rules
  are not evaluated (their moves would be spurious). One `data_stale` alert is sent instead
  (which snapshot, how old, "movement alerts are paused until fresh data arrives"), with the same
  12h cooldown and `/mute`, and a warning is logged. `/status` lists it as the last alert. The GUI
  preview uses the same rule (one shared function, `evaluate_urgent_alerts_of_record`): on a stale
  record no rule row says it would fire (each value is marked `paused: data stale`) and the urgent
  message shown is the `data_stale` one.
- Not computed: a latest live record without levels (no Call Resistance, Put Support, HVL or total
  net GEX, for example no snapshot at all) is treated like stale data: the same `data_stale` alert,
  cooldown and `/mute`, with the stored reasons in the message (`Monitor has no usable data:
  <reasons>`, at most three, then how many more), and the movement alerts are paused. Losing every
  input is therefore never silent. The GUI preview and the dry-run script show the same alert.
- Monitor error: when the hourly computation itself raises (the live record, or the daily close
  of yesterday; for example a database error), one `monitor_error` alert is sent: `Monitor
  error: the hourly computation failed: <exception class> (details: see the collector log).
  Levels and alerts are paused until it succeeds.` The exception's message is NOT sent (a
  database error can name the host, user and database); only an invalid MENTHORQ_* setting
  (`MonitorSettingsError`) is sent with its own message, cut to 200 characters. The same rule
  applies to the start-up "monitor NOT started" notice. The log keeps the full traceback, with the
  bot token and the database password replaced. Same 12h cooldown
  and `/mute`. When the delivery log cannot be read, only the deliveries this process remembers
  count for the cooldown, so a database that stays down gives one alert per 12h.
- An alert over a window in which an expiry settled says that the move coincides with the roll-off.

The brief and `/levels` add one line when the gamma regime (spot vs HVL) and the sign of total net
GEX disagree. These are different measures: spot below HVL (a negative-gamma regime) can go with a
positive total net GEX.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `MENTHORQ_MONITOR_ENABLED` | 0 | hourly computation and storage (opt-in, VPS only) |
| `MENTHORQ_ALERTS_ENABLED` | 0 | urgent alerts and the weekly brief (opt-in, VPS only) |
| `MENTHORQ_COMMANDS_ENABLED` | 0 | bot commands (getUpdates polling; opt-in, VPS only) |
| `MENTHORQ_EXPIRY_UNIVERSE` | monthly | all, monthly, quarterly_plus_front_month |
| `MENTHORQ_LIVE_RUN_MINUTE` | 10 | minute past each hour of the live run |
| `MENTHORQ_WEEKLY_BRIEF_DAY` | fri | mon to sun |
| `MENTHORQ_WEEKLY_BRIEF_TIME_UTC` | 09:00 | HH:MM, UTC (a value with an offset is refused) |
| `MENTHORQ_COMMAND_POLL_SECONDS` | 20 | at least 12 (the 10 s long-poll wait + 2 s) |
| `MENTHORQ_STALE_SNAPSHOT_MINUTES` | 120 | at least 10; stale-data limit, see below |
| `MENTHORQ_OWNER_USER_ID` | unset | owner's Telegram user id (positive integer); required only when `OSF_TELEGRAM_CHAT_ID` is a group chat (negative id) |
| `MENTHORQ_ALERT_*` | see the rules table | alert rules |

All three switches are off by default, so a local debug run of the collector neither writes
`menthorq_level_snapshots` rows into the local database (a read-only mirror that
`scripts/sync_from_vps.py` fills, with `MAX(computed_at)` as its watermark: a local row would hide
VPS rows) nor becomes a second consumer of the production bot (409 conflicts, stolen updates,
duplicate alerts).

- With `MENTHORQ_MONITOR_ENABLED` off, the collector logs one INFO line at start-up
  (`MenthorQ monitor OFF: ... (set MENTHORQ_MONITOR_ENABLED=1, production collector only)`) and
  schedules no monitor job.
- With the monitor on but either Telegram switch off, it logs one INFO line (`MenthorQ monitor:
  Telegram OFF for ...`) naming the variable to set; the records are still computed and stored.
- The dry-run CLI and the GUI preview do not read `MENTHORQ_MONITOR_ENABLED`: they compute from
  the snapshots, store nothing and never send anything. The preview's alert rule table says "yes" in
  its "enabled" column only when `MENTHORQ_MONITOR_ENABLED` and `MENTHORQ_ALERTS_ENABLED` are both
  1 and the rule is on; with the defaults every row says "no". The threshold column still shows
  which rules are on, and "would fire" is computed either way (what the VPS would send).

An invalid value of any of these variables (for example `MENTHORQ_STALE_SNAPSHOT_MINUTES=9`) stops
only the MenthorQ monitor from starting; the collector logs the error and runs on.

With `MENTHORQ_MONITOR_ENABLED=1` the collector also checks at start-up (one `to_regclass` query)
that `menthorq_level_snapshots`, `menthorq_alert_deliveries` (migration 031) and
`menthorq_monitor_state` (migration 032) exist. When one is missing, the monitor is NOT started:
the collector logs an ERROR and sends one Telegram notice naming the missing table(s) and the
migration numbers, and runs on without the monitor jobs. If the check itself fails, that is
logged and the monitor starts anyway.

### The DATA STALE line

The brief, `/levels`, `/changes` and the GUI preview start (right under the title) with one line
when the data behind the record is older than `MENTHORQ_STALE_SNAPSHOT_MINUTES` before the record's
data time, for example:

`DATA STALE: snapshot is 12 h 41 min older than the data time (spot trade 12 h 31 min); levels may be out of date.`

- Snapshot older than the limit: the levels come from an old option chain. The spot-trade age is
  added in brackets when it is also over the limit (one line, not two).
- Only the spot trade older than the limit: `spot comes from a trade ... before the data time;
  distances to spot may be out of date.`
- The brief, the weekly brief, `/levels` and `/changes` show a STORED record, so its snapshot is
  also compared with the clock at the time of the reply. A record stored days ago (collector
  down) is stale even though it was fresh when computed:
  `DATA STALE: snapshot is 3 d 0 h 20 min older than now (2026-11-09 09:10 UTC); levels may be out of date.`
  Still one line; it shows the larger of the two snapshot ages. The GUI preview and the dry run
  recompute the record at the chosen instant, so their check against the data time is already
  the check against "now" for a live run (and a past daily close is not called stale for being past).
- Exactly the limit is not stale; one minute more is. Durations are whole minutes, rounded down.
- A spot trade more than 15 min but not more than the limit before the data time is a normal
  caveat line instead. `/status` uses the same limit for its health line (snapshot vs the latest run).

The usual cause is a collector that stopped writing snapshots (or a local database that was not
synced from the VPS), so the newest snapshot is hours behind the time the brief was asked for.

The bot token and the owner chat are `OSF_TELEGRAM_BOT_TOKEN` and `OSF_TELEGRAM_CHAT_ID`, read by
`TelegramAlertService` (coding/service/notifications/telegram_alert_service.py).
Only one process can poll a bot token. If another process polls the same token, Telegram answers 409,
and that is logged.

## Deployment of the bot commands (migration 032)

Migration 032 depends on 031 being applied first. Both are idempotent (`IF NOT EXISTS`).
Apply 031 and 032 BEFORE switching `MENTHORQ_MONITOR_ENABLED` on (steps 1 and 2 before step 3):
an enabled monitor whose tables are missing is not started (see above).

1. Locally: `python scripts/run_migration.py migrations/032_add_menthorq_monitor_state.sql`
   (031 first if it is missing). Then run the monitor tests and
   `.venv\Scripts\python -m scripts.validate_system`.
2. On the VPS, as a separate step and only when asked: pull the code, then run
   `python scripts/run_migration.py migrations/031_add_menthorq_monitor_tables.sql` (if not yet
   applied) and `python scripts/run_migration.py migrations/032_add_menthorq_monitor_state.sql`.
3. In the VPS `.env`, set all three switches:
   `MENTHORQ_MONITOR_ENABLED=1`, `MENTHORQ_ALERTS_ENABLED=1` and `MENTHORQ_COMMANDS_ENABLED=1`.
   All default to 0: without the first the VPS computes and stores no record (and runs no
   monitor job at all); without the other two it sends no alert, no weekly brief and answers no
   command. Set any `MENTHORQ_ALERT_*` overrides there too. Without them the defaults above apply.
4. Restart the collector: `sudo systemctl restart option-trading`. The log should show
   `MenthorQ monitor scheduled: hourly at :10 UTC, weekly brief fri 09:00 UTC`,
   `MenthorQ bot command polling every 20 s` and, at the first poll,
   `command menu registered (6 commands)`. It must NOT show `MenthorQ monitor OFF` or
   `Telegram OFF`.
5. In Telegram, send `/help` and then `/status`.

`menthorq_monitor_state` is per-deployment alert state and is not in `scripts/sync_from_vps.py`.
