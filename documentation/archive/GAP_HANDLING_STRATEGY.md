# Gap Handling Strategy for 12-Hour Daily Downtime

## Problem Statement

- **System uptime**: 11 AM - 11 PM (12 hours/day)
- **System downtime**: 11 PM - 11 AM (12 hours/day)
- **API lookback**: Only 1.5 hours (cannot fill 12-hour gaps)

---

## Solution: Smart Collection Strategy

### **Option 1: Accept 50% Data Coverage (SIMPLEST)**

**Strategy**:
- Run hourly collection from 11 AM - 11 PM (12 hours)
- Accept 12-hour daily gaps (11 PM - 11 AM missed)
- Focus on high-quality data during trading hours

**Data Collection**:
- 12 hours/day × 90 days = **1,080 hours of data**
- 12 hours/day × 180 days = **2,160 hours of data**

**Pros**:
- ✅ Simple implementation
- ✅ High-quality data (trading hours are most active)
- ✅ No complexity in gap handling
- ✅ 1,080 hours is sufficient for ML training

**Cons**:
- ❌ Miss overnight volatility events
- ❌ Miss weekend trading patterns
- ❌ 50% data coverage

**Verdict**: **ACCEPTABLE** - 1,080 hours is more than enough for ML training. Many quant firms only use trading hours anyway.

---

### **Option 2: Run Frequently During Uptime (BETTER)**

**Strategy**:
- Run collection **every 30 minutes** (instead of hourly) from 11 AM - 11 PM
- Increases data density during uptime
- Better captures intraday volatility

**Data Collection**:
- 24 collections/day × 90 days = **2,160 collections**
- Higher granularity = better feature engineering

**Implementation**:
```python
# Scheduler config
schedule.every(30).minutes.do(collect_data)

# OR APScheduler
scheduler.add_job(
    collect_data,
    'interval',
    minutes=30,
    start_date='2026-02-02 11:00:00',
    end_date='2026-02-02 23:00:00'  # Daily window
)
```

**Pros**:
- ✅ Better intraday granularity
- ✅ More robust to collection failures
- ✅ Captures rapid volatility changes
- ✅ Still simple implementation

**Cons**:
- ❌ Still miss overnight (but that's acceptable)

**Verdict**: **RECOMMENDED** - Best balance of data quality and simplicity

---

### **Option 3: Cloud VM for 24/7 Collection (BEST, but costs)**

**Strategy**:
- Deploy collection script to cheap cloud VM (AWS EC2 t2.micro, ~$5/month)
- Run 24/7 automatically
- No gaps, full data coverage

**Data Collection**:
- 24 hours/day × 90 days = **2,160 hours** (full coverage)
- No missed overnight volatility

**Implementation**:
- Docker container on AWS ECS / DigitalOcean / Hetzner
- Systemd service with auto-restart
- PostgreSQL in cloud or connect to local via VPN

**Pros**:
- ✅ Full 24/7 data coverage
- ✅ No manual intervention needed
- ✅ Captures all market events
- ✅ Professional setup

**Cons**:
- ❌ Costs ~$5-10/month (VM + database)
- ❌ Setup complexity (Docker, deployment)
- ❌ Need cloud database or VPN to local

**Verdict**: **BEST** if budget allows, but not strictly necessary

---

### **Option 4: Hybrid - Scheduled PC Wake (CLEVER)**

**Strategy**:
- Use Windows Task Scheduler to **wake PC from sleep** at intervals
- Run collection, then sleep again
- Reduces power consumption while maintaining coverage

**Implementation**:
```
Windows Task Scheduler:
- Wake PC at: 3 AM, 7 AM (during downtime)
- Run collection script
- Sleep after 10 minutes

Result: 4-6 extra collections during "downtime"
```

**Pros**:
- ✅ Better coverage without 24/7 runtime
- ✅ Low power consumption
- ✅ No cloud costs
- ✅ Captures some overnight data

**Cons**:
- ❌ PC must support wake timers
- ❌ Not fully continuous
- ❌ More complex setup

**Verdict**: **CLEVER** middle ground

---

## 📊 **Data Sufficiency Analysis**

### How much data do we ACTUALLY need?

**ML Training Requirements**:
- **Minimum**: 500-1000 samples for prototyping
- **Good**: 2,000-5,000 samples for decent model
- **Optimal**: 10,000+ samples for production model

**Our Options**:
| Strategy | Days | Hours/Day | Total Hours | Sufficient? |
|----------|------|-----------|-------------|-------------|
| Option 1 (12h/day) | 90 | 12 | 1,080 | ✅ YES |
| Option 1 (12h/day) | 180 | 12 | 2,160 | ✅ EXCELLENT |
| Option 2 (30min intervals) | 90 | 12 | 2,160 collections | ✅ EXCELLENT |
| Option 3 (24/7 cloud) | 90 | 24 | 2,160 | ✅ EXCELLENT |

**Verdict**: Even **Option 1 (simplest)** provides sufficient data!

---

## 🎯 **RECOMMENDATION**

### **Start with Option 2: 30-Minute Collections During Uptime**

**Why**:
1. ✅ **Simple** - No cloud costs, no complex setup
2. ✅ **Sufficient** - 2,160 collections in 90 days is MORE than enough
3. ✅ **Quality** - Trading hours (11 AM - 11 PM) have most volatility anyway
4. ✅ **Robust** - If one collection fails, next one is 30 min away

**Implementation Plan**:
1. **Week 1**: Run collection every 30 minutes (11 AM - 11 PM)
2. **Week 2-12**: Continue 30-minute collections
3. **After 12 weeks**: Have 2,160+ data points (enough to train!)

**Alternative**: If data looks sparse after 2 weeks, upgrade to **Option 3 (cloud VM)** for full 24/7 coverage.

---

## 🛠️ **Implementation: Startup Gap Handling**

Even though we can't backfill 12 hours, we should handle **short gaps** intelligently:

### On System Startup (11 AM):

```python
def collect_on_startup():
    """
    Smart collection on system startup.

    Handles short gaps (< 1.5 hours) and begins regular schedule.
    """
    logger.info("System startup - checking for missed data...")

    # Get last successful collection time from database
    last_collection = get_last_collection_time()

    if last_collection:
        gap = datetime.now() - last_collection
        gap_hours = gap.total_seconds() / 3600

        if gap_hours <= 1.5:
            logger.info(f"Gap is {gap_hours:.1f}h - attempting backfill...")
            # Fetch trades since last collection (within API window)
            backfill_gap(last_collection, datetime.now())
        else:
            logger.warning(f"Gap is {gap_hours:.1f}h - too large to backfill")
            logger.warning(f"Accepting gap from {last_collection} to now")

    # Start current collection
    collect_hour()

    # Schedule regular collections every 30 minutes
    schedule.every(30).minutes.do(collect_hour)
```

**Result**: If you restart within 1.5 hours (e.g., PC crash, quick reboot), we can recover the gap!

---

## 📈 **Expected Data Quality**

### With Option 2 (30-min collections, 11 AM - 11 PM):

**Coverage**:
- **Captured**: 12 hours/day (50% of day)
- **Missed**: 12 hours/day (overnight, less active)

**Market Activity**:
- **US/EU trading hours**: 11 AM - 11 PM covers most global activity
- **Overnight**: Typically lower volume (Asia markets, less crypto activity)

**ML Training**:
- **2,160 collections** over 90 days = excellent dataset
- Captures intraday volatility, regime changes, trend shifts
- Sufficient for institutional-grade model

**Verdict**: **This is a NON-ISSUE**. 50% coverage during active hours is better than you think!

---

## 🚀 **Next Steps**

1. **Implement Option 2** (30-minute collections during uptime)
2. **Add startup gap handler** (recover < 1.5h gaps)
3. **Monitor data quality** for 1 week
4. **If needed**: Upgrade to cloud VM later (always an option)

**Bottom Line**: Don't let 12-hour gaps block you. **1,080 hours of trading data is SUFFICIENT** for excellent ML training. Start collecting NOW with what you have!
