// Architecture: how the parts connect, each with its live status, and what a harness does that an
// inference endpoint does not, shown with the numbers of a real run.
import { api, esc, fmtCompact, fmtDuration, fmtInt } from "../ui.js";
import { agentColor } from "../replay.js";

const STATE = {
  up: ["ok", "Up"], configured: ["ok", "Configured"], down: ["bad", "Unreachable"], not_configured: ["warn", "Not configured"],
};
const pill = (state) => { const [cls, label] = STATE[state] || ["warn", state]; return `<span class="st ${cls}"><i></i>${label}</span>`; };

function tiles(a) {
  const h = a.harness, w = a.warehouse;
  const harnessNote = h.state === "up" ? (h.current ? `running ${esc(h.current)}${h.queued ? ` · ${h.queued} queued` : ""}` : `idle${h.queued ? ` · ${h.queued} queued` : ""}`) : esc(h.detail || "");
  return `<section class="arch-tiles">
    <div class="arch-tile"><div class="at-k">Mistral Studio connector</div>${pill(a.studio.state)}<div class="at-note">${a.studio.endpoint ? "MCP endpoint published" : esc(a.studio.detail)}</div></div>
    <div class="arch-tile"><div class="at-k">Prime Agent harness</div>${pill(h.state)}<div class="at-note">${harnessNote}</div></div>
    <div class="arch-tile"><div class="at-k">Models</div>${pill(h.state === "up" ? "up" : "not_configured")}<div class="at-note">${esc(a.models.root)} · ${esc(a.models.child)}</div></div>
    <div class="arch-tile"><div class="at-k">Data warehouse</div>${pill(w.state)}<div class="at-note">${w.state === "up" ? `${esc(w.database)} · answered in ${fmtInt(w.latency_ms)} ms` : esc(w.detail || "")}</div></div>
  </section>`;
}

function diagram(a) {
  const workers = Math.max(1, Math.min(a.latest?.children || 3, 6));
  const wy = (i) => 300 + (i - (workers - 1) / 2) * 46;
  const child = (a.models.child || "").replace(/^Mistral /, "");
  const st = (state) => (STATE[state] || ["warn"])[0];
  const workerNodes = Array.from({ length: workers }, (_, i) => `
    <g transform="translate(760,${wy(i) - 17})"><rect class="d-worker" width="236" height="34" rx="9"></rect>
      <rect x="0" y="0" width="5" height="34" rx="2" fill="${agentColor(i + 1)}"></rect>
      <text x="16" y="22" class="d-small">Worker ${i + 1} · ${esc(child)}</text></g>
    <path class="d-link" d="M 700 300 C 730 300, 730 ${wy(i)}, 760 ${wy(i)}"></path>`).join("");
  return `
  <svg class="arch" viewBox="0 0 1560 470" role="img" aria-label="System architecture">
    <defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" class="d-head"></path></marker></defs>

    <!-- Mistral Studio -->
    <g transform="translate(20,232)"><rect class="d-box" width="250" height="136" rx="16"></rect>
      <image href="/static/brand/mistral-lockup-dark.svg" x="22" y="22" width="120" height="27"></image>
      <text x="22" y="80" class="d-title">Mistral Studio</text>
      <text x="22" y="102" class="d-small">The analyst asks in plain language</text>
      <circle cx="226" cy="24" r="6" class="dot-${st(a.studio.state)}"></circle></g>

    <!-- Cloudera AI Workbench -->
    <g transform="translate(330,92)"><rect class="d-zone" width="700" height="360" rx="20"></rect>
      <image href="/static/brand/cloudera-white.png" x="24" y="22" width="112" height="14"></image>
      <text x="146" y="35" class="d-zone-title">AI Workbench</text>
      <text x="24" y="62" class="d-small">Your environment, next to the data</text></g>
    <g transform="translate(360,240)"><rect class="d-box" width="190" height="120" rx="14"></rect>
      <text x="18" y="34" class="d-title">MCP bridge</text>
      <text x="18" y="56" class="d-small">start · watch · findings</text>
      <text x="18" y="76" class="d-small">bearer-token protected</text>
      <circle cx="170" cy="20" r="6" class="dot-${st(a.harness.state)}"></circle></g>
    <g transform="translate(580,250)"><rect class="d-box root" width="120" height="100" rx="14"></rect>
      <rect x="0" y="0" width="5" height="100" rx="2" fill="${agentColor(0)}"></rect>
      <text x="16" y="34" class="d-title">Root</text><text x="16" y="56" class="d-small">plans,</text><text x="16" y="74" class="d-small">delegates</text></g>
    ${workerNodes}
    <text x="760" y="232" class="d-label">Prime Agent harness</text>

    <!-- Mistral API (models) -->
    <g transform="translate(500,6)"><rect class="d-box cloud" width="300" height="64" rx="14"></rect>
      <text x="18" y="28" class="d-title">Mistral API</text>
      <text x="18" y="48" class="d-small">${esc(a.models.root)} · ${esc(a.models.child)}</text></g>

    <!-- Data warehouse -->
    <g transform="translate(1100,150)"><rect class="d-zone data" width="440" height="270" rx="20"></rect>
      <image href="/static/brand/cloudera-white.png" x="24" y="22" width="112" height="14"></image>
      <text x="146" y="35" class="d-zone-title">Data Warehouse</text>
      <text x="24" y="62" class="d-small">Queries run here, on the full dataset</text>
      <circle cx="414" cy="28" r="6" class="dot-${st(a.warehouse.state)}"></circle></g>
    <g transform="translate(1130,240)"><rect class="d-box" width="380" height="150" rx="14"></rect>
      <text x="20" y="36" class="d-title">Apache Iceberg tables</text>
      <text x="20" y="60" class="d-small mono">${esc(a.warehouse.database || "")}</text>
      <text x="20" y="92" class="d-small">read-only login for the agents,</text>
      <text x="20" y="112" class="d-small">governed by the platform's access policies</text></g>

    <!-- flows -->
    <path class="d-flow" d="M 270 300 L 360 300" marker-end="url(#ah)"></path>
    <text x="315" y="290" class="d-flow-label" text-anchor="middle">MCP</text>
    <path class="d-flow" d="M 550 300 L 580 300" marker-end="url(#ah)"></path>
    <path class="d-flow model" d="M 640 70 L 640 250" marker-start="url(#ah)" marker-end="url(#ah)"></path>
    <text x="652" y="206" class="d-flow-label">reasoning</text>
    <path class="d-flow data" d="M 996 280 L 1130 280" marker-end="url(#ah)"></path>
    <text x="1063" y="268" class="d-flow-label" text-anchor="middle">SQL</text>
    <path class="d-flow data" d="M 1130 330 L 996 330" marker-end="url(#ah)"></path>
    <text x="1063" y="350" class="d-flow-label" text-anchor="middle">results</text>
  </svg>`;
}

function comparison(a) {
  const r = a.latest;
  const proof = r ? `
      <li><b>${fmtInt(r.queries)}</b> SQL queries it wrote and ran on the warehouse</li>
      <li><b>${fmtInt(r.children)}</b> workers investigating in parallel, ${fmtInt(r.reports)} reports back${r.reprompts ? `, ${fmtInt(r.reprompts)} re-prompted by the supervisor` : ""}</li>
      <li><b>${fmtInt(r.findings)}</b> findings and <b>${fmtInt(r.ruled_out)}</b> ruled-out explanations, citing <b>${fmtInt(r.evidence)}</b> SQL statements</li>
      <li><b>${esc(fmtDuration(r.elapsed_s))}</b> of autonomous work · ${fmtCompact(r.tokens)} tokens</li>` : `<li class="muted">Run an investigation to see its numbers here.</li>`;
  return `
  <section class="compare">
    <article class="cmp endpoint">
      <div class="eyebrow">Inference endpoint</div>
      <h3>One prompt in, one answer out</h3>
      <ul>
        <li>Answers from what is in the prompt</li>
        <li>Cannot query your data or check its own work</li>
        <li>One model call: no plan, no delegation, no memory of steps</li>
        <li>You build and supervise the loop around it</li>
      </ul>
    </article>
    <article class="cmp harness">
      <div class="eyebrow">Harness · Prime Agent</div>
      <h3>A working process around the model</h3>
      <p class="muted">Plans, writes and runs code and SQL next to the data, splits the work across parallel workers, supervises them, and cites the evidence for every number.</p>
      ${r ? `<div class="cmp-run">In <a href="#/investigations/${esc(r.run_id)}" class="mono">${esc(r.run_id)}</a>:</div>` : ""}
      <ul class="proof">${proof}</ul>
    </article>
  </section>`;
}

export async function render(view, { onTimer }) {
  view.innerHTML = `
    <header class="page-head"><div><div class="eyebrow">Architecture</div><h1 class="page-title">How it works</h1>
      <p class="page-sub">A question asked in Mistral Studio reaches the Prime Agent harness on Cloudera AI Workbench, which reasons with Mistral models and investigates the data where it lives. Status is live.</p></div></header>
    <div id="arch"><div class="muted">Checking each part…</div></div>`;
  const load = async () => {
    let a;
    try { a = await api("/api/architecture"); }
    catch (err) { view.querySelector("#arch").innerHTML = `<div class="empty"><h3>Status unavailable</h3><p class="error">${esc(err.message)}</p></div>`; return; }
    view.querySelector("#arch").innerHTML = `${tiles(a)}<section class="panel arch-panel">${diagram(a)}</section>${comparison(a)}`;
  };
  await load();
  onTimer(load, 15000);
}
