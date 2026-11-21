(function () {
  "use strict";
  const DATA = JSON.parse(document.getElementById("report-data").textContent);
  const root = document.documentElement;
  const PLOT_CONFIG = { displaylogo: false, responsive: true, modeBarButtonsToRemove: ["lasso2d", "select2d", "toImage"] };

  /* ---------- theme palettes: report colours -> theme colours ---------- */
  const MAP = {
    light: { "#101114":"#0C0F14","#6A6F78":"#575E69","#A5AAB3":"#8A919C","#DADDE2":"#E3E6EB","#F0F1F3":"#F3F5F4","#FFFFFF":"#FFFFFF","#E4E6EA":"#E8EAEE","#F2F3F5":"#F3F5F4",
      "#2F7D55":"#0E6B50","#B8433A":"#C2410C","#9A7B0A":"#B45309","#2C6B4A":"#123524","#5FA77F":"#3FBF93","#9A5426":"#9A3412","#E0925F":"#F29A6B",
      "#2F5486":"#1E3A8A","#6E95C8":"#6B8FD6","#7A3557":"#6B2E5B","#C27A9E":"#C084B0","#C8742A":"#B45309","#C9A400":"#A16207","#D9822B":"#C2410C","#FFE14D":"rgba(14,107,80,.18)",
      "RGBA(106, 111, 120, 0.16)":"rgba(87,94,105,.14)" },
    dark: { "#101114":"#EEF5F1","#6A6F78":"#A3B8AE","#A5AAB3":"#7F978B","#DADDE2":"rgba(255,255,255,.12)","#F0F1F3":"rgba(255,255,255,.06)","#FFFFFF":"#12301F","#E4E6EA":"rgba(255,255,255,.10)","#F2F3F5":"#173D2D",
      "#2F7D55":"#3FBF93","#B8433A":"#FF9B6B","#9A7B0A":"#F2C46B","#2C6B4A":"#3FBF93","#5FA77F":"#9BE8CB","#9A5426":"#FF9B6B","#E0925F":"#FFC9A8",
      "#2F5486":"#7FA8FF","#6E95C8":"#BFD1FF","#7A3557":"#E39BCB","#C27A9E":"#F5CDE6","#C8742A":"#F2C46B","#C9A400":"#F2C46B","#D9822B":"#FF9B6B","#FFE14D":"rgba(63,191,147,.28)",
      "RGBA(106, 111, 120, 0.16)":"rgba(163,184,174,.16)" }
  };
  const RE = /#[0-9A-Fa-f]{6}\b|rgba\(106, 111, 120, 0\.16\)/g;
  const recolor = (s, t) => s.replace(RE, (m) => MAP[t][m.toUpperCase()] || m);
  const theme = () => (root.dataset.theme === "dark" ? "dark" : "light");
  const cssVar = (n) => getComputedStyle(root).getPropertyValue(n).trim();

  const cache = { light: {}, dark: {} };
  function themedFigure(key) {
    const t = theme();
    if (cache[t][key]) return cache[t][key];
    const src = DATA.figures[key];
    if (!src) return null;
    const fig = JSON.parse(recolor(JSON.stringify(src), t));
    const L = fig.layout || (fig.layout = {});
    const text = cssVar("--text"), muted = cssVar("--muted"), line = cssVar("--card-line");
    L.paper_bgcolor = "rgba(0,0,0,0)"; L.plot_bgcolor = "rgba(0,0,0,0)";
    L.font = Object.assign({}, L.font, { family: "Inter, system-ui, sans-serif", color: text });
    L.hoverlabel = Object.assign({}, L.hoverlabel, { bgcolor: cssVar("--card-solid"), bordercolor: line, font: Object.assign({}, (L.hoverlabel || {}).font, { family: "JetBrains Mono, monospace", color: text, size: 12 }) });
    L.modebar = { bgcolor: "rgba(0,0,0,0)", color: muted, activecolor: cssVar("--accent") };
    if (L.legend) L.legend = Object.assign({}, L.legend, { bgcolor: "rgba(0,0,0,0)" });
    Object.keys(L).forEach((k) => {
      if (/^[xy]axis\d*$/.test(k)) {
        const ax = L[k];
        ax.tickfont = Object.assign({ family: "JetBrains Mono, monospace", size: 11 }, ax.tickfont, { family: "JetBrains Mono, monospace" });
        if (ax.title && typeof ax.title === "object") ax.title.font = Object.assign({}, ax.title.font, { family: "Inter, system-ui, sans-serif" });
      }
    });
    cache[t][key] = fig;
    return fig;
  }

  const drawn = new Map();   // plot element -> figure key
  function plot(el, key) {
    const fig = themedFigure(key);
    if (!fig) { el.textContent = "Nothing was computed for this chart."; return; }
    if (!window.Plotly) { el.textContent = "The chart library is missing from this file, so the chart cannot be drawn."; return; }
    drawn.set(el, key);
    window.Plotly.react(el, fig.data, fig.layout, PLOT_CONFIG);
  }

  /* ---------- embedded pages (levels), themed ---------- */
  function themedPage(id) {
    const page = DATA.pages[id];
    if (page === undefined) return null;
    const t = theme();
    const vars = t === "dark"
      ? "--paper:transparent;--ink:#EEF5F1;--gray:#A3B8AE;--light:#7F978B;--rule:rgba(255,255,255,.14);--faint:rgba(255,255,255,.06);--highlight:rgba(63,191,147,.32);"
      : "--paper:transparent;--ink:#0C0F14;--gray:#575E69;--light:#8A919C;--rule:#E3E6EB;--faint:#F3F5F4;--highlight:rgba(14,107,80,.20);";
    const inject = '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&family=JetBrains+Mono:wght@400;600;700&display=swap">'
      + "<style>:root{" + vars + '--numerals:"JetBrains Mono",ui-monospace,monospace;--body:"Inter",system-ui,sans-serif;color-scheme:' + t + "}html,body{background:transparent!important}main{padding:8px 4px 16px!important}</style>";
    let s = recolor(page, t);
    s = s.includes("</head>") ? s.replace("</head>", inject + "</head>") : inject + s;
    return s;
  }
  const observers = new WeakMap();
  function fit(frame) {
    frame.addEventListener("load", () => {
      let d; try { d = frame.contentDocument; } catch (e) { d = null; }
      if (!d) return;
      const f = () => { frame.style.height = "0px"; frame.style.height = (d.documentElement.scrollHeight + 4) + "px"; };
      f();
      if (observers.has(frame)) observers.get(frame).disconnect();
      if (window.ResizeObserver && d.body) { const o = new ResizeObserver(f); o.observe(d.body); observers.set(frame, o); }
    });
  }
  function showPage(frame, id, force) {
    if (!force && frame.dataset.shownPage === id) return;
    const s = themedPage(id);
    if (s === null) return;
    if (!frame.dataset.fitting) { frame.dataset.fitting = "1"; fit(frame); }
    frame.dataset.shownPage = id;
    frame.srcdoc = s;
  }

  /* ---------- switchers ---------- */
  function drawSwitcher(el) {
    const cfg = DATA.switchers[el.dataset.switcher];
    if (!cfg) return;
    const key = cfg.controls.map((n) => cfg.selected[n]).join("|");
    const view = cfg.views[key];
    const caption = el.querySelector(".switcher-caption");
    const plotEl = el.querySelector(".switcher-plot");
    const image = el.querySelector(".switcher-image");
    const frame = el.querySelector(".switcher-frame");
    if (frame) frame.hidden = true;
    if (!view) { if (caption) caption.textContent = "Nothing was computed for this choice."; if (plotEl) plotEl.hidden = true; if (image) image.hidden = true; return; }
    if (caption) caption.innerHTML = view.caption || "";   /* built and escaped by the report generator */
    if (view.page) {
      if (plotEl) plotEl.hidden = true; if (image) image.hidden = true;
      if (frame) { frame.hidden = false; showPage(frame, view.page); }
    } else if (view.figure) {
      if (image) image.hidden = true;
      if (plotEl) { plotEl.hidden = false; plot(plotEl, view.figure); }
    } else if (view.image && image) {
      if (plotEl) plotEl.hidden = true; image.hidden = false; image.src = view.image; image.alt = view.alt || "";
    }
  }
  function setChoice(el, name, value) {
    const cfg = DATA.switchers[el.dataset.switcher];
    if (!cfg) return false;
    const buttons = Array.from(el.querySelectorAll(`button[data-control="${name}"]`));
    const select = el.querySelector(`select[data-control="${name}"]`);
    if (select) { if (!Array.from(select.options).some((o) => o.value === value)) return false; select.value = value; }
    else if (buttons.some((b) => b.dataset.value === value)) buttons.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.value === value)));
    else return false;
    cfg.selected[name] = value;
    return true;
  }
  function bindSwitcher(el) {
    const cfg = DATA.switchers[el.dataset.switcher];
    if (!cfg) return;
    el.querySelectorAll("button[data-control]").forEach((b) => b.addEventListener("click", () => { setChoice(el, b.dataset.control, b.dataset.value); drawSwitcher(el); }));
    el.querySelectorAll("select[data-control]").forEach((s) => s.addEventListener("change", () => { cfg.selected[s.dataset.control] = s.value; drawSwitcher(el); }));
  }
  document.addEventListener("click", (ev) => {
    const tr = ev.target.closest && ev.target.closest("[data-switcher-target]");
    if (!tr) return;
    const el = document.querySelector(`.switcher[data-switcher="${tr.dataset.switcherTarget}"]`);
    if (!el || !setChoice(el, tr.dataset.control, tr.dataset.value)) return;
    drawSwitcher(el);
    el.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  });

  /* ---------- report panels become cards: one card per h3 section ---------- */
  function cardify(panel) {
    const kids = Array.from(panel.childNodes);
    const intro = document.createElement("div"); intro.className = "intro";
    let current = intro;
    const out = [intro];
    kids.forEach((n) => {
      if (n.nodeType === 1 && n.tagName === "H3") { current = document.createElement("div"); current.className = "sec"; out.push(current); }
      current.appendChild(n);
    });
    panel.textContent = "";
    out.forEach((c) => { if (c.childNodes.length && (c.textContent.trim() || c.querySelector("table,svg,iframe,.switcher"))) panel.appendChild(c); });
  }
  document.querySelectorAll(".panel.src").forEach(cardify);

  /* ---------- tabs ---------- */
  const tabs = Array.from(document.querySelectorAll('[role="tab"]'));
  const shown = new Set();
  function showPanel(panel) {
    // charts that sit in a panel by themselves (not in a switcher, like the payoff charts of the Strategy tab): drawn whenever they are stale, the first time too
    panel.querySelectorAll(".switcher-plot[data-stale]").forEach((el) => { const k = el.dataset.stale; delete el.dataset.stale; plot(el, k); });
    if (!shown.has(panel.id)) {
      shown.add(panel.id);
      panel.querySelectorAll(".switcher").forEach(drawSwitcher);
      panel.querySelectorAll("iframe[data-page]").forEach((f) => showPage(f, f.dataset.page));
    } else {
      panel.querySelectorAll("iframe[data-stale]").forEach((f) => { delete f.dataset.stale; showPage(f, f.dataset.shownPage, true); });
      if (window.Plotly) panel.querySelectorAll(".js-plotly-plot").forEach((p) => window.Plotly.Plots.resize(p));
    }
  }
  function select(tab, focus, scroll) {
    tabs.forEach((o) => {
      const on = o === tab;
      o.setAttribute("aria-selected", String(on)); o.tabIndex = on ? 0 : -1;
      document.getElementById(o.getAttribute("aria-controls")).hidden = !on;
    });
    if (focus) tab.focus();
    tab.scrollIntoView({ block: "nearest", inline: "nearest" });
    try { history.replaceState(null, "", "#" + tab.dataset.key); } catch (e) { /* no history */ }
    showPanel(document.getElementById(tab.getAttribute("aria-controls")));
    if (scroll) window.scrollTo({ top: 0, behavior: "auto" });
  }
  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => select(tab, false, true));
    tab.addEventListener("keydown", (e) => {
      let t = null;
      if (e.key === "ArrowRight") t = tabs[(i + 1) % tabs.length];
      else if (e.key === "ArrowLeft") t = tabs[(i - 1 + tabs.length) % tabs.length];
      else if (e.key === "Home") t = tabs[0]; else if (e.key === "End") t = tabs[tabs.length - 1];
      if (t) { e.preventDefault(); select(t, true, false); }
    });
  });
  const byKey = (k) => tabs.find((t) => t.dataset.key === k);
  document.addEventListener("click", (e) => {
    const g = e.target.closest && e.target.closest("[data-goto]");
    if (g && byKey(g.dataset.goto)) select(byKey(g.dataset.goto), false, true);
    const x = e.target.closest && e.target.closest(".jump-exp");
    if (x && byKey("expiries")) {
      select(byKey("expiries"), false, true);
      const el = document.querySelector('#panel-expiries .switcher');
      if (el && setChoice(el, "expiry", x.dataset.exp)) drawSwitcher(el);
    }
  });
  window.addEventListener("hashchange", () => { const t = tabs.find((t) => "#" + t.dataset.key === location.hash); if (t) select(t, false, false); });

  /* ---------- theme toggle ---------- */
  const btn = document.getElementById("theme-btn");
  function syncBtn() {
    const dark = theme() === "dark";
    btn.setAttribute("aria-pressed", String(dark));
    btn.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
    btn.querySelector(".theme-label").textContent = dark ? "Light" : "Dark";
  }
  btn.addEventListener("click", () => {
    root.dataset.theme = theme() === "dark" ? "light" : "dark";
    try { localStorage.setItem("btcdesk-theme", root.dataset.theme); } catch (e) { /* storage unavailable */ }
    syncBtn();
    Array.from(drawn.entries()).forEach(([el, key]) => {
      if (el.closest("[hidden]")) { drawn.delete(el); el.dataset.stale = key; } else plot(el, key);
    });
    document.querySelectorAll("iframe.page-frame").forEach((f) => {
      if (!f.dataset.shownPage) return;
      if (f.closest("[hidden]")) f.dataset.stale = "1"; else showPage(f, f.dataset.shownPage, true);
    });
  });
  syncBtn();

  /* ---------- tooltip ---------- */
  const tip = document.getElementById("tip");
  function showTip(el, x, y) {
    const s = el.getAttribute("data-tip"); if (!s) return;
    tip.textContent = s; tip.hidden = false;
    const r = tip.getBoundingClientRect();
    let left = x + 14, top = y + 14;
    if (left + r.width > innerWidth - 8) left = x - r.width - 14;
    if (top + r.height > innerHeight - 8) top = y - r.height - 14;
    tip.style.left = Math.max(8, left) + "px"; tip.style.top = Math.max(8, top) + "px";
  }
  document.addEventListener("pointermove", (e) => { const el = e.target.closest && e.target.closest("[data-tip]"); if (el) showTip(el, e.clientX, e.clientY); else tip.hidden = true; });
  document.addEventListener("focusin", (e) => { const el = e.target.closest && e.target.closest("[data-tip]"); if (el) { const r = el.getBoundingClientRect(); showTip(el, r.left + r.width / 2, r.bottom); } });
  document.addEventListener("focusout", () => { tip.hidden = true; });
  addEventListener("scroll", () => { tip.hidden = true; }, { passive: true });

  document.querySelectorAll(".switcher").forEach(bindSwitcher);
  const start = tabs.find((t) => "#" + t.dataset.key === location.hash) || tabs[0];
  select(start, false, false);
})();
