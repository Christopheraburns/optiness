// One investigation: header and stats, the bridge's live progress while it runs, and the
// Investigation / Swarm / Resolution tabs (built in phases P2-P3).
import { api, esc, elapsed, fmtCompact, fmtDuration, fmtInt, fmtWhen, icon, modelTags, post, statusPill } from "../ui.js";

const TABS = [
  ["queries", "Investigation", "The root agent's plan and every query the agents ran, with results, as evidence arrived."],
  ["swarm", "Swarm", "The root agent and its workers investigating in parallel, on a shared timeline."],
  ["resolution", "Resolution", "What the agent concluded, the evidence behind it, and what it ruled out."],
];
const ACTIVE = new Set(["queued", "running"]);
const BRIDGE_LABEL = { queued: "Queued", running: "Running", done: "Done", failed: "Failed", timeout: "Timed out", cancelled: "Stopped" };
const BRIDGE_CLASS = { queued: "running", running: "running", done: "done", failed: "stalled", timeout: "stalled", cancelled: "ended" };

let tab = "queries";

function header(run, live) {
  const question = run?.question || live?.question || "";
  const status = run ? statusPill(run.status) : `<span class="status running">Starting</span>`;
  return `
    <a class="crumb" href="#/investigations">${icon.back}All investigations</a>
    <div class="detail-top">${status}<span>${esc(fmtWhen(run?.started || live?.created_at))}</span><span class="mono">${esc(run?.run_id || live?.id || "")}</span></div>
    <h1 class="detail-q">${esc(question)}</h1>
    ${run ? `<div style="margin-top:16px">${modelTags(run)}</div>` : ""}
    ${run ? `<section class="strip">
      <div><div class="l">Duration</div><div class="v">${esc(fmtDuration(elapsed(run)))}</div></div>
      <div><div class="l">Agents</div><div class="v">${fmtInt(run.agents)}<small>${fmtInt(run.children)} workers</small></div></div>
      <div><div class="l">SQL queries</div><div class="v">${fmtInt(run.queries)}</div></div>
      <div><div class="l">Tokens</div><div class="v">${fmtCompact(run.tokens)}</div></div>
      <div><div class="l">Findings</div><div class="v">${run.findings ? fmtInt(run.findings) : "–"}</div></div>
      <div><div class="l">Ruled out</div><div class="v">${run.ruled_out ? fmtInt(run.ruled_out) : "–"}</div></div>
    </section>` : `<div style="height:24px"></div>`}`;
}

function livePanel(live) {
  if (!live || !live.available) return "";
  const p = live.progress || {};
  const active = ACTIVE.has(live.status);
  const events = (p.recent_events || []).slice(-12).reverse();
  const problem = live.error || (["failed", "timeout"].includes(live.status) ? live.outcome : "");
  return `
    <section class="live fade-in">
      <div class="live-head">
        <div class="live-title"><span class="status ${BRIDGE_CLASS[live.status] || "ended"}">${esc(BRIDGE_LABEL[live.status] || live.status)}</span>
          <span>Live from the bridge</span>
          ${live.queue_position ? `<span class="muted">· position ${live.queue_position} in the queue</span>` : ""}
          ${live.elapsed_s ? `<span class="muted">· ${esc(fmtDuration(live.elapsed_s))}</span>` : ""}</div>
        ${active ? `<button class="btn danger" id="stop">Stop investigation</button>` : ""}
      </div>
      ${problem ? `<div class="live-problem">${esc(problem)}</div>` : ""}
      <div class="live-counters">
        <div><div class="v">${fmtInt(p.subagents_spawned)}</div><div class="l">Workers spawned</div></div>
        <div><div class="v">${fmtInt(p.subagents_reported)}</div><div class="l">Reported</div></div>
        <div><div class="v ${p.subagents_reprompted ? "warn" : ""}">${fmtInt(p.subagents_reprompted)}</div><div class="l">Re-prompted</div></div>
        <div><div class="v ${p.subagents_failed ? "bad" : ""}">${fmtInt(p.subagents_failed)}</div><div class="l">Failed</div></div>
        <div><div class="v">${fmtInt(p.root_tool_calls)}</div><div class="l">Root tool calls</div></div>
      </div>
      <div class="live-body">
        <div>
          <div class="field-label">Root agent's latest message</div>
          <div class="live-msg">${p.latest_root_message ? esc(p.latest_root_message) : `<span class="muted">Nothing yet.</span>`}</div>
        </div>
        <div>
          <div class="field-label">Recent events</div>
          <ol class="events">${events.length ? events.map((e) => `
            <li><span class="t mono">${esc(new Date(e.at).toLocaleTimeString("en-US", { hour12: false }))}</span>
              <span class="k">${esc(e.kind.replaceAll("_", " "))}</span><span class="s mono">${esc(e.session || "")}</span>
              <span class="d">${esc(e.detail)}</span></li>`).join("") : `<li class="muted">No events yet.</li>`}</ol>
        </div>
      </div>
    </section>`;
}

function body() {
  const [, label, blurb] = TABS.find(([key]) => key === tab);
  return `<div class="empty fade-in"><h3>${esc(label)}</h3><p>${esc(blurb)}</p><p class="muted">Arrives in the next build phase.</p></div>`;
}

export async function render(view, { params, onTimer }) {
  const runId = params[0];
  let run = null, live = null;

  const paint = () => {
    const keep = view.querySelector(".events")?.scrollTop;
    view.innerHTML = `${header(run, live)}${livePanel(live)}
      <nav class="tabs">${TABS.map(([key, label]) => `<button class="tab ${tab === key ? "on" : ""}" data-tab="${key}">${label}</button>`).join("")}</nav>
      <section id="tab-body">${body()}</section>`;
    if (keep) view.querySelector(".events").scrollTop = keep;
    view.querySelector(".tabs").addEventListener("click", (e) => {
      const button = e.target.closest("[data-tab]");
      if (button) { tab = button.dataset.tab; paint(); }
    });
    view.querySelector("#stop")?.addEventListener("click", async (e) => {
      if (!confirm("Stop this investigation? Its agents are stopped and it ends without findings.")) return;
      e.target.disabled = true; e.target.textContent = "Stopping…";
      try { await post(`/api/investigations/${encodeURIComponent(runId)}/cancel`); await load(); }
      catch (err) { alert(err.message); e.target.disabled = false; e.target.textContent = "Stop investigation"; }
    });
  };

  const load = async () => {
    const [r, l] = await Promise.allSettled([
      api(`/api/investigations/${encodeURIComponent(runId)}`),
      api(`/api/live/${encodeURIComponent(runId)}`),
    ]);
    run = r.status === "fulfilled" ? r.value : null;
    live = l.status === "fulfilled" ? l.value : null;
    if (!run && !(live && live.available)) {
      view.innerHTML = `<a class="crumb" href="#/investigations">${icon.back}All investigations</a>
        <div class="empty"><h3>Could not load ${esc(runId)}</h3><p class="error">${esc(r.reason?.message || "not found")}</p></div>`;
      return;
    }
    paint();
  };

  await load();
  // Refresh while the bridge says it is active, or while the run's files are still appearing.
  onTimer(() => { if ((live && ACTIVE.has(live.status)) || (run && run.status === "running") || !run) load(); }, 4000);
}
