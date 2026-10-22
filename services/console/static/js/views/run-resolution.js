// Resolution tab: what the agent concluded and the evidence behind it, what it ruled out, the run's
// numbers end to end, and its full written report. Everything shown is the agent's own output;
// the console never adds an answer of its own.
import { api, esc, fmtCompact, fmtDuration, fmtInt, markdown } from "../ui.js";
import { agentColor, clockText } from "../replay.js";

const CONF = { high: "High confidence", medium: "Medium confidence", low: "Low confidence" };

function sessionIndex(tl, name) {
  if (!name || name === "root") return 0;
  const s = tl.sessions.find((x) => x.name === name);
  return s ? s.index : 0;
}

function evidenceList(h, tl) {
  return `<ol class="res-ev">${h.evidence.map((e, j) => `
    <li data-j="${j}">
      <div class="res-ev-meta"><span class="agent-chip"><i style="background:${agentColor(sessionIndex(tl, e.session))}"></i><span class="ev-who"></span></span>
        <span class="muted small">${e.rows != null ? `${fmtInt(e.rows)} row${e.rows === 1 ? "" : "s"}` : ""}${e.elapsed_ms != null ? ` · ${fmtInt(e.elapsed_ms)} ms` : ""}${e.at != null ? ` · ran at +${clockText(e.at)}` : ""}</span></div>
      <pre class="sql"><code class="language-sql"></code></pre>
    </li>`).join("")}</ol>`;
}

function card(h, i, tl) {
  const agents = [...new Set(h.evidence.map((e) => e.session || "root"))];
  return `
    <article class="res-card ${h.kind}" data-i="${i}">
      <div class="res-card-head">
        <span class="res-n">${h.kind === "supported" ? i + 1 : "✕"}</span>
        <div class="res-main">
          <h3 class="res-title"></h3>
          <p class="res-text"></p>
          <div class="res-tags">
            ${h.kind === "supported" && h.confidence ? `<span class="conf ${esc(h.confidence)}">${esc(CONF[h.confidence] || h.confidence)}</span>` : ""}
            <span class="tag">${h.evidence.length} SQL statement${h.evidence.length === 1 ? "" : "s"}</span>
            <span class="tag">${agents.length} agent${agents.length === 1 ? "" : "s"}</span>
          </div>
        </div>
      </div>
      <details class="res-evidence"><summary>Evidence</summary>${evidenceList(h, tl)}</details>
    </article>`;
}

function strip(run, tl, scale) {
  const models = [run.root_model, ...Object.keys(run.child_models || {})].filter(Boolean);
  const step = (k, v, sub) => `<div class="flow-step"><div class="flow-k">${k}</div><div class="flow-v">${v}</div>${sub ? `<div class="flow-sub">${sub}</div>` : ""}</div>`;
  return `
    <section class="panel flow">
      <h2 class="chart-title">How it got there</h2>
      <div class="flow-row">
        ${step("Question", "Asked in plain language", "to the Prime Agent harness")}
        <span class="flow-arrow">→</span>
        ${step("Harness", "Prime Agent", "on Cloudera AI Workbench")}
        <span class="flow-arrow">→</span>
        ${step("Agents", `${fmtInt(run.agents)} agents`, esc(models.join(" · ")))}
        <span class="flow-arrow">→</span>
        ${step("Evidence", `${fmtInt(run.queries)} SQL queries`, scale ? `over ${fmtCompact(scale)} transactions in the warehouse` : "on the warehouse")}
        <span class="flow-arrow">→</span>
        ${step("Outcome", `${fmtInt(run.findings)} findings · ${fmtInt(run.ruled_out)} ruled out`, `${esc(fmtDuration(tl.duration_s ?? run.elapsed_s))} · ${fmtCompact(run.tokens)} tokens`)}
      </div>
    </section>`;
}

export async function mount(el, run, tl) {
  const supported = tl.hypotheses.filter((h) => h.kind === "supported");
  const ruled = tl.hypotheses.filter((h) => h.kind === "ruled_out");
  if (!supported.length && !ruled.length && !run.report_md) {
    el.innerHTML = `<div class="empty"><h3>No findings yet</h3><p>${run.status === "running"
      ? "The root agent writes its findings when every worker has reported. They appear here as soon as it does."
      : "This run ended without writing findings."}</p></div>`;
    return () => {};
  }
  const [system, overview] = await Promise.allSettled([api("/api/system"), api("/api/overview")]);
  const closing = system.status === "fulfilled" ? system.value.closing_message : null;
  const scale = overview.status === "fulfilled" && overview.value.available ? overview.value.total_attempts : null;

  el.innerHTML = `
    ${strip(run, tl, scale)}
    ${supported.length ? `<section class="res-section"><div class="res-head"><h2>What the agent concluded</h2>
      <span class="muted">Each conclusion is backed by the SQL statements it cites; expand one to see them.</span></div>
      ${supported.map((h, i) => card(h, i, tl)).join("")}</section>` : ""}
    ${ruled.length ? `<section class="res-section"><div class="res-head"><h2>What it ruled out</h2>
      <span class="muted">Alternative explanations the agent tested against the data and rejected.</span></div>
      ${ruled.map((h, i) => card(h, i, tl)).join("")}</section>` : ""}
    ${run.report_md ? `<details class="panel report"><summary>The agent's full report</summary><div class="md" id="report"></div></details>` : ""}
    ${closing ? `<section class="closing"><p></p></section>` : ""}`;

  // Agent-written text goes in with textContent or through the escaping renderer.
  const all = [...supported, ...ruled];
  el.querySelectorAll(".res-card").forEach((node, k) => {
    const h = all[k];
    // Conclusions: the claim's first sentence is the title and the rest is the body, so nothing
    // reads twice. Ruled out: the explanation is the title and the agent's reason the body.
    const split = h.text.match(/^(.+?\.)\s+([\s\S]+)$/);
    const title = h.kind === "supported" ? (split ? split[1] : h.text) : h.text;
    const body = h.kind === "supported" ? (split ? split[2] : "") : (h.reason || "");
    node.querySelector(".res-title").textContent = title.replace(/\.$/, "");
    const text = node.querySelector(".res-text");
    text.textContent = body;
    text.hidden = !body;
    node.querySelectorAll(".res-ev li").forEach((li) => {
      const e = h.evidence[Number(li.dataset.j)];
      li.querySelector(".ev-who").textContent = e.session === "root" || !e.session ? "Root agent" : e.session;
      const code = li.querySelector("code"); code.textContent = e.sql;
      if (window.hljs) window.hljs.highlightElement(code);
    });
  });
  const report = el.querySelector("#report");
  if (report) report.innerHTML = markdown(run.report_md);
  if (closing) el.querySelector(".closing p").textContent = closing;
  return () => {};
}
