// One investigation: header and stats now; the query feed, swarm and resolution come in phases P2-P3.
import { api, esc, elapsed, fmtCompact, fmtDuration, fmtInt, fmtWhen, icon, modelTags, statusPill } from "../ui.js";

const TABS = [
  ["queries", "Investigation", "The root agent's plan and every query the agents ran, with results, as evidence arrived."],
  ["swarm", "Swarm", "The root agent and its workers investigating in parallel, on a shared timeline."],
  ["resolution", "Resolution", "What the agent concluded, the evidence behind it, and what it ruled out."],
];

let tab = "queries";

function header(run) {
  return `
    <a class="crumb" href="#/investigations">${icon.back}All investigations</a>
    <div class="detail-top">${statusPill(run.status)}<span>${esc(fmtWhen(run.started))}</span><span class="mono">${esc(run.run_id)}</span></div>
    <h1 class="detail-q">${esc(run.question || run.run_id)}</h1>
    <div style="margin-top:16px">${modelTags(run)}</div>
    <section class="strip">
      <div><div class="l">Duration</div><div class="v">${esc(fmtDuration(elapsed(run)))}</div></div>
      <div><div class="l">Agents</div><div class="v">${fmtInt(run.agents)}<small>${fmtInt(run.children)} workers</small></div></div>
      <div><div class="l">SQL queries</div><div class="v">${fmtInt(run.queries)}</div></div>
      <div><div class="l">Tokens</div><div class="v">${fmtCompact(run.tokens)}</div></div>
      <div><div class="l">Findings</div><div class="v">${run.findings ? fmtInt(run.findings) : "–"}</div></div>
      <div><div class="l">Ruled out</div><div class="v">${run.ruled_out ? fmtInt(run.ruled_out) : "–"}</div></div>
    </section>`;
}

function body() {
  const [, label, blurb] = TABS.find(([key]) => key === tab);
  return `<div class="empty fade-in"><h3>${esc(label)}</h3><p>${esc(blurb)}</p><p class="muted">Arrives in the next build phase.</p></div>`;
}

export async function render(view, { params, onTimer }) {
  const runId = params[0];
  const paint = (run) => {
    view.innerHTML = `${header(run)}
      <nav class="tabs">${TABS.map(([key, label]) => `<button class="tab ${tab === key ? "on" : ""}" data-tab="${key}">${label}</button>`).join("")}</nav>
      <section id="tab-body">${body()}</section>`;
    view.querySelector(".tabs").addEventListener("click", (e) => {
      const button = e.target.closest("[data-tab]");
      if (button) { tab = button.dataset.tab; paint(run); }
    });
  };
  const load = async () => {
    try { paint(await api(`/api/investigations/${encodeURIComponent(runId)}`)); }
    catch (err) { view.innerHTML = `<a class="crumb" href="#/investigations">${icon.back}All investigations</a><div class="empty"><h3>Could not load ${esc(runId)}</h3><p class="error">${esc(err.message)}</p></div>`; }
  };
  await load();
  onTimer(async () => { /* live runs refresh; finished runs do not change */ }, 0);
}
