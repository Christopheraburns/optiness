// Investigations: every run, live and historical, newest first.
import { api, esc, elapsed, fmtCompact, fmtDuration, fmtInt, fmtWhen, icon, modelTags, statusPill } from "../ui.js";

const FILTERS = [
  ["all", "All", () => true],
  ["running", "Running", (r) => r.status === "running"],
  ["done", "With findings", (r) => r.status === "done"],
  ["ended", "Ended", (r) => r.status === "ended"],
  ["stalled", "Stalled", (r) => r.status === "stalled"],
];

const state = { filter: "all", query: "" };

function kpis(runs) {
  const done = runs.filter((r) => r.status === "done").length;
  const agents = runs.reduce((n, r) => n + (r.agents || 0), 0);
  const queries = runs.reduce((n, r) => n + (r.queries || 0), 0);
  const live = runs.filter((r) => r.status === "running").length;
  return `
    <section class="kpis">
      <div class="kpi"><div class="kpi-label">Investigations</div><div class="kpi-value">${fmtInt(runs.length)}</div>
        <div class="kpi-note">${live ? `${live} running now` : "none running now"}</div></div>
      <div class="kpi ok"><div class="kpi-label">Completed with findings</div><div class="kpi-value">${fmtInt(done)}</div>
        <div class="kpi-note">evidence-backed reports</div></div>
      <div class="kpi"><div class="kpi-label">Agents spawned</div><div class="kpi-value">${fmtInt(agents)}</div>
        <div class="kpi-note">root agents and their workers</div></div>
      <div class="kpi data"><div class="kpi-label">SQL queries executed</div><div class="kpi-value">${fmtCompact(queries)}</div>
        <div class="kpi-note">run on the warehouse, written by the agents</div></div>
    </section>`;
}

function row(r) {
  return `
    <a class="run fade-in" href="#/investigations/${esc(r.run_id)}">
      ${statusPill(r.status)}
      <div>
        <div class="run-q">${esc(r.question || "(no question recorded)")}</div>
        <div class="run-meta"><span>${esc(fmtWhen(r.started))}</span><span>${esc(fmtDuration(elapsed(r)))}</span><span class="mono">${esc(r.run_id)}</span></div>
      </div>
      <div class="run-side">
        <div class="models">${modelTags(r)}</div>
        <div class="metrics">
          <div><div class="metric-v">${fmtInt(r.agents)}</div><div class="metric-l">Agents</div></div>
          <div><div class="metric-v">${fmtInt(r.queries)}</div><div class="metric-l">Queries</div></div>
          <div><div class="metric-v">${r.findings ? fmtInt(r.findings) : "–"}</div><div class="metric-l">Findings</div></div>
        </div>
        ${icon.chevron}
      </div>
    </a>`;
}

function list(runs) {
  const [, , test] = FILTERS.find(([key]) => key === state.filter);
  const q = state.query.trim().toLowerCase();
  const shown = runs.filter(test).filter((r) => !q || `${r.question} ${r.run_id}`.toLowerCase().includes(q));
  if (!shown.length) {
    return `<div class="empty"><h3>No investigations match</h3><p>Change the filter or search, or start one from Mistral Studio.</p></div>`;
  }
  return shown.map(row).join("");
}

export async function render(view, { onTimer }) {
  view.innerHTML = `
    <header class="page-head">
      <div>
        <div class="eyebrow">Prime Agent</div>
        <h1 class="page-title">Investigations</h1>
        <p class="page-sub">Every question the agent has worked: who it delegated to, the SQL it ran on the warehouse, and what it concluded.</p>
      </div>
      <a class="btn primary" href="#/new">${icon.plus}New investigation</a>
    </header>
    <div id="kpis"></div>
    <div class="toolbar">
      <div class="chips" id="chips"></div>
      <label class="search">${icon.search}<input id="search" placeholder="Search questions or run IDs" value="${esc(state.query)}"></label>
    </div>
    <section class="runs" id="runs"></section>`;

  let runs = [];
  const paint = () => {
    view.querySelector("#kpis").innerHTML = kpis(runs);
    view.querySelector("#chips").innerHTML = FILTERS.map(([key, label, test]) =>
      `<button class="chip-btn ${state.filter === key ? "on" : ""}" data-filter="${key}">${label}<span class="n">${runs.filter(test).length}</span></button>`).join("");
    view.querySelector("#runs").innerHTML = list(runs);
    document.getElementById("nav-count").textContent = runs.length || "";
  };
  view.querySelector("#chips").addEventListener("click", (e) => {
    const button = e.target.closest("[data-filter]");
    if (button) { state.filter = button.dataset.filter; paint(); }
  });
  view.querySelector("#search").addEventListener("input", (e) => { state.query = e.target.value; paint(); });

  const load = async () => {
    try { runs = await api("/api/investigations"); paint(); }
    catch (err) { view.querySelector("#runs").innerHTML = `<div class="empty"><h3>Could not load investigations</h3><p class="error">${esc(err.message)}</p></div>`; }
  };
  await load();
  onTimer(load, 5000);
}
