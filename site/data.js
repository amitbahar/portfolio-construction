// Data page: reads the same prices.csv and meta.json as the backtest and shows them as tables and charts.

const $ = (id) => document.getElementById(id);
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const pct = (x, d = 1) => (x == null || Number.isNaN(x) ? "—" : `${x >= 0 ? "" : "−"}${Math.abs(x * 100).toFixed(d)}%`);
const shortTicker = (t) => t.replace(/\.TA$/, "");
const PAGE = 50;
const EXTREME = 0.15;

const state = { meta: null, dates: [], cols: {}, ticker: null, rows: [], page: 0, sort: { key: "category", dir: 1 } };

// ---------- data ----------
function parseCsv(text) {
  const lines = text.trim().split("\n");
  const head = lines[0].split(",");
  const n = lines.length - 1;
  const cols = Object.fromEntries(head.slice(1).map((h) => [h, new Float64Array(n).fill(NaN)]));
  const dates = new Array(n);
  for (let i = 0; i < n; i++) {
    const f = lines[i + 1].split(",");
    dates[i] = f[0];
    for (let j = 1; j < head.length; j++) if (f[j] !== "" && f[j] !== undefined) cols[head[j]][i] = +f[j];
  }
  return { dates, cols };
}

// Rows on which the fund has a price, with the return from its previous price (as in the SQL returns view).
function fundRows(t) {
  const p = state.cols[t];
  const rows = [];
  let prev = NaN;
  for (let i = 0; i < state.dates.length; i++) {
    if (Number.isNaN(p[i])) continue;
    rows.push({ date: state.dates[i], price: p[i], ret: Number.isNaN(prev) ? NaN : p[i] / prev - 1 });
    prev = p[i];
  }
  return rows;
}

// ---------- rendering ----------
function plotLayout(extra = {}) {
  return {
    margin: { l: 56, r: 12, t: 8, b: 32 }, paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
    font: { family: css("--font"), size: 12, color: css("--muted") },
    xaxis: { gridcolor: css("--line"), linecolor: css("--line-strong"), zeroline: false },
    yaxis: { gridcolor: css("--line"), linecolor: css("--line-strong"), zeroline: false },
    showlegend: false, hovermode: "x",
    hoverlabel: { bgcolor: css("--panel"), bordercolor: css("--line-strong"), font: { color: css("--ink") } },
    ...extra,
  };
}
const PLOT_CFG = { responsive: true, displayModeBar: false };

function renderSnapshot() {
  const m = state.meta, q = m.quality;
  $("snapshot-sub").textContent = `Downloaded ${m.downloaded_at.replace("T", " ").slice(0, 16)} UTC from ${m.source}.`;
  const stats = [
    ["Funds", m.assets.length],
    ["Rows (dates)", state.dates.length.toLocaleString()],
    ["Prices", m.assets.reduce((s, a) => s + a.n, 0).toLocaleString()],
    ["From", state.dates[0]],
    ["Through", m.data_through],
  ];
  $("stats").innerHTML = stats.map(([k, v]) => `<div><span class="stat-k">${k}</span><span class="stat-v">${esc(v)}</span></div>`).join("");

  const items = [];
  items.push(q.hard_failures.length
    ? { cls: "bad", text: `Failed checks: ${q.hard_failures.map(esc).join("; ")}.` }
    : { cls: "good", text: "Passed every publishing check: fresh prices and rates, enough funds downloaded, exchange rates for every non-dollar price, no gaps beyond the per-exchange limit and no zero or negative prices." });
  if (q.missing_tickers.length) items.push({ cls: "warn", text: `Not downloaded this time: ${q.missing_tickers.map(esc).join(", ")}.` });
  const gaps = Object.entries(q.missing_days);
  items.push(gaps.length
    ? { cls: "warn", text: `Days missing inside a fund's history: ${gaps.map(([t, n]) => `${esc(shortTicker(t))} (${n})`).join(", ")}. The backtest forward-fills gaps of up to 10 days.` }
    : { cls: "good", text: "No fund is missing a trading day inside its history." });
  items.push({ cls: q.extreme_returns.length ? "warn" : "good",
    text: `${q.extreme_returns.length} daily move${q.extreme_returns.length === 1 ? "" : "s"} beyond ±${EXTREME * 100}% ${q.extreme_returns.length === 1 ? "is" : "are"} kept and flagged for review.`
      + (q.extreme_returns.length ? ` <details class="inline-details"><summary>Show them</summary>`
        + q.extreme_returns.map((e) => `<a href="#t=${encodeURIComponent(e.ticker)}&d=${e.date}">${esc(shortTicker(e.ticker))} ${e.date} (${pct(e.ret)})</a>`).join(", ")
        + "</details>" : "") });
  const flat = Object.entries(q.flat_runs);
  items.push(flat.length
    ? { cls: "warn", text: `Runs of 5 or more unchanged prices, a sign of stale data: ${flat.map(([t, n]) => `${esc(shortTicker(t))} (${n} days)`).join(", ")}.` }
    : { cls: "good", text: "No run of 5 or more unchanged prices." });
  $("quality").innerHTML = items.map((i) => `<li class="${i.cls}">${i.text}</li>`).join("");
}

const LEFT_COLS = new Set(["name", "category", "exchange"]);
const FUND_COLS = [
  ["ticker", "Ticker"], ["name", "Name"], ["category", "Category"], ["exchange", "Exchange"], ["first", "First"],
  ["last", "Last"], ["n", "Prices"], ["coverage", "Coverage"], ["gaps", "Missing days"], ["flagged", "Flagged moves"],
];

function renderFunds() {
  const q = $("fund-filter").value.trim().toLowerCase();
  const { key, dir } = state.sort;
  const list = state.meta.assets
    .filter((a) => !q || `${a.ticker} ${a.name} ${a.category} ${a.exchange}`.toLowerCase().includes(q))
    .sort((a, b) => (a[key] < b[key] ? -dir : a[key] > b[key] ? dir : a.ticker < b.ticker ? -1 : 1));
  $("fund-table").innerHTML = `<thead><tr>${FUND_COLS.map(([k, label]) =>
    `<th class="${LEFT_COLS.has(k) ? "left" : ""}"><button type="button" class="sort" data-key="${k}">${label}${k === key ? (dir > 0 ? " ▲" : " ▼") : ""}</button></th>`).join("")}</tr></thead><tbody>`
    + list.map((a) => `<tr data-ticker="${esc(a.ticker)}" class="${a.ticker === state.ticker ? "selected" : ""}" tabindex="0">
      <td class="mono">${esc(shortTicker(a.ticker))}</td><td class="left">${esc(a.name)}</td><td class="left">${esc(a.category)}</td>
      <td class="left">${esc(a.exchange)} · ${esc(a.currency)}</td><td>${a.first}</td><td>${a.last}</td><td>${a.n.toLocaleString()}</td>
      <td>${pct(a.coverage, 1)}</td><td>${a.gaps || ""}</td><td>${a.flagged || ""}</td></tr>`).join("")
    + "</tbody>";
}

function selectFund(t, date) {
  if (!state.cols[t]) return;
  state.ticker = t;
  state.rows = fundRows(t).reverse();
  state.page = 0;
  state.highlight = null;
  if (date) jumpTo(date);
  $("fund-select").value = t;
  history.replaceState(null, "", `#t=${encodeURIComponent(t)}`);
  document.querySelectorAll("#fund-table tr[data-ticker]").forEach((tr) => tr.classList.toggle("selected", tr.dataset.ticker === t));
  const a = state.meta.assets.find((x) => x.ticker === t);
  $("viewer-title").textContent = `${shortTicker(t)} · ${a.name}`;
  $("viewer-sub").textContent = `${a.category}, ${a.exchange === "US" ? "US-listed" : `listed on ${a.exchange}`}, ${a.n.toLocaleString()} prices from ${a.first} to ${a.last}. `
    + "Adjusted closes include dividends and splits. "
    + (a.currency === "USD" ? "" : `Converted from ${a.currency} to US dollars at the Bank of Israel representative rate, so returns include currency moves. `)
    + `Moves beyond ±${EXTREME * 100}% are marked.`;
  renderFundCharts();
  renderRows();
}

function renderFundCharts() {
  if (!window.Plotly) { setTimeout(renderFundCharts, 200); return; }
  const rows = [...state.rows].reverse();
  const x = rows.map((r) => r.date);
  const ink = css("--accent");
  Plotly.react("chart-price", [{ x, y: rows.map((r) => r.price), type: "scatter", mode: "lines", line: { color: ink, width: 1.4 },
    hovertemplate: "%{x}<br>$%{y:,.4f}<extra></extra>" }],
  plotLayout({ yaxis: { ...plotLayout().yaxis, type: "log", tickprefix: "$" } }), PLOT_CFG);
  const big = rows.filter((r) => Math.abs(r.ret) > EXTREME);
  Plotly.react("chart-ret", [
    { x, y: rows.map((r) => (Number.isNaN(r.ret) ? null : r.ret)), type: "scatter", mode: "lines", line: { color: css("--muted"), width: 0.8 },
      hovertemplate: "%{x}<br>%{y:.2%}<extra></extra>" },
    { x: big.map((r) => r.date), y: big.map((r) => r.ret), type: "scatter", mode: "markers", marker: { color: css("--bad"), size: 7 },
      hovertemplate: "%{x}<br>%{y:.2%} (flagged)<extra></extra>" },
  ], plotLayout({ yaxis: { ...plotLayout().yaxis, tickformat: ".0%" } }), PLOT_CFG);
}

function renderRows() {
  const pages = Math.max(1, Math.ceil(state.rows.length / PAGE));
  state.page = Math.min(Math.max(0, state.page), pages - 1);
  const slice = state.rows.slice(state.page * PAGE, (state.page + 1) * PAGE);
  $("page-info").textContent = `Page ${state.page + 1} of ${pages}`;
  $("prev").disabled = state.page === 0;
  $("next").disabled = state.page >= pages - 1;
  $("rows-table").innerHTML = `<thead><tr><th>Date</th><th>Adj. close (USD)</th><th>Daily return</th><th class="left">Note</th></tr></thead><tbody>`
    + slice.map((r) => {
      const flag = Math.abs(r.ret) > EXTREME;
      return `<tr class="${r.date === state.highlight ? "selected" : ""}"><td>${r.date}</td><td>${r.price.toFixed(4)}</td>
        <td class="${flag ? "neg" : ""}">${pct(r.ret, 2)}</td><td class="left">${flag ? '<span class="tag">flagged</span>' : Number.isNaN(r.ret) ? "first price" : ""}</td></tr>`;
    }).join("") + "</tbody>";
}

function jumpTo(date) {
  const i = state.rows.findIndex((r) => r.date <= date); // rows are newest first
  if (i < 0) { state.page = Math.ceil(state.rows.length / PAGE) - 1; state.highlight = null; return; }
  state.page = Math.floor(i / PAGE);
  state.highlight = state.rows[i].date;
}

function renderRf() {
  if (!window.Plotly) { setTimeout(renderRf, 200); return; }
  const rf = state.cols._rf;
  const x = [], y = [];
  state.dates.forEach((d, i) => { if (!Number.isNaN(rf[i])) { x.push(d); y.push(rf[i]); } });
  Plotly.react("chart-rf", [{ x, y, type: "scatter", mode: "lines", line: { color: css("--accent"), width: 1.4 }, hovertemplate: "%{x}<br>%{y:.2%}<extra></extra>" }],
    plotLayout({ yaxis: { ...plotLayout().yaxis, tickformat: ".1%" } }), PLOT_CFG);
}

function readHash() {
  const p = new URLSearchParams(location.hash.replace(/^#/, ""));
  return { t: p.get("t"), d: /^\d{4}-\d{2}-\d{2}$/.test(p.get("d") || "") ? p.get("d") : null };
}

// ---------- boot ----------
async function boot() {
  const [meta, csv] = await Promise.all([
    fetch("data/meta.json").then((r) => r.json()),
    fetch("data/prices.csv").then((r) => r.text()),
  ]);
  Object.assign(state, parseCsv(csv), { meta });
  const flagged = {};
  for (const e of meta.quality.extreme_returns) flagged[e.ticker] = (flagged[e.ticker] || 0) + 1;
  for (const a of meta.assets) {
    a.n = state.cols[a.ticker] ? state.cols[a.ticker].reduce((s, v) => s + !Number.isNaN(v), 0) : 0;
    a.gaps = meta.quality.missing_days[a.ticker] || 0;
    a.flagged = flagged[a.ticker] || 0;
  }
  if (meta.repo) {
    $("issues-link").href = `${meta.repo}/issues`;
    $("schema-link").href = `${meta.repo}/blob/main/pipeline/refresh.py`;
  }

  $("fund-select").innerHTML = meta.assets.map((a) => `<option value="${esc(a.ticker)}">${esc(shortTicker(a.ticker))} · ${esc(a.name)}</option>`).join("");
  $("fund-filter").addEventListener("input", renderFunds);
  $("fund-table").addEventListener("click", (e) => {
    const k = e.target.closest("button.sort")?.dataset.key;
    if (k) { state.sort = { key: k, dir: state.sort.key === k ? -state.sort.dir : 1 }; renderFunds(); return; }
    const tr = e.target.closest("tr[data-ticker]");
    if (tr) { selectFund(tr.dataset.ticker); $("viewer").scrollIntoView({ behavior: "smooth" }); }
  });
  $("fund-table").addEventListener("keydown", (e) => {
    const tr = e.target.closest("tr[data-ticker]");
    if (tr && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); selectFund(tr.dataset.ticker); }
  });
  $("fund-select").addEventListener("change", (e) => selectFund(e.target.value));
  $("prev").addEventListener("click", () => { state.page--; renderRows(); });
  $("next").addEventListener("click", () => { state.page++; renderRows(); });
  $("jump").addEventListener("change", (e) => { if (e.target.value) { jumpTo(e.target.value); renderRows(); } });
  addEventListener("hashchange", () => { const h = readHash(); if (h.t) { selectFund(h.t, h.d); renderRows(); $("viewer").scrollIntoView({ behavior: "smooth" }); } });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { renderFundCharts(); renderRf(); });

  $("load-status").hidden = true;
  $("content").hidden = false;
  renderSnapshot();
  renderFunds();
  const h = readHash();
  selectFund(state.cols[h.t] ? h.t : "SPY", h.d);
  renderRows();
  renderRf();
}
boot().catch((e) => { $("load-status").textContent = `Could not load the data: ${e.message}`; });
