"""Read the market report and the on-chain dashboard HTML (their tables, panels and chart data) and build the Overview tab.

The unified dashboard is assembled from the two HTML files a run writes, so these readers depend on their structure
(see page.py). Pure functions: no files, no network.
"""
import html as H
import json
import math
import re
from html.parser import HTMLParser


# --------------------------------------------------------------------------- parsing
class _Tables(HTMLParser):
    """Collect every <table>: nearest preceding heading, caption, class, header row, body rows."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.stack = [], []
        self.head, self.inh, self.hbuf = "", None, ""
        self.cell = self.row = self.cap = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("h2", "h3", "h4"):
            self.inh, self.hbuf = tag, ""
        elif tag == "table":
            self.stack.append(dict(title=self.head, caption="", cls=a.get("class", ""), head=[], rows=[], inhead=False))
        elif self.stack:
            t = self.stack[-1]
            if tag == "thead":
                t["inhead"] = True
            elif tag == "tbody":
                t["inhead"] = False
            elif tag == "tr":
                self.row = []
            elif tag in ("td", "th"):
                self.cell = ""
            elif tag == "caption":
                self.cap = ""
            elif tag == "br" and self.cell is not None:
                self.cell += " | "

    def handle_endtag(self, tag):
        if tag == self.inh:
            self.head, self.inh = re.sub(r"\s+", " ", self.hbuf).strip(), None
        elif self.stack:
            t = self.stack[-1]
            if tag in ("td", "th") and self.cell is not None and self.row is not None:
                self.row.append(re.sub(r"\s+", " ", self.cell).strip())
                self.cell = None
            elif tag == "tr" and self.row is not None:
                if self.row:
                    (t["head"] if (t["inhead"] and not t["head"]) else t["rows"]).append(self.row)
                self.row = None
            elif tag == "caption":
                t["caption"], self.cap = re.sub(r"\s+", " ", self.cap or "").strip(), None
            elif tag == "table":
                t = self.stack.pop()
                t["head"] = t["head"][0] if t["head"] else (t["rows"].pop(0) if t["rows"] else [])
                self.tables.append(t)

    def handle_data(self, d):
        if self.inh:
            self.hbuf += d
        if self.cell is not None:
            self.cell += d
        if self.cap is not None:
            self.cap += d


def parse_tables(s):
    p = _Tables()
    p.feed(re.sub(r"<script.*?</script>", "", s, flags=re.S))
    return [t for t in p.tables if t["head"] and t["head"][0] != "Situation" and "help" not in t["cls"]]


def find(tables, title=None, cls=None, first_col=None):
    for t in tables:
        if title and not t["title"].lower().startswith(title.lower()):
            continue
        if cls and cls not in t["cls"].split():
            continue
        if first_col and (not t["head"] or t["head"][0] != first_col):
            continue
        return t
    return None


def rows_as_dicts(t):
    if not t:
        return []
    return [dict(zip(t["head"], r)) for r in t["rows"] if len(r) >= len(t["head"]) - 1]


def kv(t):
    return {r[0]: r[1] for r in (t["rows"] if t else []) if len(r) >= 2}


def num(s):
    if s is None:
        return None
    s = str(s).replace("−", "-").replace(",", "")
    m = re.search(r"[-+]?\d*\.?\d+", s)
    if not m:
        return None
    v = float(m.group())
    tail = s[m.end():m.end() + 2].strip().upper()
    if tail.startswith("B"):
        v *= 1e9
    elif tail.startswith("M"):
        v *= 1e6
    elif tail.startswith("K"):
        v *= 1e3
    return v


def esc(s):
    return H.escape(str(s), quote=True)


def minus(s):
    return str(s).replace("-", "−") if str(s)[:1] == "-" or " -" in str(s) else str(s)


def fmt_int(v):
    return minus(f"{v:,.0f}")


def fmt_usd_short(v):
    a = abs(v)
    if a >= 1e9:
        s = f"{v / 1e9:,.2f}B"
    elif a >= 1e6:
        s = f"{v / 1e6:,.1f}M"
    elif a >= 1e3:
        s = f"{v / 1e3:,.1f}K"
    else:
        s = f"{v:,.0f}"
    return minus(s)


def pct_from(p, spot):
    return minus(f"{(p / spot - 1) * 100:+.1f}%")


# --------------------------------------------------------------------------- extraction
def extract(s, prefix):
    data = json.loads(re.search(r'<script type="application/json" id="report-data">(.*?)</script>', s, re.S).group(1))
    plotly = None
    for m in re.finditer(r"<script>(.*?)</script>", s, re.S):
        if "plotly.js v" in m.group(1)[:400]:
            plotly = m.group(1)
    body = re.sub(r"<script.*?</script>", "", s, flags=re.S)
    panels = []
    for m in re.finditer(r'<section class="panel" role="tabpanel" id="panel-([^"]+)"[^>]*>(.*?)</section>\s*(?=<section class="panel" role="tabpanel"|</main>)', body, re.S):
        panels.append((m.group(1), m.group(2)))
    labels = dict(re.findall(r'<button type="button" role="tab" id="tab-([^"]+)"[^>]*>([^<]*)', body))
    facts = re.findall(r"<dt>(.*?)</dt>\s*<dd>(.*?)</dd>", (re.search(r'<dl class="facts">(.*?)</dl>', body, re.S) or [None, ""])[1] if re.search(r'<dl class="facts">', body) else "", re.S)
    intro = ""
    m = re.search(r'</dl>(.*?)<nav class="tabs"', body, re.S)
    if m:
        intro = m.group(1)
        intro = re.sub(r"^\s*</div>|</header>\s*$", "", intro.strip()).strip()
        intro = re.sub(r"</header>", "", intro)
    # namespace switchers / figures / pages
    def ns_html(h):
        h = re.sub(r'data-switcher="([^"]+)"', lambda x: f'data-switcher="{prefix}-{x.group(1)}"', h)
        h = re.sub(r'data-switcher-target="([^"]+)"', lambda x: f'data-switcher-target="{prefix}-{x.group(1)}"', h)
        h = re.sub(r'data-page="([^"]+)"', lambda x: f'data-page="{prefix}-{x.group(1)}"', h)
        return h
    figs = {f"{prefix}-{k}": v for k, v in data.get("figures", {}).items()}
    pages = {f"{prefix}-{k}": v for k, v in data.get("pages", {}).items()}
    sw = {}
    for k, v in data.get("switchers", {}).items():
        views = {}
        for vk, vv in v.get("views", {}).items():
            vv = dict(vv)
            if vv.get("figure"):
                vv["figure"] = f"{prefix}-{vv['figure']}"
            if vv.get("page"):
                vv["page"] = f"{prefix}-{vv['page']}"
            if vv.get("caption"):
                vv["caption"] = ns_html(vv["caption"])
            views[vk] = vv
        sw[f"{prefix}-{k}"] = dict(v, views=views)
    panels = [(pid, ns_html(inner)) for pid, inner in panels]
    return dict(figures=figs, pages=pages, switchers=sw, panels=panels, labels=labels,
                facts=[(re.sub("<[^>]+>", "", a).strip(), re.sub("<[^>]+>", "", b).strip()) for a, b in facts],
                intro=ns_html(intro), plotly=plotly, tables=parse_tables(s), raw=body)


# --------------------------------------------------------------------------- overview widgets
ICON = {
    "map": '<path d="M3 6h18M3 12h18M3 18h18"/><circle cx="9" cy="12" r="2.5" fill="currentColor"/>',
    "bars": '<path d="M12 3v18M6 8h6M12 13h7M8 18h4"/>',
    "wave": '<path d="M3 17c3-8 5-10 8-6s5 2 10-6"/>',
    "pie": '<circle cx="12" cy="12" r="9"/><path d="M12 3v9l7 5"/>',
    "flag": '<path d="M5 21V4h11l-2 4 2 4H5"/>',
    "grid": '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M3 9h18M9 3v18"/>',
    "bolt": '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "list": '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    "arrow": '<path d="M5 12h14M13 6l6 6-6 6"/>',
}


def icon(name, size=15):
    return f'<svg class="ico" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICON[name]}</svg>'


def card(title, inner, ico="grid", sub="", goto=None, cls=""):
    g = f'<button type="button" class="goto" data-goto="{goto}">Open tab {icon("arrow", 13)}</button>' if goto else ""
    s = f'<span class="card-sub">{esc(sub)}</span>' if sub else ""
    return (f'<section class="card {cls}"><div class="card-head"><div class="card-title"><span class="card-ico">{icon(ico)}</span>'
            f'<h3>{esc(title)}</h3></div><div class="card-meta">{s}{g}</div></div>{inner}</section>')


def spark(vals, w=180, h=52, cls="neg", uid="sp", tips=None):
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    pts = [(2 + i * (w - 4) / (len(vals) - 1), h - 4 - (v - lo) / ((hi - lo) or 1) * (h - 8)) for i, v in enumerate(vals)]
    p = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    dots = "".join(f'<circle class="sp-dot {cls}" cx="{x:.1f}" cy="{y:.1f}" r="{3 if i == len(pts) - 1 else 2}" tabindex="0" data-tip="{esc(tips[i]) if tips else ""}"/>' for i, (x, y) in enumerate(pts))
    return (f'<svg class="spark" viewBox="0 0 {w} {h}" width="{w}" height="{h}" aria-hidden="true"><defs><linearGradient id="{uid}" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0" class="stop-{cls}" stop-opacity=".30"/><stop offset="1" class="stop-{cls}" stop-opacity="0"/></linearGradient></defs>'
            f'<polygon points="2,{h} {p} {w - 2},{h}" fill="url(#{uid})"/><polyline class="sp-line {cls}" points="{p}"/>{dots}</svg>')


def gauge(frac, big, label, sub, uid):
    if frac is None:
        return ""
    L = math.pi * 50
    return (f'<figure class="gauge"><svg viewBox="0 0 120 70" width="150" height="88" role="img" aria-label="{esc(label)}: {esc(big)}">'
            f'<defs><linearGradient id="{uid}" x1="0" y1="0" x2="1" y2="0"><stop offset="0" class="stop-acc"/><stop offset="1" class="stop-warn"/></linearGradient></defs>'
            f'<path class="g-track" d="M10,62 A50,50 0 0 1 110,62"/><path d="M10,62 A50,50 0 0 1 110,62" fill="none" stroke="url(#{uid})" stroke-width="9" stroke-linecap="round" stroke-dasharray="{L * max(0, min(1, frac)):.1f} {L:.1f}"/>'
            f'<text class="g-big" x="60" y="52" text-anchor="middle">{esc(big)}</text><text class="g-sub" x="60" y="66" text-anchor="middle">{esc(sub)}</text></svg>'
            f'<figcaption>{esc(label)}</figcaption></figure>')


def tile(k, v, s, tone="", extra=""):
    return f'<div class="tile"><span class="t-k">{esc(k)}</span><div class="t-row"><span class="t-v">{esc(v)}</span>{extra}</div><span class="t-s {tone}">{esc(s)}</span></div>'


def meter_mini(frac):
    return f'<span class="mini-meter" aria-hidden="true"><span style="width:{max(0, min(1, frac)) * 100:.0f}%"></span></span>'


def build_overview(A, B, currency):
    ta, tb = A["tables"], B["tables"]
    mm = kv(find(tb, "Market metrics"))
    vol = kv(find(tb, "Volatility, funding"))
    hist = {r[0]: r for r in (find(tb, "Historical context") or {"rows": []})["rows"]}
    exps = rows_as_dicts(find(tb, "Expiries side by side"))
    roll = rows_as_dicts(find(tb, "Gamma roll-off"))
    skew = rows_as_dicts(find(tb, "Skew term structure"))
    basis = rows_as_dicts(find(tb, "Futures basis"))
    keyt = find(tb, "Key levels")
    key = {r[0]: r for r in (keyt["rows"] if keyt else [])}
    front = (keyt["title"].split(",")[-1].strip() if keyt else "")
    blocks_b = rows_as_dicts(find(tb, "Blocks"))
    prints = rows_as_dicts(find(tb, "Large prints"))
    taker = rows_as_dicts(find(ta, "Taker buy/sell ratio chart", first_col="Day (UTC)"))
    oi = rows_as_dicts(find(ta, cls="open-interest"))
    bars = rows_as_dicts(find(ta, cls="bar-expiries"))
    blocks_a = rows_as_dicts(find(ta, cls="block-structures"))
    pmap = rows_as_dicts(find(ta, cls="price-map"))
    scen = rows_as_dicts(find(ta, cls="scenarios"))
    ages = rows_as_dicts(find(ta, "Data ages"))

    spot = num(mm.get("Spot")) or num(dict(B["facts"]).get("Spot"))
    if spot is None:
        raise ValueError("the spot price is not in the on-chain dashboard (Market metrics table); the overview cannot be built")

    # ---- KPI values
    dvol = mm.get("DVOL", vol.get("DVOL", "—"))
    dvol_row = hist.get("DVOL")
    dvol_p = num(dvol_row[2]) if dvol_row and dvol_row[2].startswith("p") else None
    dvol_reg = dvol_row[5].lower() if dvol_row else ""
    ivp = num(mm.get("IV percentile (365 days)"))
    ivr = mm.get("IV rank (365 days)", "—").split(" (")[0]
    gex_values = [value for value in (num(r.get("Net GEX (signed)")) for r in roll) if value is not None]          # a missing value is not 0
    net_gex = sum(gex_values) if gex_values else None
    short_n = sum(1 for value in gex_values if value < 0)
    taker_days = [(num(r.get("Buy/sell ratio")), r.get("Day (UTC)", ""), r.get("Net", "")) for r in reversed(taker)]
    taker_days = [day for day in taker_days if day[0] is not None]          # days without a ratio are left out, not drawn as 0
    tk = [ratio for ratio, _, _ in taker_days]
    tk_days = [day for _, day, _ in taker_days]
    tk_net = [net for _, _, net in taker_days]
    perp = num(vol.get("Perpetual open interest"))
    basis_vals = sorted(v for v in (num(r.get("Annualized premium")) for r in basis) if v is not None and v > 0)
    basis_med = basis_vals[len(basis_vals) // 2] if basis_vals else None
    vrp = vol.get("Variance risk premium", "")
    vrp_v = vrp.split(" ")[0] if vrp else "—"
    vrp_reg = (re.search(r"\(([A-Z ]+)\)", vrp) or [None, ""])[1].lower()
    fund8 = mm.get("Funding, 8 hours", "—")
    fund_row = hist.get("Funding (8h)")
    rv = [num(x) for x in vol.get("Realized vol, 10 / 20 / 30 days", "").split("/")]
    cone = [num(x) for x in vol.get("Vol cone percentile, 10 / 20 / 30 days", "").split("/")]
    corr_label, corr = next(((k.replace(" price", ""), v) for k, v in vol.items() if k.startswith("Correlation with ")), ("", ""))

    tiles = [
        tile("DVOL", dvol, f"p{dvol_p:.0f} · 30d · {dvol_reg}" if dvol_p is not None else "30d percentile not computed", "warn" if (dvol_p or 0) >= 90 else "", meter_mini((dvol_p or 0) / 100)),
        tile("IV rank 365d", ivr, f"IV percentile {mm.get('IV percentile (365 days)', '—')}", "", meter_mini((num(ivr) or 0) / 100)),
        tile("Net GEX · all expiries", fmt_usd_short(net_gex), f"USD · {short_n} of {len(gex_values)} expiries short gamma", "neg" if net_gex < 0 else "pos") if net_gex is not None else tile("Net GEX · all expiries", "not computed", ""),
        tile("Taker buy/sell", f"{tk[-1]:.3f}" if tk else "—", f"{tk_days[-1]} · {'sellers' if tk and tk[-1] < 1 else 'buyers'} lead" if tk else "", "neg" if tk and tk[-1] < 1 else "pos",
             spark(tk, 92, 30, "neg" if tk and tk[-1] < 1 else "pos", "tsp", [f"{d}: {v:.3f}, net {n}" for d, v, n in zip(tk_days, tk, tk_net)]) if tk else ""),
        tile("Perpetual OI", fmt_usd_short(perp) if perp else "—", "USD · Binance"),
        tile("Futures basis", f"{basis_med:.1f}%" if basis_med is not None else "—", "median annualized premium"),
        tile("Variance risk premium", vrp_v, f"vol points · {vrp_reg}" if vrp_reg else "vol points"),
        tile("Funding 8h", fund8, f"{fund_row[2]} · 30d · {fund_row[5].lower()}" if fund_row else ""),
    ]

    # ---- price map candidates
    cands = {}

    def add(price, label, src, cat):
        if price is None or not spot or abs(price / spot - 1) > 0.20:
            return
        key_ = round(price)
        cands.setdefault(key_, []).append((label, src, cat))

    support = None
    for r in pmap:
        price_txt = r.get("Price", "").strip()
        p = num(price_txt) if re.fullmatch(r"[\d,]+(\.\d+)?", price_txt) else None
        role = r.get("Role", "")
        if p and "spot" not in role.lower():
            if p < spot and support is None:
                support = p
            add(p, "Decision " + ("support" if p < spot else "resistance"), f"weight {r.get('Weight', '')}", "dec")
    zg = num(key.get("Zero gamma level (re-priced)", [None, None])[1]) if key else None
    if zg:
        add(zg, "Zero gamma", f"{front} · re-priced", "deal")
    for r in sorted(exps, key=lambda r: -(num(r.get("Open interest (coins)")) or 0))[:3]:
        e = r.get("Expiry", "")
        add(num(r.get("Call resistance")), "Call resistance", e, "deal")
        add(num(r.get("Put support")), "Put support", e, "deal")
        add(num(r.get("Max pain")), "Max pain", e, "pain")
    for r in bars[:5]:
        carried = (r.get("Carried by expiry", "").split(",")[0]).strip()
        add(num(r.get("Strike")), f"{r.get('Type', '')} {r.get('Taker side', '')} {r.get('Notional', '')}", carried, "flow")
    catname = {"dec": "Decision", "deal": "Dealer", "pain": "Max pain", "flow": "Flow"}
    ladder_rows = []
    for p in sorted(cands, reverse=True):
        items = cands[p]
        main = items[0]
        subs = " · ".join(dict.fromkeys(f"{l} {s}".strip() if i else s for i, (l, s, c) in enumerate(items)))
        cats = "".join(f'<span class="cat cat-{c}">{catname[c]}</span>' for c in dict.fromkeys(c for _, _, c in items))
        ladder_rows.append((p, f'<div class="lv {"above" if p > spot else "below"}"><span class="lv-p">{fmt_int(p)}</span>'
                               f'<span class="lv-l"><span class="lv-t">{esc(main[0])}{cats}</span><span class="lv-s">{esc(subs)}</span></span>'
                               f'<span class="lv-d">{pct_from(p, spot)}</span></div>'))
    spot_row = (spot, f'<div class="lv spot"><span class="lv-p">{spot:,.0f}</span><span class="lv-l"><span class="lv-t">Spot · Deribit index</span></span><span class="lv-d">0.0%</span></div>')
    ladder_rows.append(spot_row)
    ladder_rows.sort(key=lambda x: -x[0])
    ladder = '<div class="ladder">' + "".join(h for _, h in ladder_rows[:14]) + "</div>"

    # ---- gamma roll-off
    mx = max((abs(num(r.get("Net GEX (signed)")) or 0) for r in roll), default=1) or 1
    top = max(roll, key=lambda r: num(r.get("Share of gross gamma")) or 0) if roll else {}
    grows = ""
    for r in roll:
        g = num(r.get("Net GEX (signed)")) or 0
        w = abs(g) / mx * 50
        side = "pos" if g > 0 else "neg"
        pos = f"left:50%;width:{w:.2f}%" if g > 0 else f"right:50%;width:{w:.2f}%"
        grows += (f'<div class="gx" tabindex="0" data-tip="{esc(r.get("Expiry"))} · {esc(r.get("Days to expiry"))} days · net GEX {esc(minus(r.get("Net GEX (signed)")))} · {esc(r.get("Share of gross gamma"))} of gross gamma">'
                  f'<span class="gx-e">{esc(r.get("Expiry"))}</span><span class="gx-track"><span class="gx-zero"></span><span class="gx-bar {side}" style="{pos}"></span></span>'
                  f'<span class="gx-v">{fmt_usd_short(g)}</span></div>')
    gamma = (f'<div class="gamma">{grows}</div><div class="axis-note"><span>◀ short gamma</span><span>USD, assumed dealer</span><span>long gamma ▶</span></div>'
             f'<p class="note-s">{short_n} of {len(gex_values)} expiries short gamma · {esc(top.get("Expiry", ""))} carries {esc(top.get("Share of gross gamma", ""))} of gross gamma.</p>')

    # ---- IV term structure (log days on x)
    W, Hh, pad = 420, 150, 10
    pts = [(num(r.get("Days")), num(r.get("ATM IV")), r) for r in skew if num(r.get("Days")) is not None and num(r.get("ATM IV")) is not None]
    ivsvg = ""
    if pts:
        lo = math.floor(min(v for _, v, _ in pts) / 5) * 5
        hi = math.ceil(max(v for _, v, _ in pts) / 5) * 5
        dl = [math.log(d + 0.5) for d, _, _ in pts]
        xs = [pad + (x - min(dl)) / ((max(dl) - min(dl)) or 1) * (W - 2 * pad - 30) for x in dl]
        ys = [Hh - pad - (v - lo) / ((hi - lo) or 1) * (Hh - 2 * pad) for _, v, _ in pts]
        grid = ""
        for gv in range(int(lo), int(hi) + 1, 5):
            y = Hh - pad - (gv - lo) / ((hi - lo) or 1) * (Hh - 2 * pad)
            grid += f'<line class="grid" x1="0" x2="{W}" y1="{y:.1f}" y2="{y:.1f}"/><text class="tick" x="{W - 2}" y="{y - 3:.1f}" text-anchor="end">{gv}%</text>'
        poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
        area = f'<polygon class="iv-area" points="{xs[0]:.1f},{Hh} {poly} {xs[-1]:.1f},{Hh}"/>'
        dots = "".join(f'<circle class="iv-dot" cx="{x:.1f}" cy="{y:.1f}" r="3.2" tabindex="0" data-tip="{esc(r.get("Expiry"))} · {esc(r.get("Days"))} days · ATM IV {esc(r.get("ATM IV"))}"/>' for x, y, (_, _, r) in zip(xs, ys, pts))
        lab = "".join(f'<text class="tick" x="{xs[i]:.1f}" y="{Hh + 12}" text-anchor="middle">{esc(pts[i][2].get("Expiry", "")[:5])}</text>' for i in sorted({0, len(pts) // 3, 2 * len(pts) // 3, len(pts) - 1}))
        ivsvg = (f'<svg class="ivchart" viewBox="0 0 {W} {Hh + 16}" role="img" aria-label="ATM implied volatility term structure">'
                 f'<defs><linearGradient id="ivg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" class="stop-acc" stop-opacity=".25"/><stop offset="1" class="stop-acc" stop-opacity="0"/></linearGradient></defs>'
                 f'{grid}{area}<polyline class="iv-line" points="{poly}"/>{dots}{lab}</svg>')
    meters = ""
    for lbl, v, c in zip(("RV 10d", "RV 20d", "RV 30d"), rv, cone):
        if v is None:
            continue
        meters += (f'<div class="meter"><div class="m-top"><span>{lbl}</span><b>{v:.1f}%</b></div><span class="m-track"><span style="width:{(c or 0):.0f}%"></span></span>'
                   f'<span class="m-note">vol cone p{c:.0f}</span></div>' if c is not None else "")
    volc = ivsvg + f'<div class="meters">{meters}</div>' + (f'<p class="note-s">{esc(corr_label)} {esc(corr)}</p>' if corr else "")

    # ---- decision
    verdict = re.search(r'<p class="verdict">(.*?)</p>', A["raw"], re.S)
    vtxt = re.sub(r"<mark>(.*?)</mark>", r"[[\1]]", verdict.group(1)) if verdict else ""
    vtxt = esc(re.sub("<[^>]+>", "", vtxt)).replace("[[", "<mark>").replace("]]", "</mark>")
    vfail = re.search(r'<p class="verdict-failed">(.*?)</p>', A["raw"], re.S)
    sc_rows = ""
    for r in scen[:4]:
        sc = num(r.get("Evidence score")) or 0
        sc_rows += (f'<div class="sc"><div class="sc-top"><span class="sc-n">{esc(r.get("Scenario", ""))}</span><span class="sc-c conf-{esc(r.get("Confidence", "").lower())}">{esc(r.get("Confidence", ""))}</span></div>'
                    f'<div class="sc-bar"><span style="width:{sc:.0f}%"></span></div><span class="sc-v">evidence score {sc:.1f} / 100</span></div>')
    pm_rows = "".join(f'<div class="pm"><span>{esc(r.get("Role", ""))}</span><b>{esc(r.get("Price", ""))}</b><span>{esc(r.get("From the spot", ""))}</span></div>' for r in pmap)
    upside_unmapped = bool(vfail and "upside is unmapped" in vfail.group(1))
    conf = scen[0].get("Confidence", "") if scen else ""
    decision = (f'<p class="verdict-s">{vtxt}</p>' + (f'<p class="warn-line">{esc(re.sub("<[^>]+>", "", vfail.group(1)))}</p>' if vfail else "")
                + f'<div class="scens">{sc_rows}</div><div class="pmap">{pm_rows}</div>')

    # ---- skew heatmap
    heat = ""
    for r in skew:
        v = num(r.get("25d RR"))
        if v is None:
            continue
        a = min(1, abs(v) / 12)
        heat += (f'<div class="hc" tabindex="0" style="--a:{0.10 + a * 0.62:.2f}" data-strong="{1 if a > 0.62 else 0}" data-tip="{esc(r.get("Expiry"))} · 25Δ RR {esc(r.get("25d RR"))} · {esc(r.get("RR 30d percentile"))} · {esc(r.get("RR regime"))} · 25Δ BF {esc(r.get("25d BF"))}">'
                 f'<b>{minus(f"{v:.1f}")}</b><span>{esc(r.get("Expiry", "")[:5])}</span><i>{esc(r.get("RR 30d percentile", ""))}</i></div>')
    rich = min(skew, key=lambda r: num(r.get("25d RR")) or 0) if skew else {}
    heatc = (f'<div class="heat" style="--n:{len(skew)}">{heat}</div><div class="axis-note"><span>25Δ risk reversal, vol points · 30-day percentile</span>'
             f'<span>Puts richest: {esc(rich.get("Expiry", ""))} ({esc(minus(rich.get("25d RR", "")))})</span></div>')

    # ---- donut
    tot = sum(num(r.get("Notional")) or 0 for r in oi)
    segs = [(r.get("Expiry"), num(r.get("Share")) or 0) for r in oi[:5]]
    other = max(0.0, 100 - sum(v for _, v in segs))
    if len(oi) > 5:
        segs.append((f"Other {len(oi) - 5}", other))
    C, off, circ, leg = 2 * math.pi * 42, 0.0, "", ""
    for i, (n, v) in enumerate(segs):
        Ln = C * v / 100
        circ += f'<circle class="d{i}" cx="60" cy="60" r="42" stroke-dasharray="{max(0, Ln - 1.2):.2f} {C:.2f}" stroke-dashoffset="{-off:.2f}" transform="rotate(-90 60 60)"><title>{esc(n)} {v:.1f}%</title></circle>'
        off += Ln
        leg += f'<div class="lg"><span class="sw d{i}"></span><span>{esc(n)}</span><b>{v:.1f}%</b></div>'
    donut = (f'<div class="donut"><svg viewBox="0 0 120 120" width="140" height="140" role="img" aria-label="Open interest share by expiry">'
             f'<circle class="d-track" cx="60" cy="60" r="42"/>{circ}<text class="d-big" x="60" y="58" text-anchor="middle">{fmt_usd_short(tot)}</text>'
             f'<text class="d-sub" x="60" y="72" text-anchor="middle">USD notional</text></svg><div class="legend">{leg}</div></div>')

    # ---- expiries table
    oi_by = {r.get("Expiry"): r for r in oi}
    maxoi = max((num(r.get("Open interest (coins)")) or 0 for r in exps), default=1) or 1
    trs = ""
    for r in exps:
        e = r.get("Expiry", "")
        o = num(r.get("Open interest (coins)")) or 0
        reg = r.get("Gamma regime", "")
        days = oi_by.get(e, {}).get("Days left", "")
        trs += (f'<tr><th scope="row"><button type="button" class="jump-exp" data-exp="{esc(e)}">{esc(e)}</button></th><td class="num">{esc(days)}</td><td class="num">{esc(r.get("Max pain", ""))}</td>'
                f'<td class="num">{esc(r.get("Call resistance", ""))}</td><td class="num">{esc(r.get("Put support", ""))}</td>'
                f'<td><span class="reg reg-{"neg" if reg.startswith("NEG") else "pos"}">{esc(reg.title())}</span></td><td class="num">{esc(r.get("Put/call (OI)", ""))}</td>'
                f'<td class="num oi-cell"><span class="oi-track"><span style="width:{o / maxoi * 100:.1f}%"></span></span>{esc(r.get("Open interest (coins)", ""))}</td>'
                f'<td class="num">{esc(r.get("ATM IV", ""))}</td></tr>')
    exptab = ('<div class="table-wrap"><table class="ov-table"><thead><tr><th scope="col">Expiry</th><th class="num" scope="col">Days</th><th class="num" scope="col">Max pain</th>'
              '<th class="num" scope="col">Call res.</th><th class="num" scope="col">Put sup.</th><th scope="col">Gamma</th><th class="num" scope="col">P/C OI</th>'
              f'<th class="num" scope="col">Open interest (coins)</th><th class="num" scope="col">ATM IV</th></tr></thead><tbody>{trs}</tbody></table></div>')

    # ---- blocks & prints
    br = ""
    for r in blocks_a[:5]:
        br += (f'<div class="tr-row"><span class="tr-a">{esc(r.get("Block", ""))}</span><span class="tr-b">{esc(r.get("What it is", ""))} · {esc(r.get("Expiries", ""))}</span>'
               f'<b class="tr-c">{esc(r.get("Notional", "").replace(" USD", ""))}</b></div>')
    pr = ""
    for r in prints[:5]:
        d = r.get("Direction", "")
        pr += (f'<div class="tr-row"><span class="tr-a">{esc(r.get("Instrument", "").replace(f"{currency}-", ""))}</span><span class="tr-b"><span class="dir dir-{esc(d)}">{esc(d)}</span> {esc(r.get("Size", ""))} · IV {esc(r.get("IV", ""))}</span>'
               f'<b class="tr-c">{fmt_usd_short(num(r.get("Notional (USD)")) or 0)}</b></div>')
    trades = (f'<div class="sub-h">Largest blocks · 24h</div>{br}<div class="sub-h">Large screen prints</div>{pr}')

    # ---- data health
    ag = ""
    for r in ages:
        a = num(r.get(next((k for k in r if k.startswith("Age")), ""), "")) or 0
        st = "ok" if a < 1 else ("warn" if a < 3 else "bad")
        ag += f'<div class="age"><span class="dot dot-{st}"></span><span>{esc(r.get("Data", ""))}</span><b>{esc(r.get(next((k for k in r if k.startswith("Age")), ""), ""))}</b></div>'
    health = f'<div class="ages">{ag}</div>'

    # ---- hero
    sup = support or (max((p for p in cands if p < spot), default=None))
    chips = [f'<span class="chip chip-live">● Chain {esc(dict(A["facts"]).get("Chain snapshot", "")[11:16])} UTC</span>',
             ]
    if net_gex is not None:
        chips.append(f'<span class="chip chip-{"neg" if net_gex < 0 else "pos"}">{"Short" if net_gex < 0 else "Long"} gamma</span>')
    if upside_unmapped:
        chips.append('<span class="chip chip-warn">Upside unmapped</span>')
    if conf:
        chips.append(f'<span class="chip">Confidence · {esc(conf)}</span>')
    line = []
    if sup:
        line.append(f"Support {fmt_int(sup)} · {pct_from(sup, spot).lstrip('−+')} below")
    if zg:
        line.append(f"zero gamma {fmt_int(zg)} · {pct_from(zg, spot).lstrip('−+')} {'above' if zg > spot else 'below'}")
    whole, frac = f"{spot:,.2f}".split(".")
    hero = (f'<section class="hero"><div class="hero-sheen"></div><div class="hero-chrome"></div><div class="hero-in">'
            f'<div class="hero-main"><div class="chips">{"".join(chips)}</div><span class="hero-k">Spot · Deribit index · USD</span>'
            f'<span class="hero-v">{whole}<span>.{frac}</span></span><span class="hero-s">{" &nbsp;·&nbsp; ".join(line)}</span></div>'
            f'<div class="hero-side">{gauge((dvol_p or 0) / 100 if dvol_p is not None else None, f"p{dvol_p:.0f}" if dvol_p is not None else "", f"DVOL {dvol}", "30-day percentile", "g1")}'
            f'{gauge((ivp or 0) / 100 if ivp is not None else None, f"{ivp:.0f}%" if ivp is not None else "", "IV percentile", "365-day", "g2")}'
            f'<div class="hero-spark"><span class="hero-k">Taker buy/sell · {len(tk)}d</span>{spark(tk, 190, 58, "neg" if tk and tk[-1] < 1 else "pos", "hsp", [f"{d}: {v:.3f}, net {n}" for d, v, n in zip(tk_days, tk, tk_net)]) if tk else ""}'
            f'<span class="hero-mono">{f"{tk[-1]:.3f}" if tk else ""} <span>· {esc(minus(tk_net[-1])) if tk_net else ""} net, {esc(tk_days[-1][5:] if tk_days else "")}</span></span></div></div></div></section>')

    return f'''{hero}
<div class="tiles">{"".join(tiles)}</div>
<div class="grid3">
{card("Price map", ladder, "map", "levels within 20% of spot", "levels")}
{card("Gamma roll-off", gamma, "bars", "net GEX by expiry", "market-wide")}
<div class="stack">{card("Volatility", volc, "wave", "ATM IV term · realized", "market-wide")}{card("Decision", decision, "flag", "evidence engine", "decision")}</div>
</div>
{card("Skew heatmap", heatc, "grid", f"{len(skew)} expiries", "market-wide")}
<div class="grid3">
{card("Open interest by expiry", donut, "pie", "chain snapshot", "option-flow")}
{card("Blocks & large prints", trades, "bolt", "last 24 hours", "option-flow")}
{card("Data health", health, "clock", "age at build time", "data-quality")}
</div>
{card("Expiries", exptab, "list", f"{len(exps)} listed · click an expiry for its charts", "expiries", "wide")}
'''


# --------------------------------------------------------------------------- page