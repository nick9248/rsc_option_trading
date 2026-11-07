"""
Message formatting for the defined-risk (iron condor / butterfly) scanners.

format_defined_risk_alert builds the plain-text summary shown in the GUI
automation tab's 'Alert Preview' box. The Telegram alert workflow that used
to send it (DefinedRiskAlertRule) was removed; the header still carries the
gate_pass label, the same field stored in defined_risk_scan_history.
"""
from datetime import datetime
from typing import Any, Dict

STRUCTURE_LABELS = {"iron_condor": "IRON CONDOR SCANNER", "butterfly": "LONG BUTTERFLY SCANNER"}


def format_defined_risk_alert(scan_result: Dict[str, Any], structure_type: str) -> str:
    """Plain-text alert-preview body. Never raises on missing data -- returns a short message instead."""
    label = STRUCTURE_LABELS.get(structure_type, structure_type.upper())
    currency = scan_result["currency"]
    as_of: datetime = scan_result["as_of"]
    index_price = scan_result["index_price"]
    expiries = scan_result.get("expiries", [])

    if not expiries:
        return f"{label} — {currency}\nNo qualifying candidates found."

    top = expiries[0]
    best = top["best"]
    regime = top.get("regime", {}) or {}
    gate_pass = regime.get("gate_pass")
    header = f"{label} — CALM-REGIME MATCH — {currency}" if gate_pass else f"{label} — {currency}"

    lines = [
        header,
        f"as of {as_of.strftime('%Y-%m-%d %H:%M UTC')} | index ${index_price:,.2f}",
        "",
        f"BEST: {top['expiry']} ({top['dte']:.0f}d)",
    ]
    if structure_type == "iron_condor":
        lines.append(f"  Short {best['short_call']:.0f}C / {best['short_put']:.0f}P, "
                      f"Long {best['long_call']:.0f}C / {best['long_put']:.0f}P")
        lines.append(f"  Credit: ${best['cost_or_credit']:,.2f}  (max loss ${best['max_loss']:,.2f})")
    else:
        lines.append(f"  Wings: {best['k1']:.0f} / {best['k2']:.0f} / {best['k3']:.0f}")
        lines.append(f"  Cost: ${best['cost_or_credit']:,.2f}  (max profit ${best['max_profit']:,.2f})")

    lines.append(f"  Breakevens: {best['breakeven_lo']:,.0f} / {best['breakeven_hi']:,.0f}")
    prob_str = f"{best['prob_profit']:.1f}%" if best.get("prob_profit") is not None else "N/A"
    ev_str = f"${best['ev']:,.2f}" if best.get("ev") is not None else "N/A"
    lines.append(f"  Probability of profit (RV-implied): {prob_str}  |  EV: {ev_str}")
    lines.append("")
    lines.append(f"  Regime: net_gex={regime.get('net_gex')}  RV10/RV30={regime.get('rv_ratio')}  "
                  f"gate_pass={gate_pass}")
    lines.append("")
    lines.append(f"Chart: {best.get('deribit_url', '')}")

    return "\n".join(lines)
