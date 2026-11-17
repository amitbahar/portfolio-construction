// UI: controls, URL state, trial counting, and rendering of the engine's results.

const STRATS = ["Equal weight", "Min variance", "Risk parity", "Max Sharpe", "HRP"];
const BENCH = ["SPY buy & hold", "60/40"];
const CODES = { "Equal weight": "EW", "Min variance": "MV", "Risk parity": "RP", "Max Sharpe": "MS", "HRP": "HRP" };
const FROM_CODE = Object.fromEntries(Object.entries(CODES).map(([k, v]) => [v, k]));
const BENCH_TICKERS = ["SPY", "IEF"];
const DEFAULT = {
  assets: ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "LQD", "GLD", "VNQ"],
  strategies: [...STRATS], lookback: 252, freq: "ME", cost_bps: 10, cap: 0.4, start: null, end: null,
};
const PRESETS = {
  "Original 10": DEFAULT.assets,
  "Stocks · bonds · gold": ["SPY", "EFA", "EEM", "IEF", "TLT", "GLD"],
  "Global multi-asset": ["VTI", "EFA", "EEM", "AGG", "TIP", "LQD", "GLD", "DBC", "VNQ"],
  "US sectors": ["XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLU", "XLB"],
  "US factors": ["IWD", "IWF", "USMV", "MTUM", "QUAL"],
  "Israel (TASE)": ["KSM-F74.TA", "HRL-F11.TA", "HRL-F15.TA", "HRL-F300.TA", "HRL-F303.TA", "TCH-F25.TA"],
  "Israel + US": ["KSM-F74.TA", "HRL-F300.TA", "TCH-F25.TA", "SPY", "IEF", "GLD"],
};
const GROUP_NOTES = {
  "Israel (TASE)": "Tel Aviv-listed funds, converted from shekels to US dollars at the Bank of Israel representative rate, "
    + "so returns include currency moves. Yahoo's history for these funds starts in August 2019, so any backtest that "
    + "includes them starts in 2020. Tel Aviv closes before New York, which makes daily correlations with US funds look "
    + "too low; when you mix exchanges, the optimizers estimate risk from weekly returns instead.",
};
const shortTicker = (t) => t.replace(/\.TA$/, "");
const ASSET_COLORS = ["#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2", "#edc948", "#b07aa1", "#ff9da7",
  "#9c755f", "#bab0ac", "#1f77b4", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#17becf", "#bcbd22",
  "#aec7e8", "#ffbb78"];

const $ = (id) => document.getElementById(id);
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const stratColor = (k) => ({
  "Equal weight": css("--ink"), "Min variance": "#3b82c4", "Risk parity": "#12a27a", "Max Sharpe": "#e0692f",
  "HRP": "#9061d6", "SPY buy & hold": "#9a9384", "60/40": "#c39b3a",
}[k]);

const state = { meta: null, cfg: structuredClone(DEFAULT), result: null, basis: "net", ready: false, running: false, runId: 0 };

// ---------- formatting ----------
const pct = (x, d = 1) => (x == null ? "—" : `${(x * 100).toFixed(d)}%`);
const spct = (x, d = 1) => (x == null ? "—" : `${x >= 0 ? "+" : "−"}${Math.abs(x * 100).toFixed(d)}%`);
const num = (x, d = 2) => (x == null ? "—" : x.toFixed(d));
const snum = (x, d = 2) => (x == null ? "—" : `${x >= 0 ? "+" : "−"}${Math.abs(x).toFixed(d)}`);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const sw = (k) => `<span class="swatch" style="background:${stratColor(k)}"></span>`;

// ---------- URL state ----------
function cfgToHash(c) {
  const p = new URLSearchParams();
  p.set("a", c.assets.join(","));
  p.set("s", c.strategies.map((s) => CODES[s]).join(","));
  p.set("lb", c.lookback);
  p.set("f", c.freq === "QE" ? "q" : "m");
  p.set("c", c.cost_bps);
  p.set("cap", Math.round(c.cap * 100));
  if (c.start) p.set("from", c.start);
  if (c.end) p.set("to", c.end);
  return "#" + p.toString();
}

function hashToCfg(hash, available) {
  const p = new URLSearchParams(hash.replace(/^#/, ""));
  if (!p.has("a")) return null;
  const c = structuredClone(DEFAULT);
  c.assets = p.get("a").split(",").filter((t) => available.has(t));
  if (p.has("s")) c.strategies = ["Equal weight", ...p.get("s").split(",").map((x) => FROM_CODE[x]).filter((s) => s && s !== "Equal weight")];
  if ([126, 252, 504].includes(+p.get("lb"))) c.lookback = +p.get("lb");
  c.freq = p.get("f") === "q" ? "QE" : "ME";
  if (p.has("c") && isFinite(+p.get("c"))) c.cost_bps = Math.min(200, Math.max(0, +p.get("c")));
  if (p.has("cap") && isFinite(+p.get("cap"))) c.cap = Math.min(1, Math.max(0.05, +p.get("cap") / 100));
  const date = /^\d{4}-\d{2}-\d{2}$/;
  if (date.test(p.get("from") || "")) c.start = p.get("from");
  if (date.test(p.get("to") || "")) c.end = p.get("to");
  return c;
}

// ---------- trial bookkeeping (per tab) ----------
function loadTrials() {
  try { return JSON.parse(sessionStorage.getItem("pc-trials") || "{}"); } catch { return {}; }
}
function saveTrials(t) {
  try { sessionStorage.setItem("pc-trials", JSON.stringify(t)); } catch { /* storage unavailable: count stays in memory */ }
}
let trials = loadTrials();
function renderTrials() {
  const keys = Object.keys(trials);
  const n = new Set(keys.map((k) => k.split("|")[0])).size;
  $("n-configs").textContent = `${n} configuration${n === 1 ? "" : "s"}`;
  $("n-variants").textContent = `${keys.length} strategy variant${keys.length === 1 ? "" : "s"}`;
}
const configKey = (c) => JSON.stringify([[...c.assets].sort(), [...c.strategies].sort(), c.lookback, c.freq, c.cost_bps, c.cap, c.start, c.end]);

// ---------- controls ----------
function minCap(n) { return Math.min(100, Math.ceil(100 / n / 5) * 5); }

function buildControls() {
  const groups = {};
  for (const a of state.meta.assets) (groups[a.category] ||= []).push(a);
  $("asset-list").innerHTML = Object.entries(groups).map(([cat, list]) => `
    <div class="asset-group" data-cat="${esc(cat)}">
      <div class="group-head"><span>${esc(cat)}</span><button type="button" data-group="${esc(cat)}">toggle all</button></div>
      ${GROUP_NOTES[cat] ? `<details class="group-note"><summary>Prices in USD, data from 2019 · details</summary>${esc(GROUP_NOTES[cat])}</details>` : ""}
      ${list.map((a) => `
        <label class="asset" data-ticker="${a.ticker}" data-search="${esc((a.ticker + " " + a.name + " " + cat).toLowerCase())}">
          <input type="checkbox" value="${a.ticker}">
          <span class="tk" title="${esc(a.ticker)}">${esc(shortTicker(a.ticker))}</span><span class="nm" title="${esc(a.name)}">${esc(a.name)}</span>
          <span class="since" title="First price ${a.first}${a.coverage < 0.995 ? ` · ${(a.coverage * 100).toFixed(0)}% of trading days present; gaps are forward-filled` : ""}">${a.first.slice(0, 4)}${a.coverage < 0.995 ? "*" : ""}</span>
        </label>`).join("")}
    </div>`).join("");

  $("presets").innerHTML = Object.keys(PRESETS).map((k) => `<button type="button" class="chip" data-preset="${esc(k)}">${esc(k)}</button>`).join("");
  $("strategy-list").innerHTML = STRATS.map((s) => `
    <label class="${s === "Equal weight" ? "locked" : ""}"><input type="checkbox" value="${s}" ${s === "Equal weight" ? "checked disabled" : ""}> ${sw(s)}${s}</label>`).join("")
    + BENCH.map((b) => `<label class="locked"><input type="checkbox" checked disabled> ${sw(b)}${b} <span class="hint">benchmark</span></label>`).join("");

  const first = state.meta.assets.reduce((m, a) => (a.first < m ? a.first : m), "9999");
  for (const id of ["start", "end"]) { $(id).min = first; $(id).max = state.meta.data_through; }

  $("asset-list").addEventListener("change", () => {
    state.cfg.assets = [...document.querySelectorAll("#asset-list input:checked")].map((i) => i.value);
    syncCap(); onConfigChange();
  });
  $("asset-list").addEventListener("click", (e) => {
    const g = e.target.dataset?.group;
    if (!g) return;
    const boxes = [...document.querySelectorAll(`.asset-group[data-cat="${CSS.escape(g)}"] input`)];
    const allOn = boxes.every((b) => b.checked);
    boxes.forEach((b) => (b.checked = !allOn));
    $("asset-list").dispatchEvent(new Event("change"));
  });
  $("presets").addEventListener("click", (e) => {
    const p = e.target.dataset?.preset;
    if (!p) return;
    state.cfg.assets = PRESETS[p].filter((t) => state.meta.assets.some((a) => a.ticker === t));
    syncCap(); writeControls(); onConfigChange();
  });
  $("asset-filter").addEventListener("input", (e) => {
    const q = e.target.value.trim().toLowerCase();
    document.querySelectorAll(".asset").forEach((el) => (el.hidden = q && !el.dataset.search.includes(q)));
    document.querySelectorAll(".asset-group").forEach((g) => (g.hidden = ![...g.querySelectorAll(".asset")].some((a) => !a.hidden)));
  });
  $("strategy-list").addEventListener("change", () => {
    state.cfg.strategies = ["Equal weight", ...[...document.querySelectorAll("#strategy-list input:checked:not(:disabled)")].map((i) => i.value)];
    onConfigChange();
  });
  $("lookback").addEventListener("change", (e) => { state.cfg.lookback = +e.target.value; onConfigChange(); });
  $("freq").addEventListener("change", (e) => { state.cfg.freq = e.target.value; onConfigChange(); });
  $("cost").addEventListener("change", (e) => {
    const v = Math.min(200, Math.max(0, +e.target.value || 0));
    state.cfg.cost_bps = v; e.target.value = v; onConfigChange();
  });
  $("cap").addEventListener("input", (e) => { state.cfg.cap = +e.target.value / 100; $("cap-out").textContent = `${e.target.value}%`; onConfigChange(); });
  $("start").addEventListener("change", (e) => { state.cfg.start = e.target.value || null; onConfigChange(); });
  $("end").addEventListener("change", (e) => { state.cfg.end = e.target.value || null; onConfigChange(); });
  $("run").addEventListener("click", run);
  $("share").addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(location.href); $("share").textContent = "Copied"; }
    catch { $("share").textContent = "Copy from address bar"; }
    setTimeout(() => ($("share").textContent = "Copy link"), 1800);
  });
  document.querySelector(".info").addEventListener("click", (e) => {
    const h = $("trials-help"); h.hidden = !h.hidden; e.currentTarget.setAttribute("aria-expanded", String(!h.hidden));
  });
  document.querySelectorAll(".toggle button").forEach((b) => b.addEventListener("click", () => {
    state.basis = b.dataset.basis;
    document.querySelectorAll(".toggle button").forEach((x) => x.classList.toggle("on", x === b));
    renderMainTable();
  }));
}

function syncCap() {
  const n = Math.max(state.cfg.assets.length, 1);
  const lo = minCap(n);
  $("cap").min = lo;
  if (state.cfg.cap * 100 < lo) state.cfg.cap = lo / 100;
  $("cap").value = Math.round(state.cfg.cap * 100);
  $("cap-out").textContent = `${Math.round(state.cfg.cap * 100)}%`;
}

function writeControls() {
  const c = state.cfg;
  document.querySelectorAll("#asset-list input").forEach((i) => (i.checked = c.assets.includes(i.value)));
  document.querySelectorAll("#strategy-list input:not(:disabled)").forEach((i) => (i.checked = c.strategies.includes(i.value)));
  $("lookback").value = c.lookback;
  $("freq").value = c.freq;
  $("cost").value = c.cost_bps;
  $("start").value = c.start || "";
  $("end").value = c.end || "";
  syncCap();
}

function onConfigChange() {
  const c = state.cfg;
  history.replaceState(null, "", cfgToHash(c));
  $("asset-count").textContent = `${c.assets.length} selected`;
  // Which fund limits the common start date?
  const firsts = state.meta.assets.filter((a) => c.assets.includes(a.ticker) || BENCH_TICKERS.includes(a.ticker));
  const latest = firsts.reduce((m, a) => (a.first > m.first ? a : m), { first: "" });
  document.querySelectorAll(".asset").forEach((el) => el.classList.toggle("limiting", el.dataset.ticker === latest.ticker && c.assets.includes(latest.ticker)));
  if (latest.ticker) {
    const begin = new Date(latest.first);
    begin.setDate(begin.getDate() + Math.round(c.lookback * 365 / 252));
    $("start-hint").innerHTML = `All selected funds trade from <strong>${latest.first}</strong> (limited by ${shortTicker(latest.ticker)}${c.assets.includes(latest.ticker) ? "" : ", which the 60/40 benchmark needs"}). `
      + `After the ${c.lookback}-day lookback, the backtest can begin around ${begin.toISOString().slice(0, 7)}.`;
  }
  const err = validate(c) || (state.serverError?.key === configKey(c) ? state.serverError.text : null);
  $("form-error").hidden = !err;
  $("form-error").textContent = err || "";
  $("run").disabled = !state.ready || state.running || !!validate(c);
  if (state.ready && !state.running) $("run").textContent = "Run backtest";
}

function validate(c) {
  if (c.assets.length < 2) return "Pick at least 2 assets.";
  if (c.cap * c.assets.length < 1 - 1e-9) return `The cap must be at least ${minCap(c.assets.length)}% with ${c.assets.length} assets.`;
  if (c.start && c.end && c.start >= c.end) return "Start must be before end.";
  return null;
}

// ---------- worker ----------
const worker = new Worker("worker.js", { type: "module" });
worker.onmessage = (e) => {
  const m = e.data;
  if (m.type === "status") setStatus(m.text, m.pct);
  else if (m.type === "fatal") { setStatus(m.text, 0); $("run").textContent = "Unavailable"; }
  else if (m.type === "ready") {
    state.ready = true; setStatus("Ready.", 100);
    if (state.meta) { onConfigChange(); run(); }
  } else if (m.type === "progress" && m.id === state.runId) {
    const label = m.i < m.n ? `Running ${m.name} (${m.i + 1}/${m.n})…` : "Computing bootstrap intervals and statistics…";
    setStatus(label, Math.round(((m.i + 0.5) / (m.n + 1)) * 100));
  } else if (m.type === "result" && m.id === state.runId) {
    state.running = false;
    if (m.result.error) { $("status").hidden = true; state.serverError = { key: configKey(state.cfg), text: m.result.error }; }
    else {
      trials = m.result.trials; saveTrials(trials); renderTrials();
      state.result = m.result; $("status").hidden = true; render();
    }
    onConfigChange();
  }
};

function setStatus(text, p) {
  $("status").hidden = false;
  $("status-text").textContent = text;
  if (p != null) $("status-fill").style.width = `${p}%`;
}

function run() {
  if (!state.ready || state.running || validate(state.cfg)) return;
  state.running = true; state.runId += 1;
  $("run").disabled = true; $("run").textContent = "Running…";
  $("form-error").hidden = true;
  setStatus("Starting…", 2);
  worker.postMessage({ type: "run", id: state.runId, cfg: state.cfg, trials, key: configKey(state.cfg) });
}

// ---------- rendering ----------
function render() {
  $("report").hidden = false;
  renderVerdict();
  renderMainTable();
  renderCharts();
  renderSideTables();
}

function renderVerdict() {
  const r = state.result;
  const ew = r.table["Equal weight"].net;
  const others = r.strategies.filter((k) => k !== "Equal weight");
  const tried = STRATS.filter((k) => r.strategies.includes(k) && k !== "Equal weight");
  const adj = r.n_trials > 1;
  const lo = (k) => (adj && STRATS.includes(k) ? r.vs_ew[k].d_sharpe_ci_adj[0] : r.vs_ew[k].d_sharpe_ci[0]);
  const better = others.filter((k) => lo(k) > 0);
  const best = (tried.length ? tried : others).reduce((a, b) => (r.vs_ew[b].d_sharpe > r.vs_ew[a].d_sharpe ? b : a));
  const b = r.vs_ew[best];
  const fmtCI = (c) => `[${snum(c[0])}, ${snum(c[1])}]`;
  const points = [];

  const head = better.length
    ? `${better.join(", ")} beat equal weight on risk-adjusted return${adj ? `, even after adjusting for ${r.n_trials} variants tried` : ""}.`
    : "No strategy reliably beat equal weight on risk-adjusted return.";
  let txt = `Best optimized Sharpe: <strong>${best}</strong> (${num(r.table[best].net.sharpe)} vs ${num(ew.sharpe)} for equal weight). `
    + `The 95% interval for the difference, ${fmtCI(b.d_sharpe_ci)}, ${b.d_sharpe_ci[0] > 0 ? "excludes" : "includes"} zero`;
  if (adj && STRATS.includes(best)) {
    txt += `. Adjusted for the ${r.n_trials} strategy variants tried this session, it is ${fmtCI(b.d_sharpe_ci_adj)}`
      + (b.d_sharpe_ci[0] > 0 && b.d_sharpe_ci_adj[0] <= 0 ? ": the edge does not survive the search." : ".");
  } else txt += ".";
  points.push({ cls: lo(best) > 0 ? "good" : "", text: txt });

  if (STRATS.includes(best)) {
    const d = r.dsr[best];
    points.push({
      cls: d >= 0.95 ? "" : "warn",
      text: `Against cash rather than equal weight: ${best}'s deflated Sharpe is <strong>${pct(d, 0)}</strong>, so its excess return over T-bills `
        + (d >= 0.95 ? "is unlikely to be an artefact of trying many variants." : "could plausibly be an artefact of trying many variants."),
    });
  }
  const shallower = others.filter((k) => r.vs_ew[k].d_max_dd_ci[0] > 0);
  if (shallower.length) points.push({
    cls: "good",
    text: `Smaller worst drawdown than equal weight (${pct(ew.max_dd, 0)}), with 95% confidence: `
      + shallower.map((k) => `${k} (${pct(r.table[k].net.max_dd, 0)})`).join(", ") + ".",
  });
  const lowerRet = others.filter((k) => r.vs_ew[k].d_ann_return_ci[1] < 0);
  if (lowerRet.length) points.push({ cls: "bad", text: `Lower return than equal weight, with 95% confidence: ${lowerRet.join(", ")}.` });

  const h = r.halves.rows;
  const flips = others.filter((k) => Math.sign(h[k].first.sharpe - h["Equal weight"].first.sharpe) !== Math.sign(h[k].second.sharpe - h["Equal weight"].second.sharpe));
  if (flips.length) points.push({
    cls: "warn",
    text: `The ranking vs equal weight flips between halves (split ${r.halves.split}) for ${flips.join(", ")}. A study that ended at the midpoint would have reached a different conclusion.`,
  });
  const capped = tried.filter((k) => r.config.cap < 1 && r.table[k].net.avg_max_weight >= r.config.cap - 0.005);
  if (capped.length) points.push({ cls: "warn", text: `${capped.join(", ")} sit${capped.length === 1 ? "s" : ""} at the ${pct(r.config.cap, 0)} cap on average, so the cap is doing much of the work.` });
  const costKilled = tried.filter((k) => r.costs.rows[k]["0"].sharpe > r.costs.rows["Equal weight"]["0"].sharpe && r.table[k].net.sharpe <= ew.sharpe);
  if (costKilled.length) points.push({ cls: "bad", text: `${costKilled.join(", ")} beat${costKilled.length === 1 ? "s" : ""} equal weight before costs but not after ${r.config.cost_bps} bps.` });

  $("verdict").innerHTML = `<h2>${head}</h2><ul class="points">${points.map((p) => `<li class="${p.cls}">${p.text}</li>`).join("")}</ul>`;
}

function ciBar(lo, est, hi, M) {
  const W = 110, H = 14, x = (v) => ((v + M) / (2 * M)) * W;
  const color = lo > 0 ? css("--good") : hi < 0 ? css("--bad") : css("--muted");
  return `<svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" aria-hidden="true">
    <line x1="${x(0)}" x2="${x(0)}" y1="0" y2="${H}" stroke="${css("--zero")}" stroke-dasharray="2 2"/>
    <line x1="${x(Math.max(lo, -M))}" x2="${x(Math.min(hi, M))}" y1="${H / 2}" y2="${H / 2}" stroke="${color}" stroke-width="3" stroke-linecap="round"/>
    <circle cx="${x(Math.max(Math.min(est, M), -M))}" cy="${H / 2}" r="3.6" fill="${css("--panel")}" stroke="${color}" stroke-width="2"/></svg>`;
}

function renderMainTable() {
  const r = state.result;
  if (!r) return;
  const p = r.period;
  $("period-line").textContent = `${p.start} to ${p.end} (${p.years.toFixed(1)} years) · ${r.config.assets.length} assets · `
    + `${r.config.lookback}-day lookback · ${r.config.freq === "QE" ? "quarterly" : "monthly"} rebalance · `
    + `${r.config.cost_bps} bps costs · ${pct(r.config.cap, 0)} cap`
    + (r.config.cov_freq === "weekly" ? " · weekly covariance (assets trade on different exchanges)" : "");
  const M = Math.max(0.2, ...Object.values(r.vs_ew).flatMap((v) => v.d_sharpe_ci.map(Math.abs)));
  const rows = r.strategies.map((k) => {
    const m = r.table[k][state.basis];
    const v = r.vs_ew[k];
    const isBench = BENCH.includes(k);
    const dd = v ? `<span class="${v.d_max_dd_ci[0] > 0 ? "pos" : v.d_max_dd_ci[1] < 0 ? "neg" : ""}">${spct(v.d_max_dd, 1)}</span>
      <span class="ci-text">[${spct(v.d_max_dd_ci[0], 0)}, ${spct(v.d_max_dd_ci[1], 0)}]</span>` : "baseline";
    const ci = v ? `<div class="ci-cell"><span class="ci-text">${snum(v.d_sharpe)} [${snum(v.d_sharpe_ci[0])}, ${snum(v.d_sharpe_ci[1])}]</span>${ciBar(v.d_sharpe_ci[0], v.d_sharpe, v.d_sharpe_ci[1], M)}</div>` : "baseline";
    const dsr = isBench ? "—" : pct(r.dsr[k], 0);
    return `<tr class="${isBench ? "bench" : ""} ${k === "Equal weight" ? "ew" : ""}">
      <td class="name">${sw(k)}${k}</td><td>${pct(m.ann_return)}</td><td>${pct(m.ann_vol)}</td><td>${num(m.sharpe)}</td>
      <td class="ci">${ci}</td><td>${dsr}</td><td>${pct(m.max_dd)}</td><td>${dd}</td>
      <td>${pct(m.ann_turnover, 0)}</td><td>${pct(m.avg_max_weight, 0)}</td></tr>`;
  });
  $("main-table").innerHTML = `<thead><tr><th>Strategy</th><th>Ann. return</th><th>Volatility</th><th>Sharpe</th>
    <th>ΔSharpe vs EW (95% CI)</th><th title="Probability the excess return over T-bills is not an artefact of the variants tried">Deflated Sharpe<br>vs cash</th><th>Max drawdown</th>
    <th title="Positive = shallower than equal weight">ΔMax DD vs EW</th><th>Turnover / yr</th><th>Avg max weight</th></tr></thead>
    <tbody>${rows.join("")}</tbody>`;
}

function plotLayout(extra = {}) {
  return {
    margin: { l: 56, r: 12, t: 8, b: 36 }, paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
    font: { family: css("--font"), size: 12, color: css("--muted") },
    xaxis: { gridcolor: css("--line"), linecolor: css("--line-strong"), zeroline: false },
    yaxis: { gridcolor: css("--line"), linecolor: css("--line-strong"), zeroline: false },
    legend: { orientation: "h", y: -0.14, x: 0, font: { size: 11 } }, hovermode: "x unified",
    hoverlabel: { bgcolor: css("--panel"), bordercolor: css("--line-strong"), font: { color: css("--ink") } },
    ...extra,
  };
}
const PLOT_CFG = { responsive: true, displayModeBar: false };

function renderCharts() {
  if (!window.Plotly) { setTimeout(renderCharts, 200); return; }
  const r = state.result;
  const x = r.series.dates;
  const line = (k, y) => ({ x, y, name: k, type: "scatter", mode: "lines",
    line: { color: stratColor(k), width: k === "Equal weight" ? 2.4 : 1.6, dash: BENCH.includes(k) ? "dot" : "solid" } });
  const split = { type: "line", xref: "x", yref: "paper", x0: r.halves.split, x1: r.halves.split, y0: 0, y1: 1,
    line: { color: css("--zero"), width: 1, dash: "dot" } };
  Plotly.react("chart-wealth", r.strategies.map((k) => ({ ...line(k, r.series.wealth[k]), hovertemplate: "%{y:$.2f}" })),
    plotLayout({ yaxis: { ...plotLayout().yaxis, type: "log", tickprefix: "$" }, shapes: [split] }), PLOT_CFG);
  Plotly.react("chart-dd", r.strategies.map((k) => ({ ...line(k, r.series.drawdown[k]), showlegend: false, hovertemplate: "%{y:.1%}" })),
    plotLayout({ yaxis: { ...plotLayout().yaxis, tickformat: ".0%" }, shapes: [split] }), PLOT_CFG);

  const assets = r.config.assets;
  const color = Object.fromEntries(assets.map((a, i) => [a, ASSET_COLORS[i % ASSET_COLORS.length]]));
  const opt = Object.keys(r.weights).filter((k) => k !== "Equal weight");
  $("weights").innerHTML = opt.length
    ? `<div style="grid-column:1/-1;font-size:.8rem;color:${css("--muted")}">${assets.map((a) => `<span style="margin-right:10px;white-space:nowrap"><span class="swatch" style="background:${color[a]}"></span>${esc(shortTicker(a))}</span>`).join("")}</div>`
      + opt.map((k, i) => `<div><p class="wtitle">${sw(k)}${k}</p><div id="w-${i}" class="chart"></div></div>`).join("")
    : `<p class="sub">Select an optimized strategy to see its weights.</p>`;
  opt.forEach((k, i) => {
    const w = r.weights[k];
    Plotly.react(`w-${i}`, w.assets.map((a, j) => ({
      x: w.dates, y: w.values[j], name: shortTicker(a), type: "scatter", mode: "lines", stackgroup: "w",
      line: { width: 0, color: color[a] }, fillcolor: color[a], hovertemplate: `${shortTicker(a)} %{y:.0%}<extra></extra>`,
    })), plotLayout({ showlegend: false, margin: { l: 40, r: 8, t: 4, b: 28 }, hovermode: "x",
      yaxis: { ...plotLayout().yaxis, tickformat: ".0%", range: [0, 1] } }), PLOT_CFG);
  });
}

function renderSideTables() {
  const r = state.result;
  const ew = r.halves.rows["Equal weight"];
  $("halves-sub").textContent = `Sharpe ratio before and after ${r.halves.split}, net of costs. A tag marks a strategy whose ranking vs equal weight flips.`;
  $("halves-table").innerHTML = `<thead><tr><th>Strategy</th><th>1st half</th><th>vs EW</th><th>2nd half</th><th>vs EW</th></tr></thead><tbody>`
    + r.strategies.map((k) => {
      const h = r.halves.rows[k];
      const d1 = h.first.sharpe - ew.first.sharpe, d2 = h.second.sharpe - ew.second.sharpe;
      const flip = k !== "Equal weight" && Math.sign(d1) !== Math.sign(d2);
      return `<tr class="${BENCH.includes(k) ? "bench" : ""}"><td class="name">${sw(k)}${k}${flip ? '<span class="tag">flips</span>' : ""}</td>
        <td>${num(h.first.sharpe)}</td><td class="${d1 > 0 ? "pos" : "neg"}">${k === "Equal weight" ? "" : snum(d1)}</td>
        <td>${num(h.second.sharpe)}</td><td class="${d2 > 0 ? "pos" : "neg"}">${k === "Equal weight" ? "" : snum(d2)}</td></tr>`;
    }).join("") + "</tbody>";

  const vm = r.vol_matched;
  $("volmatch-sub").textContent = `Each strategy levered or delevered to equal weight's ${pct(vm.target_vol)} volatility, borrowing at the T-bill rate. `
    + "This answers which method gives more return per unit of risk, if leverage were available.";
  $("volmatch-table").innerHTML = `<thead><tr><th>Strategy</th><th>Leverage</th><th>Ann. return</th><th>Max drawdown</th></tr></thead><tbody>`
    + r.strategies.map((k) => {
      const v = vm.rows[k];
      return `<tr class="${BENCH.includes(k) ? "bench" : ""}"><td class="name">${sw(k)}${k}</td><td>${num(v.leverage)}×</td><td>${pct(v.ann_return)}</td><td>${pct(v.max_dd)}</td></tr>`;
    }).join("") + "</tbody>";

  $("stress-table").innerHTML = r.stress.length
    ? `<thead><tr><th>Strategy</th>${r.stress.map((s) => `<th>${esc(s.label)}</th>`).join("")}</tr></thead><tbody>`
      + r.strategies.map((k) => `<tr class="${BENCH.includes(k) ? "bench" : ""}"><td class="name">${sw(k)}${k}</td>`
        + r.stress.map((s) => `<td>${pct(s.rows[k].return)} <span class="ci-text">(${pct(s.rows[k].max_dd, 0)})</span></td>`).join("") + "</tr>").join("") + "</tbody>"
    : `<tbody><tr><td>No stress episode (2008–09, 2020, 2022) lies fully inside this period.</td></tr></tbody>`;

  const g = r.costs.grid;
  $("cost-table").innerHTML = `<thead><tr><th>Strategy</th>${g.map((b) => `<th>${b} bps${b === r.config.cost_bps ? " ●" : ""}</th>`).join("")}</tr></thead><tbody>`
    + r.strategies.map((k) => `<tr class="${BENCH.includes(k) ? "bench" : ""}"><td class="name">${sw(k)}${k}</td>`
      + g.map((b) => `<td>${num(r.costs.rows[k][String(b)].sharpe)}</td>`).join("") + "</tr>").join("") + "</tbody>";
}

// ---------- boot ----------
async function boot() {
  renderTrials();
  setStatus("Loading data…", 2);
  state.meta = await (await fetch("data/meta.json")).json();
  const available = new Set(state.meta.assets.map((a) => a.ticker));
  state.cfg = hashToCfg(location.hash, available) || structuredClone(DEFAULT);
  buildControls();
  writeControls();
  onConfigChange();
  const q = state.meta.quality;
  $("data-line").textContent = `Data through ${state.meta.data_through}, downloaded ${state.meta.downloaded_at.replace("T", " ").slice(0, 16)} UTC (${state.meta.source}). `
    + `${q.extreme_returns.length} daily moves beyond ±15% are kept and listed for review in data/meta.json.`;
  if (state.meta.repo) { $("repo-link").href = state.meta.repo; $("issues-link").href = `${state.meta.repo}/issues`; }
  if (state.ready) run();
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => state.result && render());
}
boot().catch((e) => setStatus(`Could not load data: ${e.message}`, 0));
