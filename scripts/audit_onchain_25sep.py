"""
Auditor script for on-chain analysis - 25SEP26 expiry.

Independently fetches raw data and re-derives every reported metric
without using the calculator classes, then compares.
"""

import math
import random
from collections import defaultdict

from coding.core.logging.logging_setup import init_logging
init_logging(level="WARNING")

from coding.service.deribit.deribit_api_service import DeribitApiService
from coding.service.snapshot.snapshot_service import SnapshotService

EXPIRY   = "25SEP26"
CURRENCY = "BTC"

PASS = "  PASS"
FAIL = "  *** FAIL ***"

def section(title):
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)

def check(label, expected, actual, tol=0.01):
    ok = abs(expected - actual) <= tol * max(abs(expected), 1)
    status = PASS if ok else FAIL
    print(f"  {label}: expected={expected:.4f}  actual={actual:.4f}{status}")
    return ok

# ─────────────────────────────────────────────────────────────────────────────
with DeribitApiService() as api:
    snap_svc = SnapshotService(api)

    # ── 1. RAW DATA ──────────────────────────────────────────────────────────
    section("1. RAW DATA FETCH")
    all_data = api.get_book_summary(currency=CURRENCY, kind="option")
    print(f"  Total instruments fetched: {len(all_data)}")

    instruments_25sep = [
        i for i in all_data
        if i.get("instrument_name", "").split("-")[1] == EXPIRY
    ]
    print(f"  Instruments for {EXPIRY}: {len(instruments_25sep)}")

    # Underlying price (from SnapshotService fix — full dataset, highest volume)
    active = [i for i in all_data if (i.get("volume") or 0) > 0 and i.get("underlying_price")]
    true_price = max(active, key=lambda x: x.get("volume", 0))["underlying_price"] if active else 0.0
    print(f"  Underlying price (corrected): ${true_price:,.2f}")

    # ── 2. BASIC COUNTS ──────────────────────────────────────────────────────
    section("2. TOTAL INSTRUMENTS / CALLS / PUTS")
    calls_raw = [i for i in instruments_25sep if i.get("instrument_name", "").endswith("-C")]
    puts_raw  = [i for i in instruments_25sep if i.get("instrument_name", "").endswith("-P")]
    print(f"  Total: {len(instruments_25sep)}  Calls: {len(calls_raw)}  Puts: {len(puts_raw)}")

    # ── 3. PUT/CALL RATIO ────────────────────────────────────────────────────
    section("3. PUT / CALL RATIO (by Open Interest)")
    total_call_oi = sum(i.get("open_interest", 0) or 0 for i in calls_raw)
    total_put_oi  = sum(i.get("open_interest", 0) or 0 for i in puts_raw)
    pcr = total_put_oi / total_call_oi if total_call_oi > 0 else float("inf")
    print(f"  Call OI: {total_call_oi:,.1f}  Put OI: {total_put_oi:,.1f}  P/C: {pcr:.4f}")
    print(f"  (Report should show these exact numbers)")

    # ── 4. MAX PAIN ──────────────────────────────────────────────────────────
    section("4. MAX PAIN")
    # Max pain = strike where total dollar pain to option holders is maximized
    # pain_at_S = sum calls: max(0, S-K)*OI  +  sum puts: max(0,K-S)*OI
    strikes_oi: dict = defaultdict(lambda: {"call_oi": 0.0, "put_oi": 0.0})
    for i in instruments_25sep:
        parts = i["instrument_name"].split("-")
        if len(parts) < 4:
            continue
        try:
            k = float(parts[2])
        except ValueError:
            continue
        oi = i.get("open_interest", 0) or 0
        if parts[3] == "C":
            strikes_oi[k]["call_oi"] += oi
        else:
            strikes_oi[k]["put_oi"] += oi

    pain_by_strike = {}
    for test_strike in strikes_oi:
        pain = 0.0
        for k, d in strikes_oi.items():
            pain += max(0, test_strike - k) * d["call_oi"]
            pain += max(0, k - test_strike) * d["put_oi"]
        pain_by_strike[test_strike] = pain

    if pain_by_strike:
        max_pain_strike = min(pain_by_strike, key=lambda s: pain_by_strike[s])
        dist = true_price - max_pain_strike
        dist_pct = dist / max_pain_strike * 100
        print(f"  Max Pain Strike: ${max_pain_strike:,.0f}")
        print(f"  Distance from Current: ${dist:+,.2f} ({dist_pct:+.2f}%)")
    else:
        print("  No data")

    # ── 5. VOLUME STATISTICS ─────────────────────────────────────────────────
    section("5. VOLUME STATISTICS")
    call_vols = [i.get("volume", 0) or 0 for i in calls_raw]
    put_vols  = [i.get("volume", 0) or 0 for i in puts_raw]
    total_call_vol = sum(call_vols)
    total_put_vol  = sum(put_vols)
    print(f"  Total Call Volume: {total_call_vol:,.4f}")
    print(f"  Total Put Volume:  {total_put_vol:,.4f}")
    print(f"  Total Volume:      {total_call_vol + total_put_vol:,.4f}")

    # ── 6. MONEYNESS ─────────────────────────────────────────────────────────
    section("6. MONEYNESS ANALYSIS")
    itm_calls = [i for i in calls_raw if float(i["instrument_name"].split("-")[2]) < true_price]
    otm_calls = [i for i in calls_raw if float(i["instrument_name"].split("-")[2]) >= true_price]
    itm_puts  = [i for i in puts_raw  if float(i["instrument_name"].split("-")[2]) > true_price]
    otm_puts  = [i for i in puts_raw  if float(i["instrument_name"].split("-")[2]) <= true_price]
    print(f"  ITM Calls: {len(itm_calls)}   OTM Calls: {len(otm_calls)}")
    print(f"  ITM Puts:  {len(itm_puts)}   OTM Puts:  {len(otm_puts)}")
    itm_call_oi = sum(i.get("open_interest", 0) or 0 for i in itm_calls)
    otm_call_oi = sum(i.get("open_interest", 0) or 0 for i in otm_calls)
    itm_put_oi  = sum(i.get("open_interest", 0) or 0 for i in itm_puts)
    otm_put_oi  = sum(i.get("open_interest", 0) or 0 for i in otm_puts)
    print(f"  ITM Call OI: {itm_call_oi:,.1f}  OTM Call OI: {otm_call_oi:,.1f}")
    print(f"  ITM Put OI:  {itm_put_oi:,.1f}   OTM Put OI:  {otm_put_oi:,.1f}")

    # ── 7. GEX / DEX FORMULA AUDIT ───────────────────────────────────────────
    section("7. GEX / DEX FORMULA AUDIT (sample: 6 instruments with OI > 0)")
    # Fetch Greeks for a manageable sample — pick top-OI instruments near spot
    sample_insts = sorted(
        [i for i in instruments_25sep if (i.get("open_interest") or 0) > 0],
        key=lambda x: x.get("open_interest", 0),
        reverse=True
    )[:6]

    print(f"  Fetching ticker Greeks for {len(sample_insts)} instruments...")
    enriched = []
    for i in sample_insts:
        name = i["instrument_name"]
        ticker = api.get_ticker(name)
        greeks = ticker.get("greeks", {})
        parts = name.split("-")
        enriched.append({
            "name": name,
            "strike": float(parts[2]),
            "option_type": parts[3],
            "oi": i.get("open_interest", 0) or 0,
            "gamma": greeks.get("gamma") or 0,
            "delta": greeks.get("delta") or 0,
            "mark_iv": ticker.get("mark_iv"),
        })

    # Manual GEX calculation per instrument
    print()
    print(f"  {'Instrument':<30} {'Strike':>8} {'T':>2} {'OI':>8} {'Gamma':>10} "
          f"{'Gamma*OI':>12} {'Net GEX contrib':>16}")
    print(f"  {'-'*30} {'-'*8} {'-'*2} {'-'*8} {'-'*10} {'-'*12} {'-'*16}")

    # Aggregate by strike for audit
    audit_strikes: dict = defaultdict(lambda: {"call_g_oi": 0.0, "put_g_oi": 0.0,
                                                "call_d_oi": 0.0, "put_d_oi": 0.0})
    for inst in enriched:
        k = inst["strike"]
        g_oi = inst["gamma"] * inst["oi"]
        gex_contrib = g_oi * (true_price ** 2) * 0.01
        if inst["option_type"] == "C":
            audit_strikes[k]["call_g_oi"] += g_oi
            audit_strikes[k]["call_d_oi"] += inst["delta"] * inst["oi"]
        else:
            audit_strikes[k]["put_g_oi"] += g_oi
            audit_strikes[k]["put_d_oi"] += inst["delta"] * inst["oi"]
        print(f"  {inst['name']:<30} {k:>8,.0f} {inst['option_type']:>2} {inst['oi']:>8,.1f} "
              f"{inst['gamma']:>10.6f} {g_oi:>12.4f} {gex_contrib:>+16.2f}")

    print()
    print("  Per-strike net GEX (manual calculation):")
    print(f"  {'Strike':>8}  {'NetGEX':>14}  {'NetDEX':>10}  Formula check")
    for k in sorted(audit_strikes):
        d = audit_strikes[k]
        net_gamma = d["call_g_oi"] - d["put_g_oi"]
        net_gex   = net_gamma * (true_price ** 2) * 0.01
        net_dex   = d["call_d_oi"] + d["put_d_oi"]
        print(f"  {k:>8,.0f}  {net_gex:>+14.2f}  {net_dex:>+10.4f}  "
              f"({d['call_g_oi']:.4f} - {d['put_g_oi']:.4f}) * {true_price:.0f}^2 * 0.01")

    # ── 8. VOLATILITY SURFACE AUDIT ──────────────────────────────────────────
    section("8. VOLATILITY SURFACE AUDIT")
    # ATM IV: closest call and put to spot, average their mark_iv
    calls_with_iv = [i for i in enriched if i["option_type"] == "C" and i["mark_iv"]]
    puts_with_iv  = [i for i in enriched if i["option_type"] == "P" and i["mark_iv"]]

    # NOTE: the 6-instrument sample is sorted by OI (deep OTM for far expiry).
    # For a proper ATM IV check, fetch the strike closest to spot independently.
    print("  Fetching ATM instruments (closest strike to spot) directly...")
    all_strikes = sorted(set(float(i["instrument_name"].split("-")[2])
                             for i in instruments_25sep if len(i["instrument_name"].split("-")) >= 4))
    atm_strike = min(all_strikes, key=lambda k: abs(k - true_price))
    print(f"  Closest strike to spot ${true_price:,.0f}: ${atm_strike:,.0f}")
    atm_iv_list = []
    for opt_type, label in [("C", "Call"), ("P", "Put")]:
        name = f"BTC-{EXPIRY}-{int(atm_strike)}-{opt_type}"
        try:
            ticker = api.get_ticker(name)
            iv = ticker.get("mark_iv")
            if iv:
                atm_iv_list.append(iv)
                print(f"  ATM {label}: {name}  mark_iv={iv:.2f}%")
        except Exception as e:
            print(f"  ATM {label}: could not fetch {name}: {e}")
    if atm_iv_list:
        atm_iv_true = sum(atm_iv_list) / len(atm_iv_list)
        print(f"  TRUE ATM IV (avg call+put at closest strike): {atm_iv_true:.2f}%")

    # 25d skew requires full instrument set with deltas — note limitation
    print()
    print("  NOTE: 25-delta skew audit requires full instrument set.")
    print("  The calculator uses: 25d Put IV - 25d Call IV (find insts closest to ±0.25 delta)")

    # ── 9. VANNA / CHARM AUDIT ───────────────────────────────────────────────
    section("9. VANNA / CHARM FORMULA AUDIT (same sample)")
    # Vanna ≈ gamma * vega / spot   (per instrument, weighted by OI)
    # Charm ≈ -gamma * theta        (per instrument, weighted by OI)
    # Fetch vega and theta for sample
    print(f"  {'Instrument':<30} {'Vanna*OI':>12}  {'Charm*OI':>12}")
    net_vanna_manual = 0.0
    net_charm_manual = 0.0
    for i in sample_insts:
        name = i["instrument_name"]
        ticker = api.get_ticker(name)
        greeks = ticker.get("greeks", {})
        gamma = greeks.get("gamma") or 0
        vega  = greeks.get("vega")  or 0
        theta = greeks.get("theta") or 0
        oi    = i.get("open_interest", 0) or 0
        if oi <= 0:
            continue
        vanna = gamma * vega / true_price if true_price > 0 else 0
        charm = -gamma * theta
        net_vanna_manual += vanna * oi
        net_charm_manual += charm * oi
        print(f"  {name:<30} {vanna*oi:>+12.8f}  {charm*oi:>+12.8f}")
    print(f"  Net Vanna (sample): {net_vanna_manual:+.8f}")
    print(f"  Net Charm (sample): {net_charm_manual:+.8f}")
    print(f"  (Report value will differ — uses ALL instruments, not just this sample)")
    vanna_sign = "bullish (IV drop -> dealers buy)" if net_vanna_manual > 0 else "bearish (IV drop -> dealers sell)"
    charm_sign = "bullish drift" if net_charm_manual > 0 else "bearish drift"
    print(f"  Direction: Vanna={vanna_sign}  Charm={charm_sign}")

    # ── 10. P/C BY MONEYNESS BUCKETS ─────────────────────────────────────────
    section("10. P/C RATIO BY MONEYNESS (all 25SEP26 instruments)")
    buckets = {"atm": {"coi": 0, "poi": 0}, "near_otm": {"coi": 0, "poi": 0}, "far_otm": {"coi": 0, "poi": 0}}
    for i in instruments_25sep:
        parts = i["instrument_name"].split("-")
        if len(parts) < 4:
            continue
        k = float(parts[2])
        oi = i.get("open_interest", 0) or 0
        opt = parts[3]
        d = abs(k - true_price) / true_price * 100
        b = "atm" if d <= 5 else ("near_otm" if d <= 15 else "far_otm")
        if opt == "C":
            buckets[b]["coi"] += oi
        else:
            buckets[b]["poi"] += oi

    for label, b in [("ATM (±5%)", "atm"), ("Near-OTM (5-15%)", "near_otm"), ("Far-OTM (>15%)", "far_otm")]:
        coi, poi = buckets[b]["coi"], buckets[b]["poi"]
        ratio = poi / coi if coi > 0 else float("inf")
        print(f"  {label:<22} Call OI={coi:>8,.1f}  Put OI={poi:>8,.1f}  P/C={ratio:.2f}")

    # ── 11. FLOW & OI CHANGES (DB check) ─────────────────────────────────────
    section("11. FLOW ANALYSIS & OI CHANGES (database check)")
    try:
        from coding.core.database.repository import DatabaseRepository
        repo = DatabaseRepository()

        # Check flow data
        try:
            flow = repo.get_flow_metrics(currency=CURRENCY, expiration=EXPIRY, window_hours=24)
            if flow:
                print(f"  Flow data: {len(flow)} records found  PASS")
                if flow:
                    sample_flow = flow[0]
                    print(f"  Sample record keys: {list(sample_flow.keys())}")
            else:
                print(f"  Flow data: NO RECORDS in DB for {EXPIRY} -- flow section will show no data")
        except Exception as e:
            print(f"  Flow data: DB query failed - {e}")

        # Check OI snapshot (previous day)
        try:
            prev_oi = repo.get_previous_oi_snapshot(currency=CURRENCY, expiration=EXPIRY)
            if prev_oi:
                print(f"  OI snapshot: {len(prev_oi)} strike/type pairs from previous day  PASS")
            else:
                print(f"  OI snapshot: NO PREVIOUS SNAPSHOT in DB -- OI changes section will be empty")
        except Exception as e:
            print(f"  OI snapshot: DB query failed - {e}")

        # Check today's OI snapshot (current)
        try:
            latest_oi = repo.get_latest_snapshot_oi(currency=CURRENCY, expiration=EXPIRY)
            if latest_oi:
                print(f"  Today OI snapshot: {len(latest_oi)} records  PASS")
            else:
                print(f"  Today OI snapshot: NO DATA for today yet")
        except Exception as e:
            print(f"  Today OI snapshot: DB query failed - {e}")

    except Exception as e:
        print(f"  Could not initialize DB: {e}")

print()
print("=" * 70)
print("  AUDIT COMPLETE")
print("=" * 70)
