# Deribit API Reconnaissance - Key Findings

## Date: February 2, 2026

### ✅ SUCCESS: APIs Validated

#### 1. Recent Trades API (`/public/get_last_trades_by_currency`)
**Status**: ✅ **WORKING PERFECTLY**

**Endpoint**: `https://www.deribit.com/api/v2/public/get_last_trades_by_currency`

**Parameters**:
```json
{
  "currency": "BTC" or "ETH",
  "kind": "option",
  "count": 1-1000 (number of trades to return)
}
```

**Response Structure** (Sample Trade):
```json
{
  "timestamp": 1770052028749,
  "iv": 76.81,                          // ✅ IMPLIED VOLATILITY PRESENT!
  "price": 0.0001,
  "amount": 0.2,
  "direction": "sell",
  "index_price": 78871.86,              // Spot/Index price (for Greeks)
  "instrument_name": "BTC-3FEB26-85000-C",  // Format: CURRENCY-EXPIRY-STRIKE-TYPE
  "trade_seq": 217,
  "mark_price": 0.00010278,
  "tick_direction": 3,
  "contracts": 0.2,
  "trade_id": "411868614"
}
```

**Critical Fields Confirmed**:
- ✅ `iv`: Implied Volatility (%) - **CRITICAL for Greeks calculation**
- ✅ `price`: Trade execution price (in BTC for BTC options)
- ✅ `amount`: Trade size (contracts)
- ✅ `timestamp`: Unix timestamp in milliseconds
- ✅ `index_price`: Underlying asset price (spot/index)
- ✅ `mark_price`: Exchange mark price
- ✅ `instrument_name`: Format is `{CURRENCY}-{EXPIRY}-{STRIKE}-{TYPE}`
  - CURRENCY: BTC, ETH
  - EXPIRY: DDMMMYY (e.g., 3FEB26 = February 3, 2026)
  - STRIKE: Strike price (e.g., 85000)
  - TYPE: C (Call) or P (Put)

---

#### 2. Open Interest API (`/public/get_book_summary_by_instrument`)
**Status**: ✅ **WORKING - OI FIELD CONFIRMED**

**Endpoint**: `https://www.deribit.com/api/v2/public/get_book_summary_by_instrument`

**Parameters**:
```json
{
  "instrument_name": "BTC-PERPETUAL" (or any option instrument)
}
```

**Response Fields**:
- ✅ `open_interest`: **Outstanding contracts (NOT cumulative volume)**
- ✅ `volume`: 24h trading volume
- ✅ `bid_price`, `ask_price`, `mark_price`
- ✅ `mark_iv`: Mark implied volatility (for options)

**Critical Fix Applied**: Original plan said "infer OI from cumulative volume" which is WRONG. We now pull `open_interest` directly from API.

---

### 🔴 Issue Discovered: Historical Trades with Time Range

**Endpoint**: `/public/get_last_trades_by_currency_and_time`
**Status**: ❌ **Returns 0 trades for historical dates (Jan 2, 2026, Dec 1, 2025, etc.)**

**Possible Reasons**:
1. **Data retention limits**: Deribit may only provide recent trade history (last N days)
2. **Parameter issues**: Time range parameters might be incorrect
3. **Endpoint limitation**: May need different endpoint for deep historical data

**Tested Time Ranges** (all returned 0 trades):
- January 1, 2025, 00:00-01:00 UTC
- February 1, 2026, 14:00-15:00 UTC
- January 2, 2026, 14:00-15:00 UTC

---

### 📊 Data Availability Assessment

| Data Type | Availability | Source | Notes |
|-----------|--------------|--------|-------|
| **Recent Trades** (last few mins/hours) | ✅ Full | `/public/get_last_trades_by_currency` | IV included, all fields present |
| **Historical Trades** (months ago) | ❌ Limited? | `/public/get_last_trades_by_currency_and_time` | Returns 0 trades - needs investigation |
| **Current Open Interest** | ✅ Full | `/public/get_book_summary_by_instrument` | Real-time OI available |
| **Historical OI** | ❓ Unknown | TBD - may need snapshots or Laevitas | Need to check retention |

---

### 🎯 Implications for Backfill Strategy

#### Original Plan:
- Backfill 6-12 months of historical trades from Deribit API
- Timeline: 1 week

#### Revised Reality:
- **Deep historical trades may not be available** via public API
- Need to investigate:
  1. How far back does `/get_last_trades_by_currency_and_time` actually go?
  2. Do we need authenticated API for historical data?
  3. Should we use alternative data source (Laevitas, Tardis.dev)?
  4. Can we use forward collection starting NOW (collect prospectively)?

---

### 🔬 Next Steps for Investigation

#### 1. Test Historical Trade Limits
- ✅ Test recent data (working)
- ⏳ Test 1 day ago
- ⏳ Test 1 week ago
- ⏳ Test 1 month ago
- ⏳ Determine exact retention limit

#### 2. Check Deribit Documentation
- Read official docs on data retention for public APIs
- Check if authentication provides deeper history
- Review rate limits for high-volume queries

#### 3. Evaluate Alternative Approaches

**Option A: Prospective Collection (Starting NOW)**
- Start collecting trades/OI hourly from today forward
- Build up 3-6 months of data over time
- **Pros**: Free, reliable, complete data
- **Cons**: 3-6 month wait before ML training

**Option B: Use GitHub backfill tool (RiveChen)**
- Try the RiveChen/deribit-historical-data tool
- May have figured out historical data access
- **Pros**: If it works, get historical data
- **Cons**: May hit same retention limits

**Option C: Paid Data Provider**
- Tardis.dev, Laevitas, or Amberdata
- **Pros**: Deep historical data (months/years)
- **Cons**: Cost (though Tardis has free tier)

**Option D: Hybrid Approach (RECOMMENDED)**
- Use existing 34 days of data (Oct 31 - Dec 4, 2025) from `deribit_options_data`
- Start prospective collection NOW (hourly captures)
- Attempt RiveChen tool for any additional historical backfill
- Begin ML prototyping with 34 days while collecting more data

---

### 💡 Recommendation: Hybrid Start + Prospective Collection

**Immediate Actions**:
1. ✅ Confirmed APIs work (reconnaissance complete)
2. ⏳ **Set up prospective collection** (start collecting NOW)
   - Hourly snapshots of:
     - Recent trades (`/get_last_trades_by_currency`)
     - Open Interest (`/get_book_summary_by_currency`)
     - Book summary (full option chains)
3. ⏳ **Test RiveChen backfill tool** (may get some historical data)
4. ⏳ **Use existing 34 days** for architecture prototyping

**Timeline Adjustment**:
- Original: 1 week backfill → 8 weeks ML → 2.5 months total
- Revised: Start collection NOW + prototype with 34 days → ML training in parallel with data collection

**Nobel-Level Insight**: Don't wait for perfect data. Start with what we have (34 days), build the system, and improve data coverage in parallel. This is how real quant shops operate.

---

### 📝 Critical Fixes Validated

1. ✅ **OI from API confirmed** (not inferred from volume)
2. ✅ **IV present in trades** (can calculate Greeks)
3. ✅ **All critical fields available** (price, timestamp, index_price, etc.)
4. ✅ **Instrument naming format understood** (CURRENCY-EXPIRY-STRIKE-TYPE)

---

### 🚀 Ready to Proceed

**Next Task**: Set up prospective data collection pipeline (hourly captures)

**Parallel Track**: Test RiveChen backfill tool to see if historical data is accessible

**Backup Plan**: If no historical data available, proceed with:
- Existing 34 days (Oct 31 - Dec 4, 2025)
- Prospective collection starting today
- ML training begins when we hit 90 days total (6-8 weeks from now)
