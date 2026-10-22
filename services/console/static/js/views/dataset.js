// Dataset: the lakehouse the agents query, read live from the warehouse. A schema map (the fact
// table at the center, the tables it references around it, context tables beyond), a detail panel
// for the selected table, a table view of every table, and the SQL behind the view.
import { api, esc, fmtCompact, fmtInt } from "../ui.js";

const ROLE = { fact: "Fact", dimension: "Dimension", context: "Context" };
let selected = null;
let showTable = false;

const fmtBytes = (b) => {
  if (b == null) return "–";
  const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0, v = b;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${u[i]}`;
};
const partitionText = (t) => t.partitioning?.length
  ? `${t.partitioning.map((p) => `${p.transform.toLowerCase()}(${p.column})`).join(", ")}${t.partitions != null ? ` · ${fmtInt(t.partitions)} partitions` : ""}`
  : "Not partitioned";

// Columned layout: context tables | dimensions | fact | dimensions | context tables. Dimensions that
// reference each other or are referenced by context tables share a side, so links curve around the
// edge of a column instead of crossing the fact table.
function layout(d) {
  const nodeW = 236, nodeH = 70, rowGap = 120;
  const cols = { ctxL: 140, dimL: 500, fact: 860, dimR: 1220, ctxR: 1580 };
  const W = 1720;
  const role = Object.fromEntries(d.tables.map((t) => [t.name, t.role]));
  const dims = d.tables.filter((t) => t.role === "dimension").map((t) => t.name);
  const linked = (a, b) => d.relationships.some((e) => (e.from === a && e.to === b) || (e.from === b && e.to === a));
  // Group dimensions that link to each other; groups touching a context table go right.
  const groups = [], seen = new Set();
  for (const n of dims) {
    if (seen.has(n)) continue;
    const g = [n]; seen.add(n);
    for (let i = 0; i < g.length; i++) for (const m of dims) if (!seen.has(m) && linked(g[i], m)) { g.push(m); seen.add(m); }
    groups.push({ names: g, ctx: g.some((x) => d.relationships.some((e) => (e.from === x || e.to === x) && role[e.from === x ? e.to : e.from] === "context")) });
  }
  const right = [], left = [];
  groups.sort((a, b) => b.names.length - a.names.length).forEach((g) => (g.ctx || (left.length > right.length + g.names.length) ? right : left).push(...g.names));
  while (left.length > right.length + 1) right.push(left.pop());
  const rows = Math.max(left.length, right.length, 3);
  const H = rows * rowGap + 30;
  const pos = {};
  const place = (names, x) => names.forEach((n, i) => { pos[n] = { x, y: H / 2 + (i - (names.length - 1) / 2) * rowGap }; });
  place(left, cols.dimL); place(right, cols.dimR);
  pos[d.fact] = { x: cols.fact, y: H / 2 };
  const taken = { ctxL: [], ctxR: [] };
  d.tables.filter((t) => t.role === "context").forEach((t) => {
    const near = d.relationships.filter((e) => e.from === t.name && pos[e.to]).map((e) => pos[e.to]);
    const side = near.length && near.some((p) => p.x === cols.dimL) && !near.some((p) => p.x === cols.dimR) ? "ctxL" : (near.length ? "ctxR" : "ctxL");
    let y = near.length ? near.reduce((s2, p) => s2 + p.y, 0) / near.length : H - nodeH / 2 - 10;
    while (taken[side].some((v) => Math.abs(v - y) < nodeH + 14)) y += nodeH + 16;
    y = Math.min(H - nodeH / 2 - 4, y);
    taken[side].push(y);
    pos[t.name] = { x: cols[side], y };
  });
  for (const t of d.tables) pos[t.name].w = Math.max(nodeW, Math.ceil(t.name.length * 9.8 + 34));
  return { W, H, pos, nodeW, nodeH };
}

function edgePath(a, b) {
  if (a.x === b.x) {  // same column: bow outward from the fact table
    const out = a.x < 860 ? -1 : 1, x = a.x + out * Math.max(a.w, b.w) / 2, bow = x + out * 46;
    return { d: `M ${x} ${a.y} C ${bow} ${a.y}, ${bow} ${b.y}, ${x} ${b.y}`, lx: bow + out * 4, ly: (a.y + b.y) / 2, anchor: out < 0 ? "end" : "start" };
  }
  const dir = b.x > a.x ? 1 : -1;
  const x1 = a.x + dir * a.w / 2, x2 = b.x - dir * b.w / 2, mx = (x1 + x2) / 2;
  return { d: `M ${x1} ${a.y} C ${mx} ${a.y}, ${mx} ${b.y}, ${x2} ${b.y}`, lx: mx, ly: (a.y + b.y) / 2 - 7, anchor: "middle" };
}

function map(d) {
  const { W, H, pos, nodeH } = layout(d);
  const edges = d.relationships.map((e, i) => {
    const g = edgePath(pos[e.from], pos[e.to]);
    return `<g class="edge" data-from="${esc(e.from)}" data-to="${esc(e.to)}">
      <path d="${g.d}"></path>
      <text x="${g.lx}" y="${g.ly}" text-anchor="${g.anchor}" class="edge-key" data-i="${i}"></text></g>`;
  }).join("");
  const nodes = d.tables.map((t) => {
    const p = pos[t.name];
    return `<g class="tnode ${t.role}" data-name="${esc(t.name)}" tabindex="0" role="button" transform="translate(${p.x - p.w / 2},${p.y - nodeH / 2})">
      <rect width="${p.w}" height="${nodeH}" rx="12"></rect>
      <text x="16" y="28" class="tn-name"></text>
      <text x="16" y="49" class="tn-meta"></text>
    </g>`;
  }).join("");
  return `<svg class="schema" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">${edges}${nodes}</svg>`;
}

function detail(d, t) {
  const refs = d.relationships.filter((e) => e.from === t.name);
  const by = d.relationships.filter((e) => e.to === t.name);
  return `
    <div class="td-left">
    <div class="eyebrow">${esc(ROLE[t.role])} table</div><h2 class="td-name mono"></h2>
    ${(t.classifications || []).length ? `<div class="td-tags">${t.classifications.map(() => `<span class="tag cls"></span>`).join("")}</div>` : ""}
    <p class="td-desc"></p>
    <dl class="td-facts">
      <div><dt>Rows</dt><dd>${fmtInt(t.rows)}</dd></div>
      <div><dt>Columns</dt><dd>${fmtInt(t.columns.length)}</dd></div>
      <div><dt>Format</dt><dd>${esc(t.format)}</dd></div>
      <div><dt>Partitioning</dt><dd>${esc(partitionText(t))}</dd></div>
      <div><dt>Snapshots</dt><dd>${t.snapshots != null ? fmtInt(t.snapshots) : "–"}</dd></div>
      <div><dt>Storage</dt><dd>${fmtBytes(t.bytes)}${t.files != null ? ` · ${fmtInt(t.files)} file${t.files === 1 ? "" : "s"}` : ""}</dd></div>
    </dl>
    </div>
    <div class="td-right">
    <div class="td-rels">
      ${refs.length ? `<div><div class="field-label">References</div>${refs.map((e) => `<button class="rel" data-goto="${esc(e.to)}"><span class="mono">${esc(e.to)}</span><span class="muted small">via ${esc(e.key)}</span></button>`).join("")}</div>` : ""}
      ${by.length ? `<div><div class="field-label">Referenced by</div>${by.map((e) => `<button class="rel" data-goto="${esc(e.from)}"><span class="mono">${esc(e.from)}</span><span class="muted small">via ${esc(e.key)}</span></button>`).join("")}</div>` : ""}
    </div>
    <div class="field-label" style="margin-top:18px">Columns</div>
    <div class="td-cols">${t.columns.map(() => `<div class="col"><span class="mono c-name"></span><span class="mono c-type"></span></div>`).join("")}</div>
    </div>`;
}

function fillDetail(el, t) {
  el.querySelector(".td-name").textContent = t.name;
  el.querySelector(".td-desc").textContent = t.description || "No catalog description.";
  el.querySelectorAll(".cls").forEach((n, i) => { n.textContent = t.classifications[i]; });
  el.querySelectorAll(".td-cols .col").forEach((n, i) => {
    n.querySelector(".c-name").textContent = t.columns[i].name;
    n.querySelector(".c-type").textContent = t.columns[i].type;
  });
}

function tableView(d) {
  return `<div class="table-wrap"><table class="data"><thead><tr><th>Table</th><th>Role</th><th class="num">Rows</th><th class="num">Columns</th><th>Format</th><th>Partitioning</th><th class="num">Snapshots</th><th class="num">Storage</th></tr></thead>
    <tbody>${d.tables.map((t) => `<tr><td class="mono">${esc(t.name)}</td><td>${esc(ROLE[t.role])}</td><td class="num">${fmtInt(t.rows)}</td><td class="num">${fmtInt(t.columns.length)}</td>
      <td>${esc(t.format)}</td><td>${esc(partitionText(t))}</td><td class="num">${t.snapshots != null ? fmtInt(t.snapshots) : "–"}</td><td class="num">${fmtBytes(t.bytes)}</td></tr>`).join("")}</tbody></table></div>`;
}

export async function render(view) {
  view.innerHTML = `
    <header class="page-head">
      <div><div class="eyebrow">The lakehouse</div><h1 class="page-title" id="ds-title">Dataset</h1>
        <p class="page-sub">The tables the agents query, read live from the warehouse: their size, format, partitioning and how they relate. Descriptions come from the same metadata catalog the agents read.</p></div>
      <div class="head-actions"><button class="btn" id="refresh">Refresh</button></div>
    </header>
    <div id="ds"><div class="muted">Reading the warehouse…</div></div>`;

  const load = async (refresh) => {
    const box = view.querySelector("#ds");
    box.classList.add("refetch");
    let d;
    try { d = await api(`/api/dataset${refresh ? "?refresh=1" : ""}`); } catch (err) { d = { available: false, reason: err.message }; }
    box.classList.remove("refetch");
    if (!d.available) { box.innerHTML = `<div class="empty"><h3>The dataset is not available</h3><p>${esc(d.reason)}</p></div>`; return; }
    if (!d.tables.length) { box.innerHTML = `<div class="empty"><h3>No tables</h3><p>The database ${esc(d.database)} has no tables.</p></div>`; return; }
    view.querySelector("#ds-title").textContent = d.database;
    const by = Object.fromEntries(d.tables.map((t) => [t.name, t]));
    if (!selected || !by[selected]) selected = d.fact;
    const fact = by[d.fact];
    const counts = ["fact", "dimension", "context"].map((r) => `${d.tables.filter((t) => t.role === r).length} ${r}`).join(" · ");
    const q = d.queries;
    box.innerHTML = `
      <section class="kpis">
        <div class="kpi data"><div class="kpi-label">Transactions</div><div class="kpi-value">${fmtCompact(fact.rows)}</div>
          <div class="kpi-note mono">${esc(fact.name)}</div></div>
        <div class="kpi"><div class="kpi-label">Tables</div><div class="kpi-value">${d.tables.length}</div><div class="kpi-note">${counts}</div></div>
        <div class="kpi data"><div class="kpi-label">Partitions</div><div class="kpi-value">${fact.partitions != null ? fmtInt(fact.partitions) : "–"}</div>
          <div class="kpi-note">${esc(partitionText(fact).split(" · ")[0])} on the fact table</div></div>
        <div class="kpi"><div class="kpi-label">Storage</div><div class="kpi-value">${fmtBytes(d.total_bytes)}</div>
          <div class="kpi-note">${esc(fact.format)} · ${fmtCompact(d.total_rows)} rows in all</div></div>
      </section>
      <section class="panel chart-card">
        <div class="chart-head"><div><h2 class="chart-title">Schema</h2>
          <p class="chart-sub">Lines join a table to the tables whose key it holds, labeled with that key. Select a table to see its detail.</p></div>
          <button class="btn ghost" id="toggle-table">${showTable ? "Show schema" : "View as table"}</button></div>
        <div class="schema-wrap" ${showTable ? "hidden" : ""}>
          ${map(d)}
          <aside class="table-detail" id="detail"></aside>
        </div>
        <div ${showTable ? "" : "hidden"}>${tableView(d)}</div>
      </section>
      <details class="sql-panel"><summary>SQL behind this view <span class="muted">· ${q.length} statements in ${fmtInt(q.reduce((s, x) => s + x.elapsed_ms, 0))} ms · ${d.cached_s ? `${Math.max(1, Math.round(d.cached_s / 60))} min ago (cached)` : "just now"}</span></summary>
        ${q.map(() => `<pre><code class="language-sql"></code></pre>`).join("")}</details>`;

    // Names, descriptions and keys go in as text.
    box.querySelectorAll(".tnode").forEach((n) => {
      const t = by[n.dataset.name];
      n.querySelector(".tn-name").textContent = t.name;
      n.querySelector(".tn-meta").textContent = `${ROLE[t.role]} · ${fmtCompact(t.rows)} row${t.rows === 1 ? "" : "s"}${t.partitions ? ` · ${fmtInt(t.partitions)} partitions` : ""}`;
    });
    box.querySelectorAll(".edge-key").forEach((n) => { n.textContent = d.relationships[Number(n.dataset.i)].key; });
    box.querySelectorAll(".sql-panel code").forEach((c, i) => { c.textContent = q[i].sql; if (window.hljs) window.hljs.highlightElement(c); });

    const select = (name) => {
      selected = name;
      const panel = box.querySelector("#detail");
      panel.innerHTML = detail(d, by[name]);
      fillDetail(panel, by[name]);
      box.querySelectorAll(".tnode").forEach((n) => n.classList.toggle("on", n.dataset.name === name));
      box.querySelectorAll(".edge").forEach((e) => e.classList.toggle("on", e.dataset.from === name || e.dataset.to === name));
      panel.querySelectorAll("[data-goto]").forEach((b) => b.addEventListener("click", () => select(b.dataset.goto)));
    };
    box.querySelectorAll(".tnode").forEach((n) => {
      n.addEventListener("click", () => select(n.dataset.name));
      n.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(n.dataset.name); } });
      n.addEventListener("mouseenter", () => box.querySelectorAll(".edge").forEach((e) => e.classList.toggle("hover", e.dataset.from === n.dataset.name || e.dataset.to === n.dataset.name)));
      n.addEventListener("mouseleave", () => box.querySelectorAll(".edge").forEach((e) => e.classList.remove("hover")));
    });
    box.querySelector("#toggle-table").addEventListener("click", () => { showTable = !showTable; load(false); });
    select(selected);
  };
  view.querySelector("#refresh").addEventListener("click", () => load(true));
  await load(false);
}
