// Investigation tab: the root agent's reasoning, every query each agent ran (with its result), the
// workers spawning and reporting, and a board of hypotheses that appear and resolve with their evidence.
import { esc, fmtInt } from "../ui.js";
import { agentColor, clockText } from "../replay.js";


function agentChip(s) {
  return `<span class="agent-chip"><i style="background:${agentColor(s.index)}"></i>${esc(s.role === "root" ? "Root agent" : s.name)}</span>`;
}

function feedItems(tl) {
  const items = [];
  for (const e of tl.events) items.push({ at: e.at, kind: e.kind, e });
  for (const q of tl.queries) items.push({ at: q.end ?? q.start, kind: "query", q });
  items.sort((a, b) => (a.at ?? Infinity) - (b.at ?? Infinity));
  return items;
}

function renderItem(item, tl) {
  const at = item.at ?? tl.duration_s;
  const time = `<span class="feed-time mono">+${clockText(item.at)}</span>`;
  if (item.kind === "query") {
    const q = item.q, s = tl.sessions[q.session];
    const more = q.sql.length > 1 ? `<span class="muted small">+${q.sql.length - 1} more statement${q.sql.length > 2 ? "s" : ""}</span>` : "";
    return `<article class="feed-item query ${q.ok ? "" : "failed"}" data-at="${at}">
      <header>${agentChip(s)}${time}
        <span class="q-meta">${q.ok ? `<span class="ok-dot"></span>ran` : `<span class="bad-dot"></span>failed`}
        ${q.duration_ms != null ? ` · ${fmtInt(q.duration_ms)} ms` : ""}${q.rows != null ? ` · ${fmtInt(q.rows)} row${q.rows === 1 ? "" : "s"}` : ""} · on the warehouse</span></header>
      <pre class="sql"><code class="language-sql"></code></pre>${more}
      ${q.result ? `<details class="result"><summary>Result</summary><pre></pre></details>` : ""}
    </article>`;
  }
  const e = item.e, s = tl.sessions[e.session] || tl.sessions[0];
  if (item.kind === "thought") {
    return `<article class="feed-item thought" data-at="${at}"><header>${agentChip(s)}${time}<span class="muted small">reasoning</span></header>
      <div class="thought-text"></div></article>`;
  }
  const text = { spawn: `started working on its slice`, report: `reported back to the root agent`, notice: `stopped without reporting; the supervisor re-prompts it` }[item.kind];
  return `<div class="feed-item event ${item.kind}" data-at="${at}">${agentChip(s)}<span>${esc(text)}</span>${time}</div>`;
}

function fill(el, items) {
  // Untrusted agent text goes in with textContent; SQL is highlighted once, not on every tick.
  el.querySelectorAll(".feed-item").forEach((node, i) => {
    const item = items[i];
    if (item.kind === "query") {
      const code = node.querySelector("code");
      code.textContent = item.q.sql[0];
      if (window.hljs) window.hljs.highlightElement(code);
      const pre = node.querySelector(".result pre");
      if (pre) pre.textContent = item.q.result + (item.q.truncated ? "\n…" : "");
    } else if (item.kind === "thought") {
      node.querySelector(".thought-text").textContent = item.e.text;
    }
  });
}

function board(tl) {
  if (!tl.hypotheses.length) {
    return `<div class="board-empty">${tl.status === "running"
      ? "Hypotheses appear here when the root agent writes its findings, each tied to the queries that support or rule it out."
      : "This run wrote no findings contract, so there are no hypotheses to show."}</div>`;
  }
  return tl.hypotheses.map((h, i) => `
    <details class="hyp ${h.kind}" data-i="${i}">
      <summary><span class="hyp-state"></span><span class="hyp-title"></span></summary>
      <div class="hyp-body"><p class="hyp-text"></p>
        <div class="field-label">Evidence · ${h.evidence.length} quer${h.evidence.length === 1 ? "y" : "ies"}</div>
        <ul class="hyp-ev">${h.evidence.map(() => `<li><span class="ev-meta"></span><pre class="sql"><code class="language-sql"></code></pre></li>`).join("")}</ul>
      </div>
    </details>`).join("");
}

function fillBoard(el, tl) {
  el.querySelectorAll(".hyp").forEach((node) => {
    const h = tl.hypotheses[Number(node.dataset.i)];
    node.querySelector(".hyp-title").textContent = h.title;
    node.querySelector(".hyp-text").textContent = h.kind === "ruled_out" && h.reason ? `${h.text} — ${h.reason}` : h.text;
    node.querySelectorAll(".hyp-ev li").forEach((li, j) => {
      const ev = h.evidence[j];
      li.querySelector(".ev-meta").textContent = `${ev.session}${ev.rows != null ? ` · ${fmtInt(ev.rows)} rows` : ""}${ev.at != null ? ` · +${clockText(ev.at)}` : ""}`;
      const code = li.querySelector("code"); code.textContent = ev.sql;
      if (window.hljs) window.hljs.highlightElement(code);
    });
  });
}

export function mount(el, tl, replay) {
  const items = feedItems(tl);
  el.innerHTML = `
    <div class="run-grid">
      <section class="feed" id="feed">${items.map((it) => renderItem(it, tl)).join("") || `<div class="board-empty">No activity recorded yet.</div>`}</section>
      <aside class="board"><div class="board-head"><h3>Hypotheses</h3><span class="muted small" id="board-count"></span></div>
        <div class="board-legend"><span><i class="lg testing"></i>Testing</span><span><i class="lg supported"></i>Supported</span><span><i class="lg ruled_out"></i>Ruled out</span></div>
        <div id="board">${board(tl)}</div></aside>
    </div>`;
  const feed = el.querySelector("#feed");
  fill(feed, items);
  fillBoard(el.querySelector("#board"), tl);
  const nodes = [...feed.querySelectorAll(".feed-item")];
  const hyps = [...el.querySelectorAll(".hyp")];
  let lastShown = -1;

  return replay.on((t) => {
    let shown = -1;
    nodes.forEach((n, i) => { const vis = Number(n.dataset.at) <= t + 1e-6; n.hidden = !vis; if (vis) shown = i; });
    if (replay.playing && shown !== lastShown && shown >= 0) nodes[shown].scrollIntoView({ block: "end", behavior: "smooth" });
    lastShown = shown;
    let open = 0, settled = 0;
    hyps.forEach((n) => {
      const h = tl.hypotheses[Number(n.dataset.i)];
      const state = t < h.first_at ? "hidden" : t < h.resolved_at ? "testing" : h.kind;
      n.hidden = state === "hidden";
      n.dataset.state = state;
      n.querySelector(".hyp-state").textContent = { testing: "Testing", supported: "Supported", ruled_out: "Ruled out" }[state] || "";
      if (state === "testing") open++; else if (state !== "hidden") settled++;
    });
    el.querySelector("#board-count").textContent = tl.hypotheses.length ? `${settled} settled · ${open} testing` : "";
  });
}
