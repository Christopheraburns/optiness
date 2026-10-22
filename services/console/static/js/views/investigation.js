// One investigation: header and stats, the bridge's live progress while it runs, and the
// Investigation (feed + hypotheses) and Swarm tabs on one replay clock. Resolution comes in P3.
// Live polling repaints only the header and live panel, so a replay in progress is never reset.
import { api, esc, elapsed, fmtCompact, fmtDuration, fmtInt, fmtWhen, icon, modelTags, post, statusPill } from "../ui.js";
import { Replay, controls } from "../replay.js";
import * as feed from "./run-feed.js";
import * as swarm from "./run-swarm.js";

const TABS = [["queries", "Investigation"], ["swarm", "Swarm"], ["resolution", "Resolution"]];
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
  // Shown while the bridge reports the run active; a finished run's story is in the tabs.
  if (!live || !live.available || !ACTIVE.has(live.status)) return "";
  const p = live.progress || {};
  const events = (p.recent_events || []).slice(-12).reverse();
  return `
    <section class="live fade-in">
      <div class="live-head">
        <div class="live-title"><span class="status ${BRIDGE_CLASS[live.status] || "ended"}">${esc(BRIDGE_LABEL[live.status] || live.status)}</span>
          <span>Live from the bridge</span>
          ${live.queue_position ? `<span class="muted">· position ${live.queue_position} in the queue</span>` : ""}
          ${live.elapsed_s ? `<span class="muted">· ${esc(fmtDuration(live.elapsed_s))}</span>` : ""}</div>
        <button class="btn danger" id="stop">Stop investigation</button>
      </div>
      ${live.error ? `<div class="live-problem">${esc(live.error)}</div>` : ""}
      <div class="live-counters">
        <div><div class="v">${fmtInt(p.subagents_spawned)}</div><div class="l">Workers spawned</div></div>
        <div><div class="v">${fmtInt(p.subagents_reported)}</div><div class="l">Reported</div></div>
        <div><div class="v ${p.subagents_reprompted ? "warn" : ""}">${fmtInt(p.subagents_reprompted)}</div><div class="l">Re-prompted</div></div>
        <div><div class="v ${p.subagents_failed ? "bad" : ""}">${fmtInt(p.subagents_failed)}</div><div class="l">Failed</div></div>
        <div><div class="v">${fmtInt(p.root_tool_calls)}</div><div class="l">Root tool calls</div></div>
      </div>
      <div class="live-body">
        <div><div class="field-label">Root agent's latest message</div>
          <div class="live-msg">${p.latest_root_message ? esc(p.latest_root_message) : `<span class="muted">Nothing yet.</span>`}</div></div>
        <div><div class="field-label">Recent events</div>
          <ol class="events">${events.length ? events.map((e) => `
            <li><span class="t mono">${esc(new Date(e.at).toLocaleTimeString("en-US", { hour12: false }))}</span>
              <span class="k">${esc(e.kind.replaceAll("_", " "))}</span><span class="s mono">${esc(e.session || "")}</span>
              <span class="d">${esc(e.detail)}</span></li>`).join("") : `<li class="muted">No events yet.</li>`}</ol></div>
      </div>
    </section>`;
}

function outcomeNote(live) {
  if (!live || !live.available || ACTIVE.has(live.status) || !["failed", "timeout", "cancelled"].includes(live.status)) return "";
  return `<div class="live-problem">${esc(BRIDGE_LABEL[live.status])}: ${esc(live.error || live.outcome || "")}</div>`;
}

export async function render(view, { params, onTimer }) {
  const runId = params[0];
  let run = null, live = null, tl = null, mounted = null, offControls = null, tlSig = "";
  const replay = new Replay();

  view.innerHTML = `<div id="head"></div><div id="live"></div>
    <nav class="tabs">${TABS.map(([key, label]) => `<button class="tab ${tab === key ? "on" : ""}" data-tab="${key}">${label}</button>`).join("")}</nav>
    <div id="replay-bar"></div><section id="tab-body"></section>`;
  const $ = (sel) => view.querySelector(sel);

  const mountTab = () => {
    if (mounted) mounted(); mounted = null;
    if (offControls) offControls(); offControls = null;
    $("#replay-bar").innerHTML = "";
    const body = $("#tab-body");
    if (tab === "resolution") {
      body.innerHTML = `<div class="empty fade-in"><h3>Resolution</h3><p>What the agent concluded, the evidence behind it, and what it ruled out.</p><p class="muted">Arrives in the next build phase.</p></div>`;
      return;
    }
    if (!tl) { body.innerHTML = `<div class="muted">Loading the run's timeline…</div>`; return; }
    if (!tl.replayable && !tl.queries.length && !tl.events.length) {
      body.innerHTML = `<div class="empty"><h3>No activity recorded yet</h3><p>The run's transcripts will appear here as its agents work.</p></div>`;
      return;
    }
    offControls = controls(replay, $("#replay-bar"));
    mounted = (tab === "swarm" ? swarm : feed).mount(body, tl, replay);
    replay.emit();
  };

  $(".tabs").addEventListener("click", (e) => {
    const button = e.target.closest("[data-tab]");
    if (!button || button.dataset.tab === tab) return;
    tab = button.dataset.tab;
    view.querySelectorAll(".tab").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
    mountTab();
  });

  const paintTop = () => {
    $("#head").innerHTML = header(run, live);
    $("#live").innerHTML = livePanel(live) + outcomeNote(live);
    $("#stop")?.addEventListener("click", async (e) => {
      if (!confirm("Stop this investigation? Its agents are stopped and it ends without findings.")) return;
      e.target.disabled = true; e.target.textContent = "Stopping…";
      try { await post(`/api/investigations/${encodeURIComponent(runId)}/cancel`); await load(); }
      catch (err) { alert(err.message); e.target.disabled = false; e.target.textContent = "Stop investigation"; }
    });
  };

  const isLive = () => (live && ACTIVE.has(live.status)) || (run && run.status === "running");

  const load = async () => {
    const [r, l, t] = await Promise.allSettled([
      api(`/api/investigations/${encodeURIComponent(runId)}`),
      api(`/api/live/${encodeURIComponent(runId)}`),
      api(`/api/investigations/${encodeURIComponent(runId)}/timeline`),
    ]);
    run = r.status === "fulfilled" ? r.value : null;
    live = l.status === "fulfilled" ? l.value : null;
    if (!run && !(live && live.available)) {
      view.innerHTML = `<a class="crumb" href="#/investigations">${icon.back}All investigations</a>
        <div class="empty"><h3>Could not load ${esc(runId)}</h3><p class="error">${esc(r.reason?.message || "not found")}</p></div>`;
      return;
    }
    paintTop();
    if (t.status === "fulfilled") {
      const sig = `${t.value.queries.length}:${t.value.events.length}:${t.value.hypotheses.length}:${t.value.duration_s}`;
      if (sig !== tlSig) {  // remount only when the run has new activity
        tlSig = sig; tl = t.value;
        replay.load(tl, isLive());
        mountTab();
      }
    } else if (!tl) mountTab();
  };

  await load();
  onTimer(() => { if (isLive() || !run) load(); }, 4000);
  window.addEventListener("hashchange", () => replay.dispose(), { once: true });
}
