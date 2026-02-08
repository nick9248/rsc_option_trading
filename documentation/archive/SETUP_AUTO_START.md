# Auto-Start Collection Daemon Setup Guide

## Overview

This guide sets up the data collection daemon to **start automatically when your PC boots**, regardless of what time you turn it on.

**Features**:
- ✅ Starts on any boot time (11 AM, 1 PM, 9 AM, doesn't matter!)
- ✅ Collects every 30 minutes while system is running
- ✅ Handles gaps intelligently (< 1.5h can be backfilled)
- ✅ Stops gracefully on shutdown
- ✅ Logs all activity

---

## Method 1: Windows Task Scheduler (RECOMMENDED)

### Step 1: Create Scheduled Task

1. **Open Task Scheduler**:
   - Press `Win + R`
   - Type `taskschd.msc`
   - Press Enter

2. **Create New Task**:
   - Click "Create Task" (NOT "Create Basic Task")
   - Name: `Deribit Data Collection Daemon`
   - Description: `Auto-start data collection on system boot`

3. **General Tab**:
   - ✅ Check "Run whether user is logged on or not"
   - ✅ Check "Run with highest privileges"
   - ✅ Check "Hidden" (optional - hides console window)
   - Configure for: `Windows 10`

4. **Triggers Tab**:
   - Click "New..."
   - Begin the task: `At startup`
   - Delay task for: `1 minute` (wait for services)
   - ✅ Check "Enabled"
   - Click OK

5. **Actions Tab**:
   - Click "New..."
   - Action: `Start a program`
   - Program/script: `C:\Users\Nick\PycharmProjects\option_trading\scripts\start_collection_daemon.bat`
   - Start in: `C:\Users\Nick\PycharmProjects\option_trading`
   - Click OK

6. **Conditions Tab**:
   - ✅ UNcheck "Start the task only if the computer is on AC power"
   - ✅ Check "Start only if the following network connection is available: Any connection"

7. **Settings Tab**:
   - ✅ Check "Allow task to be run on demand"
   - ✅ Check "Run task as soon as possible after a scheduled start is missed"
   - ✅ Check "If the running task does not end when requested, force it to stop"
   - If the task fails, restart every: `5 minutes`
   - Attempt to restart up to: `3 times`

8. **Save**:
   - Click OK
   - Enter your Windows password when prompted

---

### Step 2: Test the Scheduled Task

**Option A: Test by Running Manually**:
1. In Task Scheduler, find your task
2. Right-click → "Run"
3. Check that daemon starts (console window or check logs)

**Option B: Test by Rebooting**:
1. Restart your PC
2. After boot, check Task Scheduler → "Task Scheduler Library"
3. Your task should show "Running"
4. Check log file: `output/log/collection_daemon.log`

---

### Step 3: Verify It's Working

**Check Logs**:
```bash
# View log file
type output\log\collection_daemon.log

# Or tail last 50 lines
powershell -Command "Get-Content output\log\collection_daemon.log -Tail 50"
```

**Check Database** (when PostgreSQL is running):
```sql
-- Check collection logs
SELECT * FROM collection_logs ORDER BY run_timestamp DESC LIMIT 10;

-- Check last collection
SELECT MAX(captured_at) FROM historical_trades;
```

---

## Method 2: Windows Startup Folder (ALTERNATIVE - Simpler)

This method is simpler but less robust (only starts after user login).

### Step 1: Create Shortcut

1. **Navigate to**:
   ```
   C:\Users\Nick\PycharmProjects\option_trading\scripts
   ```

2. **Right-click `start_collection_daemon.bat`**:
   - Send to → Desktop (create shortcut)

3. **Move shortcut to Startup folder**:
   - Press `Win + R`
   - Type: `shell:startup`
   - Press Enter
   - Move the shortcut here

**Done!** Daemon will start when you log in.

**Limitations**:
- Only starts after user login (not on system boot)
- Visible console window (unless minimized)
- Less robust than Task Scheduler

---

## Method 3: Windows Service (ADVANCED - Most Robust)

For 24/7 production deployment, convert to Windows Service using NSSM (Non-Sucking Service Manager).

### Step 1: Install NSSM

1. Download NSSM: https://nssm.cc/download
2. Extract `nssm.exe` to `C:\nssm\`

### Step 2: Install Service

Open PowerShell as Administrator:

```powershell
cd C:\nssm

# Install service
.\nssm.exe install DeribitCollector "C:\Users\Nick\PycharmProjects\option_trading\.venv\Scripts\python.exe" "-m coding.service.data_collection.collection_daemon"

# Set working directory
.\nssm.exe set DeribitCollector AppDirectory "C:\Users\Nick\PycharmProjects\option_trading"

# Set log files
.\nssm.exe set DeribitCollector AppStdout "C:\Users\Nick\PycharmProjects\option_trading\output\log\daemon_stdout.log"
.\nssm.exe set DeribitCollector AppStderr "C:\Users\Nick\PycharmProjects\option_trading\output\log\daemon_stderr.log"

# Set auto-restart
.\nssm.exe set DeribitCollector AppExit Default Restart

# Start service
.\nssm.exe start DeribitCollector
```

### Manage Service:

```powershell
# Check status
.\nssm.exe status DeribitCollector

# Stop service
.\nssm.exe stop DeribitCollector

# Restart service
.\nssm.exe restart DeribitCollector

# Remove service
.\nssm.exe remove DeribitCollector confirm
```

---

## Adaptive Behavior - How It Works

### Scenario 1: Boot at 11 AM (Your Normal Time)
- ✅ Daemon starts at 11:00 AM
- ✅ Checks for gaps (finds overnight gap, logs it)
- ✅ Runs first collection immediately
- ✅ Schedules next at 11:30 AM, then 12:00 PM, etc.

### Scenario 2: Boot at 1 PM (Late Start)
- ✅ Daemon starts at 1:00 PM
- ✅ Checks for gaps (finds 14-hour gap since last night)
- ✅ Logs gap (cannot backfill - too old)
- ✅ Runs first collection immediately
- ✅ Schedules next at 1:30 PM, then 2:00 PM, etc.

### Scenario 3: Boot at 9 AM (Early Start)
- ✅ Daemon starts at 9:00 AM
- ✅ Checks for gaps (finds overnight gap)
- ✅ Runs first collection immediately
- ✅ Schedules next at 9:30 AM, 10:00 AM, etc.

### Scenario 4: Crash/Restart Within 1.5 Hours
- ✅ Daemon starts after crash
- ✅ Checks for gaps (finds 45-minute gap)
- ✅ **Backfills gap!** (within API window)
- ✅ Continues normal schedule

### Scenario 5: Shutdown at 11 PM
- ✅ Daemon receives shutdown signal
- ✅ Stops scheduler gracefully
- ✅ Logs final stats
- ✅ Exits cleanly

**Result**: Works with ANY boot time, ANY shutdown time, ANY crash/restart!

---

## Monitoring & Troubleshooting

### Check If Daemon Is Running

**Option 1: Task Manager**:
- Press `Ctrl + Shift + Esc`
- Look for `python.exe` running `collection_daemon`

**Option 2: Command Line**:
```powershell
# Find python processes
tasklist /FI "IMAGENAME eq python.exe"

# Or use PowerShell
Get-Process python | Where-Object {$_.CommandLine -like "*collection_daemon*"}
```

### View Logs in Real-Time

```powershell
# Tail log file (PowerShell)
Get-Content output\log\collection_daemon.log -Wait -Tail 20

# Or open in notepad
notepad output\log\collection_daemon.log
```

### Stop Daemon Manually

**Option 1: Graceful Stop** (RECOMMENDED):
- Press `Ctrl + C` in daemon console window
- Daemon shuts down gracefully

**Option 2: Task Scheduler**:
- Open Task Scheduler
- Right-click task → "End"

**Option 3: Kill Process**:
```powershell
# Find PID
tasklist | findstr python

# Kill by PID
taskkill /PID <pid> /F
```

---

## Testing Schedule

### Test 1: Manual Run
```bash
# Activate venv
.venv\Scripts\activate

# Run daemon manually
python -m coding.service.data_collection.collection_daemon
```

**Expected**:
- Daemon starts
- Runs initial collection
- Schedules recurring collections
- Prints status every 5 minutes
- Press Ctrl+C to stop

### Test 2: Task Scheduler Run
1. Open Task Scheduler
2. Find your task
3. Right-click → "Run"
4. Check logs

### Test 3: Actual Reboot
1. Restart PC
2. Wait 1-2 minutes after login
3. Check if daemon is running
4. Check logs

---

## Configuration Options

Edit `collection_daemon.py` to customize:

```python
# Collection interval (default: 30 minutes)
daemon = CollectionDaemon(
    collection_interval_minutes=30,  # Change to 15, 60, etc.
    currencies=["BTC", "ETH"]  # Add more currencies
)
```

**Common intervals**:
- `15` minutes: Higher granularity (48 collections/day)
- `30` minutes: Recommended (24 collections/day)
- `60` minutes: Lower granularity (12 collections/day)

---

## Next Steps

1. ✅ **Install Dependencies**:
   ```bash
   pip install apscheduler
   ```

2. ✅ **Setup Task Scheduler** (Method 1 recommended)

3. ✅ **Test Manual Run** first

4. ✅ **Test Scheduled Run**

5. ✅ **Test Reboot**

6. ✅ **Monitor for 1 day** (check logs, database)

7. ✅ **Let it run!** (Data accumulates automatically)

---

## FAQ

**Q: What if I turn on my PC at 1 PM instead of 11 AM?**
A: No problem! Daemon starts at 1 PM, runs first collection immediately, then every 30 minutes until shutdown.

**Q: Can it fill the overnight gap?**
A: No, overnight gaps (11 PM - 11 AM) are too large (API only provides 1.5h lookback). But that's OK - 12h/day is sufficient!

**Q: What if my PC crashes?**
A: On restart, if gap < 1.5h, it will backfill. If gap > 1.5h, it logs the gap and continues.

**Q: How do I stop it?**
A: Press Ctrl+C in console, or stop via Task Scheduler, or disable the scheduled task.

**Q: Can I run it 24/7?**
A: Yes! Just leave your PC on, or use a cheap cloud VM ($5/month).

**Q: How much disk space does it use?**
A: Logs rotate, database grows ~100MB per month (manageable).

---

## Summary

**What you get**:
- ✅ Adaptive auto-start (works with any boot time)
- ✅ Continuous collection (every 30 min while running)
- ✅ Intelligent gap handling (< 1.5h backfilled)
- ✅ Graceful shutdown
- ✅ Comprehensive logging
- ✅ Self-healing (restarts on crash)

**Effort**: 15 minutes to setup, then runs forever automatically!

**Data quality**: 12h/day × 90 days = 1,080 hours = **EXCELLENT** for ML training!
