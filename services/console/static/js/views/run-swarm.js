// Swarm tab: the root agent and its workers. Counters, the session tree, and swim lanes showing the
// workers investigating in parallel on one timeline. Each agent keeps one color (dataviz dark slots,
// validated) and is always labeled by name, so identity never rests on color alone.
import { esc, fmtCompact, fmtInt } from "../ui.js";
import { agentColor, clockText } from "../replay.js";

const MODEL = (m) => {
  const id = (m || "").split("/").pop();
  return { "mistral-large-4": "Mistral Large 4", "mistral-medium-latest": "Mistral Medium 3.5", "mistral-large-latest": "Mistral Large 3",
           "ministral-8b-latest": "Ministral 8B" }[id] || id;
};

function stateAt(s, tl, t) {
  if (s.start == null) return "unknown";
  if (t < s.start) return "waiting";
  const reported = tl.events.some((e) => e.kind === "report" && e.session === s.index && e.at <= t);
  if (s.role === "child" && reported) return "reported";
  if (s.end != null && t >= s.end) return s.role === "root" ? "finished" : "exited";
  return "working";
}
const STATE_LABEL = { waiting: "Not started", working: "Working", reported: "Reported", exited: "Exited", finished: "Finished", unknown: "–" };

function tree(tl) {
  const root = tl.sessions[0], kids = tl.sessions.slice(1);
  const W = 1100, rowH = 92, H = Math.max(rowH * Math.max(kids.length, 1) + 16, 140);
  const rootY = H / 2, kidX = 640;
  const node = (s, x, y, w) => `
    <g class="node" data-s="${s.index}" transform="translate(${x},${y})">
      <rect class="node-box" x="0" y="-34" width="${w}" height="68" rx="12"></rect>
      <rect x="0" y="-34" width="5" height="68" rx="2" fill="${agentColor(s.index)}"></rect>
      <circle class="pulse" cx="${w - 22}" cy="-14" r="5" fill="${agentColor(s.index)}"></circle>
      <text x="20" y="-10" class="node-name"></text>
      <text x="20" y="12" class="node-model"></text>
      <text x="20" y="28" class="node-stat"></text>
    </g>`;
  const links = kids.map((s, i) => {
    const y = 8 + rowH * i + rowH / 2;
    return `<path class="link" data-s="${s.index}" d="M ${290} ${rootY} C ${460} ${rootY}, ${470} ${y}, ${kidX} ${y}"></path>`;
  }).join("");
  return `<svg class="tree" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMinYMid meet" style="height:${H}px">
    ${links}${node(root, 20, rootY, 270)}${kids.map((s, i) => node(s, kidX, 8 + rowH * i + rowH / 2, 300)).join("")}</svg>`;
}

function lanes(tl, width) {
  const left = 190, right = 24, laneH = 40, top = 30;
  const H = top + laneH * tl.sessions.length + 26;
  const x = d3.scaleLinear().domain([0, Math.max(tl.duration_s || 1, 1)]).range([left, width - right]);
  const svg = d3.create("svg").attr("class", "lanes").attr("viewBox", `0 0 ${width} ${H}`).attr("width", "100%").attr("height", H);
  const ticks = x.ticks(8);
  svg.append("g").selectAll("line").data(ticks).join("line").attr("class", "grid")
    .attr("x1", (d) => x(d)).attr("x2", (d) => x(d)).attr("y1", top - 8).attr("y2", H - 22);
  svg.append("g").selectAll("text").data(ticks).join("text").attr("class", "tick")
    .attr("x", (d) => x(d)).attr("y", H - 6).attr("text-anchor", "middle").text((d) => clockText(d));
  tl.sessions.forEach((s, i) => {
    const y = top + i * laneH;
    const g = svg.append("g").attr("class", "lane");
    g.append("rect").attr("class", "lane-bg").attr("x", left).attr("y", y + 4).attr("width", width - left - right).attr("height", laneH - 8).attr("rx", 6);
    g.append("rect").attr("x", 0).attr("y", y + 12).attr("width", 10).attr("height", 16).attr("rx", 3).attr("fill", agentColor(i));
    g.append("text").attr("class", "lane-name").attr("x", 18).attr("y", y + 20).text(s.role === "root" ? "Root agent" : s.name);
    g.append("text").attr("class", "lane-model").attr("x", 18).attr("y", y + 34).text(MODEL(s.model));
    if (s.start != null && s.end != null) {
      g.append("rect").attr("class", "span").attr("data-s", i).attr("x", x(s.start)).attr("y", y + 18).attr("height", 4)
        .attr("width", Math.max(2, x(s.end) - x(s.start))).attr("fill", agentColor(i));
    }
    s.cells.filter((c) => c.start != null).forEach((c) => {
      const end = c.end ?? c.start;
      g.append("rect").attr("class", `cell ${c.ok ? "" : "bad"} ${c.sql ? "q" : ""}`).attr("data-start", c.start).attr("data-end", end)
        .attr("x", x(c.start)).attr("y", y + 10).attr("height", laneH - 20).attr("rx", 3)
        .attr("width", Math.max(3, x(end) - x(c.start))).attr("fill", c.ok ? agentColor(i) : "#f0555a")
        .append("title").text(`${s.role === "root" ? "Root agent" : s.name} · +${clockText(c.start)} · ${c.duration_ms != null ? fmtInt(c.duration_ms) + " ms" : ""}${c.sql ? " · SQL" : ""}${c.ok ? "" : " · failed"}`);
    });
    tl.events.filter((e) => e.kind === "report" && e.session === i).forEach((e) => {
      g.append("path").attr("class", "report").attr("data-at", e.at).attr("transform", `translate(${x(e.at)},${y + laneH / 2})`)
        .attr("d", "M0,-7 L7,0 L0,7 L-7,0 Z").append("title").text(`${s.name} reported back · +${clockText(e.at)}`);
    });
  });
  svg.append("line").attr("class", "cursor").attr("y1", top - 10).attr("y2", H - 22);
  return { node: svg.node(), x };
}

export function mount(el, tl, replay) {
  el.innerHTML = `
    <section class="swarm-counters">
      <div><div class="v" id="sw-active">0</div><div class="l">Agents working</div></div>
      <div><div class="v" id="sw-queries">0</div><div class="l">Queries run</div></div>
      <div><div class="v" id="sw-reported">0</div><div class="l">Workers reported</div></div>
      <div><div class="v" id="sw-time">0:00</div><div class="l">Elapsed</div></div>
    </section>
    <section class="panel swarm-tree"><div class="chart-head"><div><h2 class="chart-title">The swarm</h2>
      <p class="chart-sub">The root agent plans and delegates; each worker investigates its own slice of the data in parallel and reports back.</p></div></div>
      <div id="tree">${tree(tl)}</div></section>
    <section class="panel swarm-lanes"><div class="chart-head"><div><h2 class="chart-title">Timeline</h2>
      <p class="chart-sub">Each block is one step an agent ran; diamonds mark a worker reporting back. Times are from the start of the run.</p></div></div>
      <div id="lanes"></div></section>`;
  const lanesBox = el.querySelector("#lanes");
  const { node, x } = lanes(tl, Math.max(900, lanesBox.clientWidth || 1500));
  lanesBox.append(node);
  const svg = d3.select(node);
  const treeSvg = el.querySelector(".tree");
  tl.sessions.forEach((s) => {
    const g = treeSvg.querySelector(`.node[data-s="${s.index}"]`);
    g.querySelector(".node-name").textContent = s.role === "root" ? "Root agent" : s.name;
    g.querySelector(".node-model").textContent = MODEL(s.model);
  });
  const qs = tl.queries.filter((q) => q.end != null || q.start != null);

  return replay.on((t) => {
    svg.select(".cursor").attr("x1", x(t)).attr("x2", x(t));
    svg.selectAll(".cell").attr("opacity", function () { return Number(this.dataset.start) <= t ? 1 : 0.12; });
    svg.selectAll(".report").attr("opacity", function () { return Number(this.dataset.at) <= t ? 1 : 0; });
    let active = 0;
    tl.sessions.forEach((s) => {
      const st = stateAt(s, tl, t);
      if (st === "working") active++;
      const g = treeSvg.querySelector(`.node[data-s="${s.index}"]`);
      g.dataset.state = st;
      const done = s.cells.filter((c) => c.sql && c.start != null && c.start <= t).length;
      g.querySelector(".node-stat").textContent = `${STATE_LABEL[st]} · ${done} quer${done === 1 ? "y" : "ies"}${s.tokens && t >= (s.end ?? Infinity) ? ` · ${fmtCompact(s.tokens)} tokens` : ""}`;
      const link = treeSvg.querySelector(`.link[data-s="${s.index}"]`);
      if (link) link.dataset.state = st;
    });
    el.querySelector("#sw-active").textContent = active;
    el.querySelector("#sw-queries").textContent = fmtInt(qs.filter((q) => (q.end ?? q.start) <= t).length);
    el.querySelector("#sw-reported").textContent = `${tl.events.filter((e) => e.kind === "report" && e.at <= t).map((e) => e.session).filter((v, i, a) => a.indexOf(v) === i).length} / ${tl.sessions.length - 1}`;
    el.querySelector("#sw-time").textContent = clockText(t);
  });
}
